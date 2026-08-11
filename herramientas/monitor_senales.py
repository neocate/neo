# ---------------------------------------------------------------
# monitor_senales.py - Evalua EN VIVO las señales de vela de
# mercado/senales.py (impulso/aceleracion/ruptura/rechazo/divergencia RSI)
# cada vez que cierra una vela nueva del TF vigilado, con el contexto de
# niveles vigentes del Grupo A (ver mercado/senales.py). Hermano de
# monitor_niveles.py (SOLO niveles) - hasta 2026-08-11 esto vivia fusionado
# ahi mismo (desde el commit 7a11670, nunca hubo una version separada
# previa). Se separa por el mismo motivo que ya justifico separar
# grabador_libro.py de monitor.py: señales es logica que se ajusta/prueba
# con frecuencia (umbrales, que señales entran en REFINADAS_CONFIRMADAS),
# vigilar niveles es mecanico y estable - un ajuste aqui no deberia
# obligar a reiniciar (y cortar la serie de) el vigilante de niveles.
#
# A diferencia de monitor_niveles.py, NO usa --tf-macro: el filtro de
# contexto de señales usa los niveles vigentes del TF micro SIN recortar al
# rango macro (mismo criterio que valida herramientas/backtest_senales.py -
# "sin --tf-macro en esto, igual que el backtest que lo valido"). Tampoco
# arma ninguna watchlist con tolerancias por-nivel - solo necesita
# _analizar(coin, tf, ...) directo de niveles_soporte.py.
#
# De las 7 señales que mercado/senales.NIVEL_UTIL_GRUPO_A puede renombrar/
# filtrar segun el contexto de nivel, solo las 4 de
# senales.REFINADAS_CONFIRMADAS (edge positivo y consistente en el backtest
# BTC+ETH 2018-2022) se acaban logueando aqui; las 3 de
# REFINADAS_EN_PRUEBAS se descartan (no confirmadas todavia). El resto de
# señales (fuera del Grupo A) no se ven afectadas por esto.
#
# Solo detecta y loguea - NO manda Telegram (2026-08-11, sacado de aqui a
# proposito): eso es responsabilidad de monitor_telegram.py, un proceso
# aparte que seguido de este descubre y sigue TODOS los senales_*.csv que
# haya y decide que mandar - mismo motivo que separo señales de niveles,
# notificar es una tercera responsabilidad distinta de detectar.
#
# Lee (tail) el CSV de grabador_libro.py solo para el imbalance/cvd de
# contexto que se loguea junto a cada señal - el precio/las velas salen de
# herramientas/libro/historico_<COIN>_<TF>_bitget.csv, que mantiene
# descargar_bit.py --feed (proceso independiente). Si ninguno de los dos
# esta corriendo, este monitor los arranca el (ver monitor_comun.py).
#
# Avisos: por consola en vivo, Y a un CSV PROPIO en herramientas/libro/
# (senales_<fecha>_<coin>_<tf>.csv, distinto del avisos_*.csv de
# monitor_niveles.py - dos escritores en el mismo fichero es justo lo que
# se queria evitar al separar).
#
# Uso:
#   python herramientas/monitor_senales.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 4 [--desde-dias 90] [--cada 15] [--velas-cada 60]
#
# Ejemplo:
#   python herramientas/monitor_senales.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4 --desde-dias 90
# ---------------------------------------------------------------

import csv
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.niveles_soporte import _analizar
from herramientas.descargar_bit import _archivo as _archivo_bitget
from herramientas.monitor_comun import (
    DIR_LIBRO, CAMPOS_AVISOS, _flt, _localizar_csv_libro, _tail_csv,
    _ultima_fila_coin, _esperar_historico, _asegurar_grabador_libro,
)
from mercado import senales


def _fmt_fecha_ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _velas_nuevas(ruta, ultimo_ts):
    """Filas del historico (mismo formato que descargar_bit.py: timestamp,
    fecha_utc,open,high,low,close,volumen) con timestamp > 'ultimo_ts'.

    Lee solo la COLA del fichero (igual que _ultimo_timestamp_ms de
    descargar_bit.py) en vez de reparsear el historico completo en cada
    refresco - puede tener decenas de miles de filas. 1MB de cola sobra
    para cualquier '--velas-cada' razonable."""
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 1_000_000))
        cola = f.read()
    filas = []
    for linea in cola.split(b"\n"):
        linea = linea.strip()
        if not linea or linea.startswith(b"timestamp"):
            continue
        partes = linea.decode("utf-8").split(",")
        ts = int(partes[0])
        if ts > ultimo_ts:
            filas.append([ts, float(partes[2]), float(partes[3]), float(partes[4]),
                          float(partes[5]), float(partes[6])])
    filas.sort(key=lambda v: v[0])
    return filas


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return
    coin, tf = args[0], args[1]
    resto = args[2:]

    k = tolerancia_atr = toques_min = None
    desde_dias = None
    confirmacion_velas = 2
    cada = 15.0
    velas_cada = 60.0
    i = 0
    while i < len(resto):
        if resto[i] == "--k":
            i += 1; k = int(resto[i])
        elif resto[i] == "--tolerancia-atr":
            i += 1; tolerancia_atr = float(resto[i])
        elif resto[i] == "--toques-min":
            i += 1; toques_min = int(resto[i])
        elif resto[i] == "--desde-dias":
            i += 1; desde_dias = float(resto[i])
        elif resto[i] == "--confirmacion-velas":
            i += 1; confirmacion_velas = int(resto[i])
        elif resto[i] == "--cada":
            i += 1; cada = float(resto[i])
        elif resto[i] == "--velas-cada":
            i += 1; velas_cada = float(resto[i])
        i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parametros obligatorios: --k, --tolerancia-atr, --toques-min")
        print("(sin defaults a proposito - ver cabecera de niveles_soporte.py)")
        return

    _esperar_historico(coin, {tf}, cada)

    # Sin --tf-macro a proposito (ver cabecera) - mismo criterio que valida
    # herramientas/backtest_senales.py.
    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)

    # Niveles para el filtro de contexto del Grupo A - se usa
    # r["techos"]/r["suelos"] (ya agrupados por ROL EFECTIVO, con el flip
    # aplicado - ver _rol_efectivo en niveles_soporte.py).
    niveles_vigentes = ([(n["precio"], "techo") for n in r["techos"]]
                         + [(n["precio"], "suelo") for n in r["suelos"]])
    tolerancia_nivel = r["tolerancia"]

    # Solo se necesita historico reciente (ver senales.VENTANA_MAXIMA) -
    # r["velas"] trae hasta el limite de descargar_bit.py --feed, pero
    # arrastrar eso en memoria y recortarlo de nuevo en CADA vela nueva
    # durante una sesion larga es trabajo de sobra.
    velas = r["velas"][-senales.VENTANA_MAXIMA:]
    ruta_historico = _archivo_bitget(coin, tf)
    ultimo_ts_vela = velas[-1][0] if velas else None

    os.makedirs(DIR_LIBRO, exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    ruta_log = os.path.join(DIR_LIBRO, f"senales_{fecha}_{coin.upper()}_{tf}.csv")
    nuevo = not os.path.exists(ruta_log)
    log = open(ruta_log, "a", newline="")
    writer = csv.DictWriter(log, fieldnames=CAMPOS_AVISOS)
    if nuevo:
        writer.writeheader()
        log.flush()

    print(f"Señales de vela ({coin.upper()} {tf}) - precio de referencia {r['precio_actual']:.4f}")
    print(f"Log de señales -> {ruta_log}")

    _asegurar_grabador_libro()
    ruta_libro = _localizar_csv_libro(coin)
    while ruta_libro is None:
        print(f"Esperando a que grabador_libro.py genere el CSV de {coin.upper()} en "
              f"{DIR_LIBRO}... Reintento cada {cada:.0f}s...")
        time.sleep(cada)
        ruta_libro = _localizar_csv_libro(coin)
    print(f"Leyendo de {ruta_libro} (solo lineas nuevas desde ahora).")

    offset = os.path.getsize(ruta_libro)
    ultimo_imbalance = ultimo_cvd = None

    # Estado inicial desde la ULTIMA fila ya grabada (mismo motivo que
    # monitor_niveles.py: solo evita que imbalance/cvd empiecen en None
    # hasta el primer tick nuevo, no dispara ningun evento por si mismo).
    fila_inicial = _ultima_fila_coin(ruta_libro, coin)
    if fila_inicial:
        ultimo_imbalance = _flt(fila_inicial.get("imbalance"))
        ultimo_cvd = _flt(fila_inicial.get("cvd"))
        print(f"Estado inicial ({fila_inicial.get('fecha_utc', '?')}): "
              f"imbalance {(ultimo_imbalance or 0.0):+.2f}  cvd {(ultimo_cvd or 0.0):+.4f}")

    ultimo_refresco_velas = time.monotonic()
    print(f"Señales revisadas cada {velas_cada:.0f}s. Ctrl+C para parar.\n")

    try:
        while True:
            filas, offset = _tail_csv(ruta_libro, offset)
            for fila in filas:
                if fila.get("coin") != coin.upper():
                    continue
                imbalance_val = _flt(fila.get("imbalance"))
                if imbalance_val is not None:
                    ultimo_imbalance = imbalance_val
                cvd = _flt(fila.get("cvd"))
                if cvd is not None:
                    ultimo_cvd = cvd

            if ultimo_ts_vela is not None and time.monotonic() - ultimo_refresco_velas >= velas_cada:
                ultimo_refresco_velas = time.monotonic()
                for nueva in _velas_nuevas(ruta_historico, ultimo_ts_vela):
                    velas.append(nueva)
                    del velas[:-senales.VENTANA_MAXIMA]
                    ultimo_ts_vela = nueva[0]
                    activas = senales.detectar(velas, k, niveles_vigentes=niveles_vigentes,
                                                tolerancia_nivel=tolerancia_nivel)
                    for nombre in activas:
                        if nombre in senales.REFINADAS_EN_PRUEBAS:
                            continue
                        fecha_vela = datetime.fromtimestamp(nueva[0] / 1000, timezone.utc)
                        print(f"  >>> SENAL {nombre}  cierre {nueva[4]:.4f}  "
                              f"({fecha_vela:%Y-%m-%d %H:%M} UTC, vela {tf})")
                        writer.writerow({
                            "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                            "fecha_utc": _fmt_fecha_ahora(),
                            "coin": coin.upper(),
                            "evento": "senal",
                            "tipo": nombre,
                            "origen": "vela",
                            "nivel_precio": "",
                            "precio_actual": nueva[4],
                            "imbalance": round(ultimo_imbalance, 4) if ultimo_imbalance is not None else "",
                            "cvd": round(ultimo_cvd, 4) if ultimo_cvd is not None else "",
                        })
                        log.flush()

            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")
    finally:
        log.close()


if __name__ == "__main__":
    main()
