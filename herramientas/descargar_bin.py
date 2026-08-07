# ---------------------------------------------------------------
# descargar_bin.py - Baja/actualiza historico de velas de Binance a CSV
#
# Binance tiene mucho más histórico que Bitget (ETH/BTC desde 2017), pero su
# volumen/OHLCV es el de OTRO exchange - para contrastar velas exactas contra
# operaciones reales de monitor.py (que opera en Bitget), usar descargar_bit.py.
# Este script es para backtests largos donde el histórico profundo importa
# más que la fidelidad exacta al venue de ejecución.
#
# Cache PERMANENTE en herramientas/historicos/ (convencion de Fran,
# 2026-08-06, ver anotaciones.md): un fichero por coin/tf con el DIA DE
# CORTE delante del nombre en vez de re-descargar todo cada vez -
#     DD-MM-AA_<COIN>_<TF>_binance.csv   (ej. 05-08-26_ETH_1m_binance.csv)
# Si NO existe fichero para esa coin/tf: descarga TODO el historico
# disponible. Si YA existe: lee la ULTIMA vela guardada (del propio
# contenido, no del nombre) y solo pide lo que falta, lo AÑADE (append, sin
# reescribir el fichero entero) y renombra al nuevo dia de corte. En los dos
# casos corta SIEMPRE en el ultimo dia COMPLETO (hoy-1 UTC a las 00:00) para
# no dejar guardado un dia a medias que habria que re-descargar mañana.
#
# El CSV en si sigue el MISMO formato de siempre:
#     timestamp,fecha_utc,open,high,low,close,volumen
#
# Uso:
#   python descargar_bin.py <coin> <timeframe>
#     coin:       eth, btc, icp, sol...  (o símbolo completo ETH/USDT)
#     timeframe:  1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...
#
# Ejemplos:
#   python descargar_bin.py btc 15m
#   python descargar_bin.py eth 1m
# ---------------------------------------------------------------

import csv
import glob
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import ccxt

DIR_HISTORICOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historicos")


def _simbolo(coin):
    """'eth' -> 'ETH/USDT'; deja intactos los que ya traen '/'."""
    coin = coin.strip()
    if '/' in coin:
        return coin.upper()
    return f"{coin.upper()}/USDT"


def _fecha_str(fecha):
    """date/datetime -> 'DD-MM-AA' (convencion de nombre de fichero)."""
    return fecha.strftime('%d-%m-%y')


def _archivo_existente(coin_code, timeframe):
    """Busca 'DD-MM-AA_<COIN>_<TF>_binance.csv' en herramientas/historicos/.
    El mas reciente por fecha de MODIFICACION si hubiera mas de uno (no
    deberia, pero por si una actualizacion vieja fallo a medio renombrar).
    None si no hay ninguno todavia."""
    patron = os.path.join(DIR_HISTORICOS, f"*_{coin_code}_{timeframe}_binance.csv")
    candidatos = glob.glob(patron)
    if not candidatos:
        return None
    return max(candidatos, key=os.path.getmtime)


def _ultimo_timestamp_ms(ruta):
    """Timestamp (ms) de la ULTIMA fila, leyendo solo los ultimos 64KB - los
    CSV de 1m llegan a cientos de MB, no tiene sentido cargarlos enteros
    solo para saber donde se quedaron."""
    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        cola = f.read()
    lineas = [l for l in cola.split(b'\n') if l.strip()]
    return int(lineas[-1].split(b',')[0])


def _desde_ms_completo(cliente):
    return cliente.parse8601('2017-01-01T00:00:00Z')  # Binance arranca ~2017


def _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req=1000):
    """Pagina fetch_ohlcv desde 'since' hasta 'hasta_ms' (exclusive).
    Devuelve velas ordenadas y sin duplicados."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    velas = []
    vistos = set()
    while since < hasta_ms:
        try:
            lote = cliente.fetch_ohlcv(simbolo, timeframe, since=since, limit=limite_req)
        except ccxt.BaseError as e:
            print(f"  [reintento] {e}")
            time.sleep(2)
            continue
        if not lote:
            break
        nuevos = 0
        for v in lote:
            if v[0] < hasta_ms and v[0] not in vistos:
                vistos.add(v[0])
                velas.append(v)
                nuevos += 1
        since = lote[-1][0] + tf_ms
        if len(velas) % 20000 < limite_req:
            print(f"  {len(velas)} velas nuevas... "
                  f"({datetime.fromtimestamp(lote[-1][0]/1000, timezone.utc):%Y-%m-%d})")
        if len(lote) < limite_req or nuevos == 0:
            break
    velas.sort(key=lambda v: v[0])
    return velas


def _escribir_filas(f, velas):
    w = csv.writer(f)
    for t, o, h, l, c, vol in velas:
        fecha = datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        w.writerow([t, fecha, o, h, l, c, vol])


def actualizar(coin, timeframe, limite_req=1000):
    os.makedirs(DIR_HISTORICOS, exist_ok=True)
    cliente = ccxt.binance({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    coin_code = simbolo.split('/')[0]

    hoy_utc = datetime.now(timezone.utc).date()
    corte_dt = datetime(hoy_utc.year, hoy_utc.month, hoy_utc.day, tzinfo=timezone.utc)
    hasta_ms = int(corte_dt.timestamp() * 1000)  # excluye TODO lo de hoy (dia incompleto)

    existente = _archivo_existente(coin_code, timeframe)

    if existente is None:
        print(f"Sin historico previo de {simbolo} {timeframe} en {DIR_HISTORICOS} - descarga completa.")
        since = _desde_ms_completo(cliente)
        velas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
        if not velas:
            print("No se descargó nada (¿símbolo o timeframe inválido?).")
            return None
        ultimo_dia = datetime.fromtimestamp(velas[-1][0] / 1000, timezone.utc).date()
        ruta = os.path.join(DIR_HISTORICOS, f"{_fecha_str(ultimo_dia)}_{coin_code}_{timeframe}_binance.csv")
        with open(ruta, 'w', newline='') as f:
            csv.writer(f).writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
            _escribir_filas(f, velas)
        print(f"\n[OK] {len(velas)} velas guardadas en {ruta}")
        print(f"     {datetime.fromtimestamp(velas[0][0]/1000, timezone.utc):%Y-%m-%d} -> {ultimo_dia}")
        return ruta

    ultimo_ts = _ultimo_timestamp_ms(existente)
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    since = ultimo_ts + tf_ms
    if since >= hasta_ms:
        print(f"{os.path.basename(existente)} ya esta al dia (ultima vela "
              f"{datetime.fromtimestamp(ultimo_ts/1000, timezone.utc):%Y-%m-%d %H:%M} UTC, "
              f"corte en {hoy_utc - timedelta(days=1)}). Nada que descargar.")
        return existente

    print(f"Actualizando {os.path.basename(existente)} desde "
          f"{datetime.fromtimestamp(since/1000, timezone.utc):%Y-%m-%d %H:%M} UTC "
          f"hasta {hoy_utc - timedelta(days=1)} ...")
    nuevas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
    if not nuevas:
        print("No hay velas nuevas todavia (ya esta al dia).")
        return existente

    with open(existente, 'a', newline='') as f:
        _escribir_filas(f, nuevas)

    ultimo_dia = datetime.fromtimestamp(nuevas[-1][0] / 1000, timezone.utc).date()
    nueva_ruta = os.path.join(DIR_HISTORICOS, f"{_fecha_str(ultimo_dia)}_{coin_code}_{timeframe}_binance.csv")
    if nueva_ruta != existente:
        os.rename(existente, nueva_ruta)
    print(f"\n[OK] +{len(nuevas)} velas añadidas -> {nueva_ruta}")
    return nueva_ruta


def main():
    if len(sys.argv) < 3:
        print("Uso: python descargar_bin.py <coin> <timeframe>")
        print("  coin:      eth, btc, icp, sol...  (o ETH/USDT)")
        print("  timeframe: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...")
        print(f"\nCache en {DIR_HISTORICOS}: si no hay fichero previo para esa")
        print("coin/tf, baja TODO el historico; si ya existe, solo AÑADE lo que")
        print("falte desde la ultima vela guardada. Corta siempre en el ultimo")
        print("dia completo (hoy-1 UTC) y renombra el fichero a ese dia.")
        print("\nEjemplos:")
        print("  python descargar_bin.py btc 15m")
        print("  python descargar_bin.py eth 1m")
        return
    coin = sys.argv[1]
    timeframe = sys.argv[2]
    actualizar(coin, timeframe)


if __name__ == "__main__":
    main()
