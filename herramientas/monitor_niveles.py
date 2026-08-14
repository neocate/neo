# ---------------------------------------------------------------
# monitor_niveles.py - Monitorea el precio EN VIVO contra los niveles que
# calcula niveles.py, avisando cuando el precio entra o sale de la
# zona de tolerancia de un nivel vigente (mismo criterio de "toque" que
# _contar_toques: |precio - nivel| <= tolerancia).
#
# SOLO niveles - las señales de vela (mercado/senales.py) viven en
# monitor_senales.py, un proceso hermano separado (2026-08-11, antes
# estaban fusionadas aqui mismo desde el commit 7a11670 - nunca hubo una
# version separada previa, se extrajo de cero). Mismo motivo que separo
# grabador_libro.py de monitor.py: señales es logica que se ajusta/prueba
# con frecuencia, vigilar niveles es mecanico y estable - no tiene sentido
# que un ajuste de señales obligue a reiniciar (y cortar la serie de) el
# vigilante de niveles. Las funciones que ambos comparten (leer el flujo en
# vivo, comprobar sus dependencias) viven en monitor_comun.py.
#
# Los niveles son una FOTO tomada al arrancar (via _analizar() de
# niveles.py, sobre el historico ya descargado) - este proceso no
# los recalcula vela a vela. Si el historico cambio mucho desde que arranco,
# cortar (Ctrl+C), volver a bajarlo/correr niveles.py, y reiniciar.
#
# Lee (tail) el CSV que escribe grabador_libro.py - NO pide nada a la API
# por su cuenta, y YA NO arranca sus dependencias si faltan (2026-08-11
# a 2026-08-12: cambio de filosofia, ver monitor_comun.py) - si
# grabador_libro.py (libro/OI/funding/CVD) o descargar_bit.py --feed
# (velas) no estan corriendo para esta moneda, avisa y para la ejecucion
# en vez de arrancarlos el mismo - Fran, tras liarse relanzando procesos en
# cascada y dejarse alguno sin relanzar: "de esta forma evito dejar sin
# relanzar por error un py". Hay que lanzarlos a mano primero.
#
# Localiza el archivo mas reciente en herramientas/libro/flujo_*.csv que
# incluya la moneda pedida (ver monitor_comun._localizar_csv_libro). Al
# arrancar se posiciona al FINAL del archivo (no relee historico viejo) y
# cada --cada segundos lee solo las lineas nuevas agregadas desde la
# ultima vuelta.
#
# OJO: un "toque" en vivo (precio entra en nivel +/- tolerancia) NO es lo
# mismo que "roto" en niveles.py (que exige --confirmacion-velas
# CIERRES DE VELA consecutivos). Esto es aviso en tiempo real de que el
# precio esta ahi - no una confirmacion definitiva de ruptura. Para eso,
# correr niveles.py de nuevo despues y mirar el estado.
#
# Con --tf-macro, igual que en niveles.py: los niveles del TF
# principal se acotan al rango [suelo_macro, techo_macro] antes de vigilarlos
# (mismos k/tolerancia-atr/toques-min para el macro). Los dos topes macro se
# vigilan tambien, marcados [macro] en los avisos.
#
# Avisos: por consola en vivo, Y a un CSV en herramientas/libro/
# (avisos_<coin>_<tf>.csv, SIN fecha - un reinicio sigue el mismo fichero,
# igual que flujo_<coin>.csv) - timestamp, evento, nivel, precio,
# imbalance, cvd en ese momento, para revisar despues o cruzar con el resto.
#
# Uso:
#   python herramientas/monitor_niveles.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 4 [--desde-dias 90] [--tf-macro 4h] [--cada 15]
#
# Ejemplo:
#   python herramientas/monitor_niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4 --desde-dias 90 --tf-macro 4h
# ---------------------------------------------------------------

import csv
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.niveles import _analizar
from herramientas.descargar_bit import _archivo_velas as _archivo_bitget
from herramientas.monitor_comun import (
    DIR_LIBRO, CAMPOS_AVISOS, _flt, _localizar_csv_libro, _tail_csv,
    _ultima_fila_coin, _requerir_grabador_libro, _requerir_feed_velas,
)


def _fmt_fecha_ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _armar_watchlist(coin, tf, k, tolerancia_atr, toques_min, desde_dias,
                      confirmacion_velas, tf_macro):
    """Devuelve (watchlist, tolerancia_micro) - watchlist es una lista de
    dicts {precio, tipo, origen, tolerancia} a vigilar. Sin --tf-macro es
    simplemente todo lo vigente del TF principal; con --tf-macro se acota
    al rango macro (igual que niveles.py) y se agregan los dos
    topes macro marcados como tal."""
    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    tolerancia = r["tolerancia"]

    if tf_macro is None:
        watch = [dict(precio=n["precio"], tipo=n["tipo"], origen="micro", tolerancia=tolerancia)
                 for n in r["techos"] + r["suelos"]]
        return watch, r

    rm = _analizar(coin, tf_macro, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    techo_macro = min((n for n in rm["techos"] if n["dist_pct"] > 0),
                       key=lambda d: d["dist_pct"], default=None)
    suelo_macro = max((n for n in rm["suelos"] if n["dist_pct"] < 0),
                       key=lambda d: d["dist_pct"], default=None)
    lo = suelo_macro["precio"] if suelo_macro else float("-inf")
    hi = techo_macro["precio"] if techo_macro else float("inf")

    watch = [dict(precio=n["precio"], tipo=n["tipo"], origen="micro", tolerancia=tolerancia)
             for n in r["techos"] + r["suelos"] if lo <= n["precio"] <= hi]
    if techo_macro:
        watch.append(dict(precio=techo_macro["precio"], tipo="techo", origen="macro",
                           tolerancia=rm["tolerancia"]))
    if suelo_macro:
        watch.append(dict(precio=suelo_macro["precio"], tipo="suelo", origen="macro",
                           tolerancia=rm["tolerancia"]))
    return watch, r


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
    tf_macro = None
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
        elif resto[i] == "--tf-macro":
            i += 1; tf_macro = resto[i]
        elif resto[i] == "--cada":
            i += 1; cada = float(resto[i])
        i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parametros obligatorios: --k, --tolerancia-atr, --toques-min")
        print("(sin defaults a proposito - ver cabecera de niveles.py)")
        return

    # 2026-08-12: ya NO se auto-arranca nada si falta una dependencia -
    # aviso y parar, ver cabecera de monitor_comun.py (cadena en cascada
    # pedida por Fran para no dejarse un proceso sin relanzar por error).
    if not _requerir_grabador_libro(coin):
        print(f"ERROR: grabador_libro.py no esta corriendo para {coin.upper()} - "
              f"lanzalo primero (python herramientas/grabador_libro.py {coin.lower()}) y reintenta.")
        return
    if not _requerir_feed_velas():
        print("ERROR: descargar_bit.py --feed no esta corriendo - lanzalo primero y reintenta.")
        return
    tfs_necesarios = {tf} | ({tf_macro} if tf_macro else set())
    faltan = [t for t in tfs_necesarios if not os.path.exists(_archivo_bitget(coin, t))]
    if faltan:
        print(f"ERROR: falta el historico de {coin.upper()} {', '.join(faltan)} en herramientas/velas/ - "
              f"bajalo primero con: python herramientas/descargar_bit.py --velas {coin.lower()} <tf>")
        return

    watch, r = _armar_watchlist(coin, tf, k, tolerancia_atr, toques_min, desde_dias,
                                 confirmacion_velas, tf_macro)
    if not watch:
        print("No hay niveles vigentes para vigilar con estos parametros.")
        return

    os.makedirs(DIR_LIBRO, exist_ok=True)
    ruta_log = os.path.join(DIR_LIBRO, f"avisos_{coin.upper()}_{tf}.csv")
    nuevo = not os.path.exists(ruta_log)
    log = open(ruta_log, "a", newline="")
    writer = csv.DictWriter(log, fieldnames=CAMPOS_AVISOS)
    if nuevo:
        writer.writeheader()
        log.flush()

    print(f"Vigilando {coin.upper()} {tf}" + (f" (acotado a rango macro {tf_macro})" if tf_macro else ""))
    print(f"Precio de referencia al armar niveles: {r['precio_actual']:.4f}")
    print(f"{len(watch)} niveles vigilados:")
    for niv in sorted(watch, key=lambda d: -d["precio"]):
        etiqueta = f"[{niv['origen']}]" if niv["origen"] == "macro" else ""
        print(f"  {niv['tipo']:<6} {niv['precio']:>12.4f}  tolerancia {niv['tolerancia']:.4f} {etiqueta}")
    print(f"Log de avisos -> {ruta_log}")

    ruta_libro = _localizar_csv_libro(coin)
    if ruta_libro is None:
        print(f"ERROR: grabador_libro.py esta vivo pero no se encuentra su CSV de {coin.upper()} "
              f"en {DIR_LIBRO} todavia (¿acaba de arrancar? reintenta en unos segundos).")
        return
    print(f"Leyendo de {ruta_libro} (solo lineas nuevas desde ahora).")

    offset = os.path.getsize(ruta_libro)
    for niv in watch:
        niv["tocando"] = False
    ultimo_imbalance = ultimo_cvd = None

    # Estado inicial desde la ULTIMA fila ya grabada (no se relee ni se
    # avisa nada del historico viejo - offset ya quedo al final, arriba -
    # esto solo evita arrancar a ciegas: sin esto, imbalance/cvd empiezan
    # en None y todos los niveles en tocando=False aunque el precio YA
    # estuviera dentro de la tolerancia de alguno, hasta que llegue el
    # primer tick nuevo.
    fila_inicial = _ultima_fila_coin(ruta_libro)
    if fila_inicial:
        precio_inicial = _flt(fila_inicial.get("mid"))
        if precio_inicial is None:
            bid, ask = _flt(fila_inicial.get("bid")), _flt(fila_inicial.get("ask"))
            precio_inicial = (bid + ask) / 2 if (bid is not None and ask is not None) else None
        ultimo_imbalance = _flt(fila_inicial.get("imbalance"))
        ultimo_cvd = _flt(fila_inicial.get("cvd"))
        if precio_inicial is not None:
            for niv in watch:
                niv["tocando"] = (niv["precio"] - niv["tolerancia"]) <= precio_inicial <= (niv["precio"] + niv["tolerancia"])
            print(f"Estado inicial ({fila_inicial.get('fecha_utc', '?')}): precio {precio_inicial:.4f}  "
                  f"imbalance {(ultimo_imbalance or 0.0):+.2f}  cvd {(ultimo_cvd or 0.0):+.4f}")
            for niv in watch:
                if niv["tocando"]:
                    etiqueta = f" [{niv['origen']}]" if niv["origen"] == "macro" else ""
                    print(f"  ya esta tocando {niv['tipo']} {niv['precio']:.4f}{etiqueta}")

    print("Ctrl+C para parar.\n")

    try:
        while True:
            filas, offset = _tail_csv(ruta_libro, offset)
            for fila in filas:
                precio_actual = _flt(fila.get("mid"))
                if precio_actual is None:
                    bid, ask = _flt(fila.get("bid")), _flt(fila.get("ask"))
                    precio_actual = (bid + ask) / 2 if (bid is not None and ask is not None) else None
                if precio_actual is None:
                    continue

                imbalance_val = _flt(fila.get("imbalance"))
                if imbalance_val is not None:
                    ultimo_imbalance = imbalance_val
                cvd = _flt(fila.get("cvd"))
                if cvd is not None:
                    ultimo_cvd = cvd

                techo_cercano = min((n for n in watch if n["precio"] > precio_actual),
                                     key=lambda d: d["precio"] - precio_actual, default=None)
                suelo_cercano = max((n for n in watch if n["precio"] < precio_actual),
                                     key=lambda d: d["precio"], default=None)
                resumen = (f"{fila.get('fecha_utc', _fmt_fecha_ahora())}  precio {precio_actual:>12.4f}  "
                           f"imbalance {ultimo_imbalance if ultimo_imbalance is not None else 0.0:>+6.2f}  "
                           f"cvd {ultimo_cvd if ultimo_cvd is not None else 0.0:>+10.4f}")
                if techo_cercano:
                    resumen += f"  | techo mas cerca {techo_cercano['precio']:.4f}"
                if suelo_cercano:
                    resumen += f"  | suelo mas cerca {suelo_cercano['precio']:.4f}"
                print(resumen)

                for niv in watch:
                    cerca = (niv["precio"] - niv["tolerancia"]) <= precio_actual <= (niv["precio"] + niv["tolerancia"])
                    if cerca and not niv["tocando"]:
                        niv["tocando"] = True
                        evento = "tocando"
                    elif not cerca and niv["tocando"]:
                        niv["tocando"] = False
                        lado = "arriba" if precio_actual > niv["precio"] else "abajo"
                        evento = f"sale_hacia_{lado}"
                    else:
                        continue

                    etiqueta = f" [{niv['origen']}]" if niv["origen"] == "macro" else ""
                    print(f"  >>> {evento.upper()} {niv['tipo']} {niv['precio']:.4f}{etiqueta}  "
                          f"precio {precio_actual:.4f}  imbalance {(ultimo_imbalance or 0.0):+.2f}  "
                          f"cvd {(ultimo_cvd or 0.0):+.4f}")
                    writer.writerow({
                        "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                        "fecha_utc": _fmt_fecha_ahora(),
                        "coin": coin.upper(),
                        "evento": evento,
                        "tipo": niv["tipo"],
                        "origen": niv["origen"],
                        "nivel_precio": niv["precio"],
                        "precio_actual": precio_actual,
                        "imbalance": round(ultimo_imbalance, 4) if ultimo_imbalance is not None else "",
                        "cvd": round(ultimo_cvd, 4) if ultimo_cvd is not None else "",
                        "pid": os.getpid(),
                    })
                    log.flush()

            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")
    finally:
        log.close()


if __name__ == "__main__":
    main()
