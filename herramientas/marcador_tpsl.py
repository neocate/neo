# ---------------------------------------------------------------
# marcador_tpsl.py - Vigila EN VIVO las señales que ya dispara
# monitor_senales.py y marca si habrian tocado TP o SL antes de agotar el
# tiempo maximo, usando el precio REAL tick a tick de flujo_<COIN>.csv (no
# un retorno a N velas fijas como backtest_senales.py).
#
# 2026-08-12: extraido de fjsl.py, que hacia esto Y la confirmacion de
# señales por nivel vigente en el mismo fichero - se separan por el mismo
# motivo de siempre ("un .py, una obligacion"). La confirmacion por nivel
# vive ahora en validador_niveles.py (esa SI es la prioridad ahora mismo);
# este fichero es preparatorio para una cartera simulada futura, sin
# relacion con la confirmacion de señales - Fran, sobre esto mismo: "TP/SL
# ahora mismo es cazar de noche sin luna a mosquitos con cañones, no
# tenemos datos suficientes... ahora mismo es guardar datos sin
# contrastar, una vez ajustado [validador_niveles.py] pasamos a ello".
#
# TP/SL AHORA se derivan de la tolerancia ATR que ya calcula
# niveles.py (mismo criterio que usan monitor_niveles.py/
# validador_niveles.py para decidir que es un "toque" de nivel) - NO de un
# % fijo, porque un TP/SL fijo no tiene sentido igual en un mercado
# tranquilo que en uno volatil.
#
# DISEÑO FUTURO YA ACORDADO (2026-08-12, para no tener que desmontar esto
# cuando llegue - Fran: "marcador_tpsl.py va a marcar como sl un 3% y como
# tp un 10% de 20 usdt, que lea contrato para comisiones y funding, si
# pierde el 3% la proxima validacion sera 19,4 usdt - comisiones, a la
# inversa tambien"): cuando se implemente la cartera simulada de verdad,
# esto pasa de TP/SL relativo (ATR) a TP/SL en % fijo (3%/10%) sobre un
# NOTIONAL en USDT (20 USDT de partida), leyendo el contrato real (ccxt,
# comisiones + funding rate) para descontarlos del resultado, y el
# NOTIONAL de la siguiente operacion sale del saldo ya compuesto (gana o
# pierde) menos comisiones - NO de un notional fijo cada vez. Esto todavia
# NO esta implementado - de momento solo se guardan datos (entrada/salida
# en precio) sin aplicar nada de eso, ver cabecera de CAMPOS_TPSL.
#
# El tiempo maximo de espera por operacion sale de --horizontes (el mismo
# flag que usa backtest_senales.py) convertido a minutos segun el TF - se
# reusa el concepto en vez de inventar un --tiempo-max nuevo.
#
# Depende de que YA esten corriendo monitor_niveles.py Y monitor_senales.py
# <coin> <tf>, y grabador_libro.py para 'coin' (2026-08-12, cadena en
# cascada pedida por Fran - ver monitor_comun.py) - si falta alguno, avisa
# y PARA la ejecucion en vez de esperar o arrancarlo el mismo.
# grabador_libro.py se comprueba aparte (no basta con que monitor_niveles.py/
# monitor_senales.py esten vivos) porque este proceso lee flujo_<coin>.csv
# directamente para el TP/SL tick a tick, no solo a traves de ellos. No
# hace falta comprobar descargar_bit.py --feed por separado: si
# monitor_senales.py esta vivo, ya lo comprobo el al arrancar.
#
# Uso:
#   python herramientas/marcador_tpsl.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 4
#                                         [--desde-dias N] [--confirmacion-velas 2]
#                                         [--tp-mult 1.5] [--sl-mult 1.0] [--horizontes 5,15,30]
#                                         [--refresco-tolerancia-horas 6] [--resumen-cada 1800] [--cada 15]
#
#   python herramientas/marcador_tpsl.py --consultar <coin> <tf>
#     Modo puntual: lee tpsl_<COIN>_<TF>.csv ya grabado por el proceso en
#     vivo e imprime el marcador acumulado, sin arrancar ningun bucle.
#
# Ejemplo:
#   python herramientas/marcador_tpsl.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4
#   python herramientas/marcador_tpsl.py --consultar eth 1h
# ---------------------------------------------------------------

import csv
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.niveles import _analizar
from herramientas.backtest_senales import DIRECCION_ESPERADA, _tf_minutos
from herramientas.monitor_comun import (
    DIR_LIBRO, CAMPOS_AVISOS, _flt, _localizar_csv_libro, _tail_csv,
    _proceso_corriendo, _requerir_grabador_libro,
)

CAMPOS_TPSL = [
    "ts_entrada_ms", "fecha_entrada_utc", "coin", "tf", "senal", "direccion",
    "precio_entrada", "tp", "sl", "resultado", "ts_resuelto_ms",
    "fecha_resuelto_utc", "precio_resuelto", "retorno_pct", "pid",
]


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_fecha_ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _refrescar_tolerancia(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas):
    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    return r["tolerancia"]


def _texto_marcador(stats):
    """Texto de la tabla WIN/LOSS/TIMEOUT, o "" si no hay datos - devuelve
    texto en vez de imprimir (2026-08-12) para que telegram_control.py
    pueda mandarlo tal cual, mismo motivo que _texto_confirmaciones en
    validador_niveles.py."""
    if not stats:
        return ""
    lineas = [f"{'señal':<28}{'WIN':>6}{'LOSS':>6}{'TIMEOUT':>9}{'win rate%':>12}"]
    for nombre in sorted(stats):
        s = stats[nombre]
        resueltas = s["WIN"] + s["LOSS"]
        wr = (s["WIN"] / resueltas * 100) if resueltas else None
        lineas.append(f"{nombre:<28}{s['WIN']:>6}{s['LOSS']:>6}{s['TIMEOUT']:>9}"
                      + (f"{wr:>11.1f}%" if wr is not None else f"{'--':>12}"))
    return "\n".join(lineas)


def _resolver(p, ts, precio, resultado, stats, writer, log):
    retorno_pct = (precio - p["precio_entrada"]) / p["precio_entrada"] * p["direccion"] * 100
    s = stats.setdefault(p["senal"], {"WIN": 0, "LOSS": 0, "TIMEOUT": 0})
    s[resultado] += 1
    print(f"  >>> {resultado} {p['senal']} entrada {p['precio_entrada']:.4f} -> "
          f"{precio:.4f} ({retorno_pct:+.3f}%) en {(ts - p['ts_entrada']) / 60000:.0f} min")
    writer.writerow({
        "ts_entrada_ms": p["ts_entrada"], "fecha_entrada_utc": _fmt_fecha(p["ts_entrada"]),
        "coin": p["coin"], "tf": p["tf"], "senal": p["senal"], "direccion": p["direccion"],
        "precio_entrada": p["precio_entrada"], "tp": p["tp"], "sl": p["sl"],
        "resultado": resultado, "ts_resuelto_ms": ts, "fecha_resuelto_utc": _fmt_fecha(ts),
        "precio_resuelto": precio, "retorno_pct": round(retorno_pct, 6), "pid": os.getpid(),
    })
    log.flush()


def _consultar(coin, tf):
    """Texto del marcador acumulado de 'coin'/'tf' - usado tanto por
    --consultar (se imprime tal cual) como por telegram_control.py (se
    manda tal cual)."""
    ruta = os.path.join(DIR_LIBRO, f"tpsl_{coin.upper()}_{tf}.csv")
    if not os.path.exists(ruta):
        return (f"No hay {ruta} todavia - marcador_tpsl.py no ha resuelto ninguna señal "
                f"para {coin.upper()} {tf} (¿esta corriendo el proceso en vivo?).")
    stats = {}
    with open(ruta, newline="") as f:
        for fila in csv.DictReader(f):
            nombre, resultado = fila.get("senal"), fila.get("resultado")
            if not nombre or resultado not in ("WIN", "LOSS", "TIMEOUT"):
                continue
            stats.setdefault(nombre, {"WIN": 0, "LOSS": 0, "TIMEOUT": 0})[resultado] += 1
    texto = _texto_marcador(stats)
    return f"=== Marcador acumulado {coin.upper()} {tf} ===\n{texto}" if texto else \
        f"Sin operaciones resueltas todavia para {coin.upper()} {tf}."


def main():
    args = sys.argv[1:]
    if args and args[0] == "--consultar":
        if len(args) < 3:
            print("Uso: python herramientas/marcador_tpsl.py --consultar <coin> <tf>")
            return
        print(_consultar(args[1], args[2]))
        return
    if len(args) < 2:
        print(__doc__)
        return
    coin, tf = args[0], args[1]
    resto = args[2:]

    k = tolerancia_atr = toques_min = None
    desde_dias = None
    confirmacion_velas = 2
    tp_mult = 1.5
    sl_mult = 1.0
    horizontes = [5, 15, 30]
    refresco_tolerancia_horas = 6.0
    resumen_cada = 1800.0
    cada = 15.0
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
        elif resto[i] == "--tp-mult":
            i += 1; tp_mult = float(resto[i])
        elif resto[i] == "--sl-mult":
            i += 1; sl_mult = float(resto[i])
        elif resto[i] == "--horizontes":
            i += 1; horizontes = [int(x) for x in resto[i].split(",")]
        elif resto[i] == "--refresco-tolerancia-horas":
            i += 1; refresco_tolerancia_horas = float(resto[i])
        elif resto[i] == "--resumen-cada":
            i += 1; resumen_cada = float(resto[i])
        elif resto[i] == "--cada":
            i += 1; cada = float(resto[i])
        i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parametros obligatorios: --k, --tolerancia-atr, --toques-min")
        print("(sin defaults a proposito - ver cabecera de niveles.py)")
        return

    tiempo_max_ms = max(horizontes) * _tf_minutos(tf) * 60_000

    # 2026-08-12: cadena en cascada pedida por Fran - si falta cualquier
    # dependencia (monitor_niveles.py, monitor_senales.py o
    # grabador_libro.py, este ultimo comprobado aparte porque aqui SI se
    # lee flujo_<coin>.csv directamente, no solo a traves de los
    # monitores), avisa y para en vez de esperar (ver monitor_comun.py).
    if not _proceso_corriendo(["monitor_niveles.py", coin.lower(), tf]):
        print(f"ERROR: monitor_niveles.py no esta corriendo para {coin.upper()} {tf} - "
              f"lanzalo primero y reintenta.")
        return
    if not _proceso_corriendo(["monitor_senales.py", coin.lower(), tf]):
        print(f"ERROR: monitor_senales.py no esta corriendo para {coin.upper()} {tf} - "
              f"lanzalo primero y reintenta.")
        return
    if not _requerir_grabador_libro(coin):
        print(f"ERROR: grabador_libro.py no esta corriendo para {coin.upper()} - "
              f"lanzalo primero (python herramientas/grabador_libro.py {coin.lower()}) y reintenta.")
        return

    ruta_senales = os.path.join(DIR_LIBRO, f"senales_{coin.upper()}_{tf}.csv")
    if not os.path.exists(ruta_senales):
        print(f"ERROR: monitor_senales.py esta vivo pero no se encuentra {ruta_senales} todavia "
              f"(¿acaba de arrancar? reintenta en unos segundos).")
        return

    ruta_libro = _localizar_csv_libro(coin)
    if ruta_libro is None:
        print(f"ERROR: grabador_libro.py esta vivo pero no se encuentra su CSV de {coin.upper()} "
              f"en {DIR_LIBRO} todavia (¿acaba de arrancar? reintenta en unos segundos).")
        return

    os.makedirs(DIR_LIBRO, exist_ok=True)
    ruta_tpsl = os.path.join(DIR_LIBRO, f"tpsl_{coin.upper()}_{tf}.csv")
    nuevo = not os.path.exists(ruta_tpsl)
    log = open(ruta_tpsl, "a", newline="")
    writer = csv.DictWriter(log, fieldnames=CAMPOS_TPSL)
    if nuevo:
        writer.writeheader()
        log.flush()

    tolerancia = _refrescar_tolerancia(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    ultimo_refresco_tolerancia = time.monotonic()
    ultimo_resumen = time.monotonic()

    offset_senales = os.path.getsize(ruta_senales)
    offset_flujo = os.path.getsize(ruta_libro)
    pendientes = []
    stats = {}

    print(f"marcador_tpsl.py: {coin.upper()} {tf} - tolerancia {tolerancia:.4f} (TP x{tp_mult} / SL x{sl_mult}), "
          f"tiempo max {tiempo_max_ms / 60000:.0f} min. Leyendo señales nuevas desde ahora.")
    print(f"Marcador -> {ruta_tpsl}. Ctrl+C para parar.")

    try:
        while True:
            if time.monotonic() - ultimo_refresco_tolerancia >= refresco_tolerancia_horas * 3600:
                tolerancia = _refrescar_tolerancia(coin, tf, k, tolerancia_atr, toques_min,
                                                    desde_dias, confirmacion_velas)
                ultimo_refresco_tolerancia = time.monotonic()
                print(f"(tolerancia refrescada: {tolerancia:.4f})")

            filas_senales, offset_senales = _tail_csv(ruta_senales, offset_senales, fieldnames=CAMPOS_AVISOS)
            for fila in filas_senales:
                nombre = fila.get("tipo")
                if nombre not in DIRECCION_ESPERADA:
                    continue  # fila rara/incompleta - se descarta, no se adivina
                ts = int(fila["timestamp_ms"])
                precio = _flt(fila.get("precio_actual"))
                if precio is None:
                    continue
                direccion = DIRECCION_ESPERADA[nombre]
                tp = precio + direccion * tolerancia * tp_mult
                sl = precio - direccion * tolerancia * sl_mult
                pendientes.append({
                    "senal": nombre, "coin": coin.upper(), "tf": tf, "ts_entrada": ts,
                    "precio_entrada": precio, "direccion": direccion, "tp": tp, "sl": sl,
                    "ts_limite": ts + tiempo_max_ms,
                })
                print(f"  + pendiente: {nombre} @ {precio:.4f}  TP {tp:.4f}  SL {sl:.4f}  "
                      f"({_fmt_fecha(ts)})")

            filas_flujo, offset_flujo = _tail_csv(ruta_libro, offset_flujo)
            for fila in filas_flujo:
                if not pendientes:
                    break
                ts = int(fila["timestamp_ms"])
                precio = _flt(fila.get("mid"))
                if precio is None:
                    bid, ask = _flt(fila.get("bid")), _flt(fila.get("ask"))
                    precio = (bid + ask) / 2 if (bid is not None and ask is not None) else None
                if precio is None:
                    continue
                aun_pendientes = []
                for p in pendientes:
                    if ts <= p["ts_entrada"]:
                        aun_pendientes.append(p)
                        continue
                    toco_tp = (precio - p["tp"]) * p["direccion"] >= 0
                    toco_sl = (precio - p["sl"]) * p["direccion"] <= 0
                    if toco_tp:
                        _resolver(p, ts, precio, "WIN", stats, writer, log)
                    elif toco_sl:
                        _resolver(p, ts, precio, "LOSS", stats, writer, log)
                    elif ts >= p["ts_limite"]:
                        _resolver(p, ts, precio, "TIMEOUT", stats, writer, log)
                    else:
                        aun_pendientes.append(p)
                pendientes = aun_pendientes

            if time.monotonic() - ultimo_resumen >= resumen_cada:
                print(f"\n[{_fmt_fecha_ahora()} UTC] {len(pendientes)} pendientes")
                print(_texto_marcador(stats))
                ultimo_resumen = time.monotonic()

            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")
        print(_texto_marcador(stats))
    finally:
        log.close()


if __name__ == "__main__":
    main()
