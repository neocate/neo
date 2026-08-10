# ---------------------------------------------------------------
# backtest_senales.py - Backtest OFFLINE de mercado/senales.py sobre
# historico profundo, para afinar umbrales SIN tocar monitor_niveles.py ni
# depender de una sesion en vivo (grabador_libro.py corriendo, Bitget, etc).
#
# Dos fuentes posibles:
#   --fuente historicos (default) -> historicos/<fecha>_<COIN>_<TF>_binance.csv
#                                     (el mas profundo: años de historia,
#                                     bajado con historicos/descargar_bin.py)
#   --fuente bitget               -> herramientas/libro/historico_<COIN>_<TF>_bitget.csv
#                                     (el que usa monitor_niveles.py en vivo,
#                                     mas corto pero el precio real vigilado)
# Es solo para pruebas/calibracion offline - NO se usa para decidir niveles
# en vivo (ver advertencia de Bitget-vs-Binance en niveles_soporte.py), asi
# que mezclar exchange aqui no es un problema.
#
# Recorre el historico vela a vela llamando a senales.detectar() (la MISMA
# funcion que usa monitor_niveles.py en vivo) y para cada señal que se
# dispara mide el retorno % a N velas despues, en la direccion que esa
# señal anticipa (ver DIRECCION_ESPERADA) - comparado contra la linea base
# (retorno medio de CUALQUIER vela a esa misma distancia). Una señal sin
# "borde" real deberia parecerse a la linea base.
#
# Uso:
#   python herramientas/backtest_senales.py <coin> <tf> [--fuente historicos|bitget]
#                                            [--dias N] [--horizontes 5,15,30] [--k 3]
#
# Ejemplos:
#   python herramientas/backtest_senales.py btc 15m
#   python herramientas/backtest_senales.py btc 15m --dias 365 --horizontes 10,30,60
#   python herramientas/backtest_senales.py eth 5m --fuente bitget
# ---------------------------------------------------------------

import csv
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import senales
from herramientas.descargar_bit import _archivo as _archivo_bitget

DIR_HISTORICOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "historicos")

# Direccion que cada señal anticipa: +1 = espera subida, -1 = espera
# bajada. impulso/aceleracion/ruptura son de CONTINUACION; rechazo/
# divergencia/rsi-extremo son de REVERSION - por eso rechazo_max (mecha de
# rechazo arriba) espera bajada, no subida.
DIRECCION_ESPERADA = {
    "impulso_alza": 1, "impulso_baja": -1,
    "aceleracion_alza": 1, "aceleracion_baja": -1,
    "ruptura_alza": 1, "ruptura_baja": -1,
    "rechazo_max": -1, "rechazo_min": 1,
    "div_bajista": -1, "div_alcista": 1,
    "rsi_sobrecompra": -1, "rsi_sobreventa": 1,
}


def _ultimo_timestamp_ms(ruta):
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        cola = f.read()
    lineas = [l for l in cola.split(b"\n") if l.strip()]
    return int(lineas[-1].split(b",")[0])


def _localizar_historicos(coin, tf):
    """Busca en historicos/ el CSV mas reciente <fecha>_<COIN>_<TF>_binance.csv
    (puede haber varias fechas si se re-bajo mas de una vez - se toma la
    ultima modificada, mismo patron que monitor_niveles._localizar_csv_libro)."""
    if not os.path.isdir(DIR_HISTORICOS):
        return None
    patron = re.compile(rf"^\d{{2}}-\d{{2}}-\d{{2}}_{coin.upper()}_{tf}_binance\.csv$")
    candidatos = [os.path.join(DIR_HISTORICOS, n) for n in os.listdir(DIR_HISTORICOS) if patron.match(n)]
    if not candidatos:
        return None
    return max(candidatos, key=os.path.getmtime)


def _cargar_velas(ruta, dias=None):
    """Carga 'ruta' (mismo formato en historicos/ y herramientas/libro/:
    timestamp,fecha_utc,open,high,low,close,volumen). Si se pide 'dias', el
    corte es relativo a la ULTIMA vela del fichero (no a 'ahora' - son datos
    historicos, pueden ser de años atras) y se descartan las filas viejas
    AL VUELO en vez de cargar el fichero entero en memoria para tirarlas
    despues (los de 1m/3m pueden pesar cientos de MB)."""
    corte_ms = None
    if dias is not None:
        ultimo_ts = _ultimo_timestamp_ms(ruta)
        corte_ms = ultimo_ts - int(dias * 86_400_000)

    velas = []
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r)  # cabecera
        for row in r:
            ts = int(row[0])
            if corte_ms is not None and ts < corte_ms:
                continue
            velas.append([ts, float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])])
    return velas


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _backtest(velas, k, horizontes):
    """Recorre 'velas' vela a vela llamando a senales.detectar() sobre una
    ventana acotada (senales.VENTANA_MAXIMA - mismo criterio que usa
    monitor_niveles.py en vivo, ver su comentario) para no copiar listas
    cada vez mas grandes en cada paso. Devuelve:
      disparos: {nombre_señal: [(idx, retorno_pct_por_horizonte), ...]}
      baseline: {horizonte: [retorno_pct, ...]} de TODAS las velas (para comparar)
    """
    ventana_max = senales.VENTANA_MAXIMA
    disparos = {nombre: {h: [] for h in horizontes} for nombre in DIRECCION_ESPERADA}
    baseline = {h: [] for h in horizontes}
    n = len(velas)
    inicio = min(ventana_max, n)

    for i in range(inicio, n):
        cierre_i = velas[i][4]
        for h in horizontes:
            if i + h < n:
                baseline[h].append((velas[i + h][4] - cierre_i) / cierre_i * 100)

        ventana = velas[i + 1 - ventana_max:i + 1]
        for nombre in senales.detectar(ventana, k):
            for h in horizontes:
                if i + h < n:
                    retorno = (velas[i + h][4] - cierre_i) / cierre_i * 100
                    disparos[nombre][h].append(retorno)

    return disparos, baseline


def _media(xs):
    return sum(xs) / len(xs) if xs else None


def _tasa_acierto(retornos, direccion):
    if not retornos:
        return None
    aciertos = sum(1 for r in retornos if r * direccion > 0)
    return aciertos / len(retornos) * 100


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return
    coin, tf = args[0], args[1]
    resto = args[2:]

    fuente = "historicos"
    dias = None
    horizontes = [5, 15, 30]
    k = 3
    i = 0
    while i < len(resto):
        if resto[i] == "--fuente":
            i += 1; fuente = resto[i]
        elif resto[i] == "--dias":
            i += 1; dias = float(resto[i])
        elif resto[i] == "--horizontes":
            i += 1; horizontes = [int(x) for x in resto[i].split(",")]
        elif resto[i] == "--k":
            i += 1; k = int(resto[i])
        i += 1

    if fuente == "historicos":
        ruta = _localizar_historicos(coin, tf)
        if ruta is None:
            print(f"No hay CSV en historicos/ para {coin.upper()} {tf} "
                  f"(patron esperado: <fecha>_{coin.upper()}_{tf}_binance.csv)")
            return
    elif fuente == "bitget":
        ruta = _archivo_bitget(coin, tf)
        if not os.path.exists(ruta):
            print(f"No hay historico Bitget para {coin.upper()} {tf} en {ruta} - "
                  f"bajalo primero con descargar_bit.py")
            return
    else:
        print("Fuente invalida - usar 'historicos' o 'bitget'")
        return

    print(f"Cargando {ruta}" + (f" (ultimos {dias:.0f} dias)" if dias else " (todo el historico)") + " ...")
    velas = _cargar_velas(ruta, dias)
    if not velas:
        print("No se cargo ninguna vela (¿--dias muy corto?).")
        return
    print(f"{len(velas)} velas: {_fmt_fecha(velas[0][0])} -> {_fmt_fecha(velas[-1][0])}")
    print(f"k={k}  horizontes={horizontes} velas  ventana señales={senales.VENTANA_MAXIMA}\n")

    disparos, baseline = _backtest(velas, k, horizontes)

    baseline_media = {h: _media(baseline[h]) for h in horizontes}

    cab = f"{'señal':<18}{'n':>7}"
    for h in horizontes:
        cab += f"   ret@{h}(%)  acierto%   edge@{h}(%)"
    print(cab)
    print("-" * len(cab))
    for nombre in sorted(DIRECCION_ESPERADA):
        direccion = DIRECCION_ESPERADA[nombre]
        n_disparos = len(disparos[nombre][horizontes[0]]) if horizontes else 0
        fila = f"{nombre:<18}{n_disparos:>7}"
        for h in horizontes:
            retornos = disparos[nombre][h]
            ret_dir = _media([r * direccion for r in retornos]) if retornos else None
            acierto = _tasa_acierto(retornos, direccion)
            # 'edge' compara contra lo que ganaria la MISMA apuesta direccional
            # sobre una vela cualquiera (direccion*baseline), no contra el
            # baseline crudo - si el activo tiene tendencia neta en el periodo,
            # comparar contra el baseline sin ajustar de signo favorece a
            # ciegas a las señales que apuestan a favor de esa tendencia.
            edge = (ret_dir - direccion * baseline_media[h]) if ret_dir is not None else None
            fila += (f"   {ret_dir:>8.3f}  {acierto:>7.1f}  {edge:>10.3f}"
                     if ret_dir is not None else f"   {'--':>8}  {'--':>7}  {'--':>10}")
        print(fila)

    print("\n'ret@N' es el retorno medio en la direccion que la señal anticipa (positivo = a favor).")
    print("'edge@N' es ret@N MENOS lo que ganaria la misma apuesta direccional sobre una vela")
    print("cualquiera (direccion x retorno medio de TODO el historico a esa distancia) - aisla el")
    print("aporte de la señal en si de la tendencia neta del periodo. edge@N ~ 0 = sin borde real.")


if __name__ == "__main__":
    main()
