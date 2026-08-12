# ---------------------------------------------------------------
# validador_niveles.py - Comprueba EN VIVO si cada señal de las 7 del Grupo
# A (mercado/senales.NOMBRE_REFINADO) dispara de verdad cerca de un nivel
# vigente del tipo que necesita (suelo o techo, ver
# mercado/senales.NIVEL_UTIL_GRUPO_A), con el MISMO criterio que usa
# monitor_niveles.py para "tocando" (|precio-nivel| <= tolerancia).
#
# 2026-08-12: extraido de fjsl.py, que hacia esto Y el marcador TP/SL en el
# mismo fichero - se separan por el mismo motivo de siempre en este
# proyecto ("un .py, una obligacion"): esta parte es la prioridad ahora
# mismo (entender que señales confirman de verdad con un nivel real y
# cuales no - Fran: "necesitamos analizar todas las señales junto con
# niveles soportes, para poder entender donde fallan y cuales no son
# validas"), el TP/SL (marcador_tpsl.py) es preparatorio para una cartera
# simulada futura, sin relacion con esto.
#
# No hace falta tailear avisos_*.csv ni definir ninguna ventana de tiempo:
# la señal solo dispara al cerrar su propia vela (senales_<coin>_<tf>.csv,
# que escribe monitor_senales.py), y los niveles vigentes
# (niveles_soporte._analizar()["techos"/"suelos"]) son un ESTADO, no un
# evento - se comprueba en el mismo instante que cierra esa vela. Tampoco
# hace falta flujo_<coin>.csv ni grabador_libro.py - a diferencia de
# marcador_tpsl.py, aqui no se seguie el precio tick a tick despues, solo
# el precio de la propia señal en el momento en que dispara.
#
# Esto es una comprobacion INDEPENDIENTE de lo que ya implica el propio
# nombre de la señal (p.ej. "rechazo_max_en_soporte" ya significa que
# monitor_senales.py la vio cerca de un nivel favorable AL ARRANCAR el
# monitor, con su propia foto de niveles) - aqui se repite la comprobacion
# con los niveles vigentes ACTUALES de este proceso (refrescados cada
# --refresco-tolerancia-horas), asi que puede revelar si esa etiqueta se
# quedo desactualizada.
#
# Depende de que YA esten corriendo monitor_niveles.py Y monitor_senales.py
# <coin> <tf> (2026-08-12, cadena en cascada pedida por Fran - ver
# monitor_comun.py) - si alguno de los dos falta, avisa y PARA la
# ejecucion en vez de esperar o arrancarlo el mismo. No hace falta
# comprobar aqui grabador_libro.py/descargar_bit.py --feed por separado:
# si monitor_niveles.py/monitor_senales.py estan vivos, ya comprobaron
# ELLOS esas dependencias al arrancar (confianza en cascada, un solo
# eslabon por comprobacion).
#
# Uso:
#   python herramientas/validador_niveles.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 4
#                                             [--desde-dias N] [--confirmacion-velas 2]
#                                             [--refresco-tolerancia-horas 6] [--resumen-cada 1800] [--cada 15]
#
#   python herramientas/validador_niveles.py --consultar <coin> <tf>
#     Modo puntual: lee confirmaciones_<COIN>_<TF>.csv ya grabado por el
#     proceso en vivo e imprime el marcador acumulado, sin arrancar ningun
#     bucle.
#
# Ejemplo:
#   python herramientas/validador_niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4
#   python herramientas/validador_niveles.py --consultar eth 1h
# ---------------------------------------------------------------

import csv
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.niveles_soporte import _analizar
from herramientas.monitor_comun import DIR_LIBRO, CAMPOS_AVISOS, _flt, _tail_csv, _proceso_corriendo
from mercado.senales import NIVEL_UTIL_GRUPO_A, NOMBRE_REFINADO

# nombre YA RENOMBRADO (el que sale en senales_*.csv, p.ej.
# "rechazo_max_en_soporte") -> "techo"/"suelo" que esa señal necesita cerca
# para tener sentido (mismo mapeo que NIVEL_UTIL_GRUPO_A, pero indexado por
# el nombre renombrado en vez de por el nombre base).
ROL_ESPERADO = {renombrada: NIVEL_UTIL_GRUPO_A[base] for base, renombrada in NOMBRE_REFINADO.items()}

CAMPOS_CONFIRMACIONES = [
    "ts_ms", "fecha_utc", "coin", "tf", "senal", "rol_esperado", "confirmada",
    "precio_senal", "nivel_mas_cercano", "distancia", "tolerancia", "pid",
]


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_fecha_ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _refrescar_niveles(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas):
    """(tolerancia, techos, suelos) - techos/suelos son los vigentes AHORA
    (r["techos"]/r["suelos"] de niveles_soporte._analizar(), ya vivos/flip,
    ver niveles_soporte.py)."""
    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    return r["tolerancia"], r["techos"], r["suelos"]


def _evaluar_confirmacion(nombre, precio, techos, suelos, tolerancia):
    """None si 'nombre' no es una de las 7 señales del Grupo A (no aplica).
    Si lo es, devuelve (rol_esperado, confirmada, nivel_mas_cercano_o_None,
    distancia_o_None) - confirmada=True si hay un nivel vigente del rol que
    esa señal necesita a <= tolerancia de precio, mismo criterio que
    monitor_niveles.py usa para "tocando"."""
    rol = ROL_ESPERADO.get(nombre)
    if rol is None:
        return None
    lista = techos if rol == "techo" else suelos
    if not lista:
        return rol, False, None, None
    niv_cercano = min(lista, key=lambda n: abs(precio - n["precio"]))
    distancia = abs(precio - niv_cercano["precio"])
    return rol, distancia <= tolerancia, niv_cercano["precio"], distancia


def _imprimir_confirmaciones(confirmaciones):
    if not confirmaciones:
        return
    print(f"\n{'señal':<28}{'rol':<8}{'confirmada':>11}{'no confirm.':>13}{'% confirmada':>14}")
    for nombre in sorted(confirmaciones):
        c = confirmaciones[nombre]
        total = c["confirmada"] + c["no_confirmada"]
        pct = (c["confirmada"] / total * 100) if total else 0.0
        print(f"{nombre:<28}{c['rol']:<8}{c['confirmada']:>11}{c['no_confirmada']:>13}{pct:>13.1f}%")


def _consultar(coin, tf):
    ruta = os.path.join(DIR_LIBRO, f"confirmaciones_{coin.upper()}_{tf}.csv")
    if not os.path.exists(ruta):
        print(f"No hay {ruta} todavia - validador_niveles.py no ha evaluado ninguna señal "
              f"para {coin.upper()} {tf} (¿esta corriendo el proceso en vivo?).")
        return
    confirmaciones = {}
    with open(ruta, newline="") as f:
        for fila in csv.DictReader(f):
            nombre, rol = fila.get("senal"), fila.get("rol_esperado")
            if not nombre or not rol:
                continue
            c = confirmaciones.setdefault(nombre, {"rol": rol, "confirmada": 0, "no_confirmada": 0})
            c["confirmada" if fila.get("confirmada") == "1" else "no_confirmada"] += 1
    print(f"=== Confirmaciones acumuladas {coin.upper()} {tf} ({ruta}) ===")
    _imprimir_confirmaciones(confirmaciones)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--consultar":
        if len(args) < 3:
            print("Uso: python herramientas/validador_niveles.py --consultar <coin> <tf>")
            return
        _consultar(args[1], args[2])
        return
    if len(args) < 2:
        print(__doc__)
        return
    coin, tf = args[0], args[1]
    resto = args[2:]

    k = tolerancia_atr = toques_min = None
    desde_dias = None
    confirmacion_velas = 2
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
        elif resto[i] == "--refresco-tolerancia-horas":
            i += 1; refresco_tolerancia_horas = float(resto[i])
        elif resto[i] == "--resumen-cada":
            i += 1; resumen_cada = float(resto[i])
        elif resto[i] == "--cada":
            i += 1; cada = float(resto[i])
        i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parametros obligatorios: --k, --tolerancia-atr, --toques-min")
        print("(sin defaults a proposito - ver cabecera de niveles_soporte.py)")
        return

    # 2026-08-12: cadena en cascada pedida por Fran - si monitor_niveles.py
    # o monitor_senales.py no estan corriendo para esta moneda/TF, avisa y
    # para en vez de esperar (ver monitor_comun.py). Comprueba el PROCESO,
    # no solo el CSV: un senales_*.csv de una sesion vieja podria seguir en
    # disco aunque el proceso ya no este vivo.
    if not _proceso_corriendo(["monitor_niveles.py", coin.lower(), tf]):
        print(f"ERROR: monitor_niveles.py no esta corriendo para {coin.upper()} {tf} - "
              f"lanzalo primero y reintenta.")
        return
    if not _proceso_corriendo(["monitor_senales.py", coin.lower(), tf]):
        print(f"ERROR: monitor_senales.py no esta corriendo para {coin.upper()} {tf} - "
              f"lanzalo primero y reintenta.")
        return

    ruta_senales = os.path.join(DIR_LIBRO, f"senales_{coin.upper()}_{tf}.csv")
    if not os.path.exists(ruta_senales):
        print(f"ERROR: monitor_senales.py esta vivo pero no se encuentra {ruta_senales} todavia "
              f"(¿acaba de arrancar? reintenta en unos segundos).")
        return

    os.makedirs(DIR_LIBRO, exist_ok=True)
    ruta_confirmaciones = os.path.join(DIR_LIBRO, f"confirmaciones_{coin.upper()}_{tf}.csv")
    nuevo = not os.path.exists(ruta_confirmaciones)
    log = open(ruta_confirmaciones, "a", newline="")
    writer = csv.DictWriter(log, fieldnames=CAMPOS_CONFIRMACIONES)
    if nuevo:
        writer.writeheader()
        log.flush()

    tolerancia, techos, suelos = _refrescar_niveles(coin, tf, k, tolerancia_atr, toques_min,
                                                      desde_dias, confirmacion_velas)
    ultimo_refresco = time.monotonic()
    ultimo_resumen = time.monotonic()
    offset_senales = os.path.getsize(ruta_senales)
    confirmaciones = {}

    print(f"validador_niveles.py: {coin.upper()} {tf} - tolerancia {tolerancia:.4f} "
          f"({len(techos)} techos / {len(suelos)} suelos vigentes). Leyendo señales nuevas desde ahora.")
    print(f"Marcador -> {ruta_confirmaciones}. Ctrl+C para parar.")

    try:
        while True:
            if time.monotonic() - ultimo_refresco >= refresco_tolerancia_horas * 3600:
                tolerancia, techos, suelos = _refrescar_niveles(coin, tf, k, tolerancia_atr, toques_min,
                                                                  desde_dias, confirmacion_velas)
                ultimo_refresco = time.monotonic()
                print(f"(niveles refrescados: tolerancia {tolerancia:.4f}, {len(techos)} techos, "
                      f"{len(suelos)} suelos)")

            filas, offset_senales = _tail_csv(ruta_senales, offset_senales, fieldnames=CAMPOS_AVISOS)
            for fila in filas:
                nombre = fila.get("tipo")
                resultado = _evaluar_confirmacion(nombre, _flt(fila.get("precio_actual")),
                                                   techos, suelos, tolerancia)
                if resultado is None:
                    continue
                ts = int(fila["timestamp_ms"])
                precio = _flt(fila.get("precio_actual"))
                rol, confirmada, niv_cercano, distancia = resultado
                c = confirmaciones.setdefault(nombre, {"rol": rol, "confirmada": 0, "no_confirmada": 0})
                c["confirmada" if confirmada else "no_confirmada"] += 1
                print(f"  {'[OK]' if confirmada else '[NO]'} {nombre} @ {precio:.4f} - "
                      f"{rol} vigente mas cercano "
                      + (f"{niv_cercano:.4f} (dist {distancia:.4f}, tolerancia {tolerancia:.4f})"
                         if niv_cercano is not None else "ninguno")
                      + f"  ({_fmt_fecha(ts)})")
                writer.writerow({
                    "ts_ms": ts, "fecha_utc": _fmt_fecha(ts), "coin": coin.upper(), "tf": tf,
                    "senal": nombre, "rol_esperado": rol, "confirmada": 1 if confirmada else 0,
                    "precio_senal": precio, "nivel_mas_cercano": niv_cercano, "distancia": distancia,
                    "tolerancia": tolerancia, "pid": os.getpid(),
                })
                log.flush()

            if time.monotonic() - ultimo_resumen >= resumen_cada:
                print(f"\n[{_fmt_fecha_ahora()} UTC]")
                _imprimir_confirmaciones(confirmaciones)
                ultimo_resumen = time.monotonic()

            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")
        _imprimir_confirmaciones(confirmaciones)
    finally:
        log.close()


if __name__ == "__main__":
    main()
