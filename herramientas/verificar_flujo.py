# ---------------------------------------------------------------
# verificar_flujo.py - Detecta tramos de flujo_<coin>.csv donde el CVD no
# es una serie continua fiable: comprueba que cvd[i] == cvd[i-1] +
# delta_vol[i] fila a fila, y agrupa las filas consecutivas que fallan en
# "incidentes" (desde -> hasta). Tambien avisa directamente si aparece mas
# de un PID distinto en el fichero (desde 2026-08-12 cada fila lleva el
# PID de quien la escribio, ver grabador_libro.CAMPOS_CSV) - señal aun mas
# directa que el calculo de CVD, aunque solo vale para filas escritas
# despues de ese cambio (las de antes llevan pid vacio).
#
# Motivo (2026-08-11/12, ver anotaciones.md/mirar.md): grabador_libro.py
# no tenia forma de detectar si YA habia otro escritor vivo (ni el de los
# huerfanos del 7 de agosto, invisibles a 'ps w', ni el auto-arranque
# propio con el mismo bug) - dos veces en la misma sesion, un segundo
# grabador_libro.py escribio el mismo flujo_<coin>.csv en paralelo, cada
# uno con su propio CVD acumulado en memoria, dejando el fichero con dos
# series entrelazadas en vez de una. Los dos incidentes se encontraron a
# mano con scripts de usar y tirar - esto es lo mismo pero reutilizable,
# para correrlo cuando quieras sin depender de que alguien te escriba el
# script cada vez.
#
# Un "incidente" abierto (que llegue hasta la ULTIMA fila del fichero) es
# la señal de que puede haber un escritor duplicado corriendo AHORA MISMO
# - revisa con 'ps -ef | grep grabador_libro.py' (NUNCA 'ps w' a secas,
# ver monitor_comun.py._proceso_corriendo) si sale mas de un PID.
#
# No repara nada - el CVD de un tramo con dos escritores no se puede
# reconstruir (no hay forma de saber que trades proceso cada uno). Esto
# solo te dice EXACTAMENTE que rango no es fiable, para poder excluirlo de
# cualquier analisis que dependa de una serie de CVD continua.
#
# Uso:
#   python herramientas/verificar_flujo.py [coin[,coin2,...]] [--separacion-min 5]
#   coin por defecto: btc,eth (mismo default que grabador_libro.py)
#   --separacion-min: huecos "limpios" mas cortos que esto (minutos) se
#                      fusionan con el incidente siguiente en vez de
#                      contarse como que el problema se arreglo solo.
#
# Ejemplos:
#   python herramientas/verificar_flujo.py
#   python herramientas/verificar_flujo.py btc,eth
#   python herramientas/verificar_flujo.py btc,eth --separacion-min 10
# ---------------------------------------------------------------

import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.grabador_libro import _archivo as _archivo_flujo


def _incidentes_cvd(filas, tolerancia=0.01, separacion_max=300.0):
    """'filas' es una lista de (fecha_utc, cvd, delta_vol) EN ORDEN de
    aparicion en el fichero. Devuelve una lista de (fecha_desde,
    fecha_hasta, n_filas) - tramos donde cvd no cuadra con cvd_anterior +
    delta_vol (indicio de dos escritores simultaneos).

    Con DOS escritores intercalando filas, una transicion entre dos filas
    del MISMO escritor sale "consistente" por pura coincidencia - sin
    fusionar esto, un incidente real de horas aparece troceado en decenas
    de fragmentos separados por huecos de un puñado de segundos que no
    significan que el problema se resolviera. Por eso se fusionan
    incidentes separados por menos de 'separacion_max' segundos - un hueco
    realmente limpio (el problema se arreglo de verdad) dura minutos u
    horas, no un puñado de vueltas del bucle."""
    crudos = []
    en_racha = False
    inicio = ultimo_malo = None
    n = 0
    prev_cvd = prev_fecha = None
    for fecha, cvd, delta in filas:
        if prev_cvd is not None:
            esperado = prev_cvd + delta
            malo = abs(esperado - cvd) > tolerancia
            if malo:
                if not en_racha:
                    en_racha = True
                    inicio = prev_fecha
                    n = 0
                n += 1
                ultimo_malo = fecha
            elif en_racha:
                crudos.append([inicio, ultimo_malo, n])
                en_racha = False
        prev_cvd, prev_fecha = cvd, fecha
    if en_racha:
        crudos.append([inicio, ultimo_malo, n])

    if not crudos:
        return []
    fmt = "%Y-%m-%d %H:%M:%S"
    fusionados = [crudos[0]]
    for desde, hasta, n in crudos[1:]:
        anterior = fusionados[-1]
        hueco = (datetime.strptime(desde, fmt) - datetime.strptime(anterior[1], fmt)).total_seconds()
        if hueco <= separacion_max:
            anterior[1] = hasta
            anterior[2] += n
        else:
            fusionados.append([desde, hasta, n])
    return [tuple(x) for x in fusionados]


def _verificar_coin(coin, separacion_max):
    ruta = _archivo_flujo(coin)
    if not os.path.exists(ruta):
        print(f"{coin}: no existe {ruta}")
        return

    filas = []
    pids_vistos = set()
    total_filas = 0
    filas_malformadas = 0
    with open(ruta, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        idx = {n: i for i, n in enumerate(header)}
        tiene_pid = "pid" in idx
        for row in reader:
            if len(row) != len(header):
                filas_malformadas += 1
                continue
            total_filas += 1
            if tiene_pid and row[idx["pid"]]:
                pids_vistos.add(row[idx["pid"]])
            try:
                cvd = float(row[idx["cvd"]])
                delta = float(row[idx["delta_vol"]])
            except ValueError:
                continue
            filas.append((row[idx["fecha_utc"]], cvd, delta))

    print(f"{coin}: {ruta}")
    print(f"  {total_filas} filas ({filas_malformadas} descartadas por columnas incompletas - "
          f"normal si el fichero se lee mientras se escribe).")
    if tiene_pid and len(pids_vistos) > 1:
        print(f"  AVISO: {len(pids_vistos)} PID distintos escribieron en este fichero: "
              f"{', '.join(sorted(pids_vistos))} - señal directa de escritores duplicados "
              f"(compruebalo con 'ps -ef | grep grabador_libro.py').")

    incidentes = _incidentes_cvd(filas, separacion_max=separacion_max)
    if not incidentes:
        print("  CVD consistente en todo el fichero.\n")
        return
    print(f"  {len(incidentes)} incidente(s) de CVD -")
    ultima_fecha_fichero = filas[-1][0] if filas else None
    for desde, hasta, n in incidentes:
        abierto = " <-- LLEGA HASTA LA ULTIMA FILA, revisa si hay un escritor duplicado AHORA" \
            if hasta == ultima_fecha_fichero else ""
        print(f"    {desde}  ->  {hasta}   ({n} filas){abierto}")
    print("  El CVD dentro de esos rangos no es fiable como serie continua (ver cabecera del "
          "script) - el resto de columnas (bid/ask/mid/imbalance/OI/funding/long_short_ratio) "
          "no se ven afectadas.\n")


def main():
    args = sys.argv[1:]
    coins = [c.strip().upper() for c in args[0].split(",")] if args else ["BTC", "ETH"]
    resto = args[1:]
    separacion_max = 300.0  # 5 min - ver _incidentes_cvd
    i = 0
    while i < len(resto):
        if resto[i] == "--separacion-min":
            i += 1; separacion_max = float(resto[i]) * 60.0
        i += 1

    for coin in coins:
        _verificar_coin(coin, separacion_max)


if __name__ == "__main__":
    main()
