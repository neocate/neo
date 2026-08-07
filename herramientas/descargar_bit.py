# ---------------------------------------------------------------
# descargar_bit.py - Baja/actualiza historico de velas de Bitget (futuros) a CSV
#
# Mismo símbolo/mercado que consulta monitor.py en vivo (datos.velas() con
# normalizar_simbolo(coin, "f") -> "<COIN>/USDT:USDT", futuros USDT-M). Menos
# histórico que Binance, pero son las velas EXACTAS que vio el monitor al
# decidir cada operación - para contrastar una sesión real usar este script,
# no descargar_bin.py (Binance es otro exchange, su OHLCV puede diferir por
# tener un volumen de negocio mucho más amplio).
#
# Pagina hasta cubrir el rango pedido (el límite por request lo impone
# Bitget, normalmente menor que el de Binance - por eso el corte de página
# se decide por "no llegaron velas nuevas", no por "llegaron menos de las
# pedidas", que con paginas mas pequeñas cortaria demasiado pronto). Nunca
# incluye la vela EN CURSO (todavía sin cerrar) - solo velas cerradas, igual
# que el resto del sistema (ver anotaciones.md: señales sobre velas[-2]).
#
# El CSV sale en el MISMO formato que descargar_bin.py:
#     timestamp,fecha_utc,open,high,low,close,volumen
# y con el nombre  historico_<COIN>_<TF>_bitget.csv, en herramientas/libro/
# (mismo sitio que flujo_*.csv de grabador_libro.py - todo lo de una sesion
# de captura en vivo junto, para las pruebas en frio despues).
#
# Dos formas de usarlo:
#   descargar(coin, tf, desde=...)  - SIEMPRE reescribe el fichero entero
#                                      desde 'desde' (o todo el historico).
#                                      Uso manual/puntual.
#   actualizar(coin, tf)            - si no hay fichero previo, baja todo
#                                      (como descargar()); si ya existe, lee
#                                      la ultima vela guardada y solo pide/
#                                      AÑADE lo que falta (append, sin
#                                      reescribir) - pensado para refrescar
#                                      seguido (ver grabador_libro.py) sin
#                                      volver a bajar todo cada vez.
#
# Uso:
#   python descargar_bit.py <coin> <timeframe> [desde]
#     coin:       eth, btc, icp, sol...  (o símbolo completo ETH/USDT:USDT)
#     timeframe:  1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...
#     desde:      opcional. 'YYYY-MM-DD'  o  número de días hacia atrás.
#                 Si se omite, baja TODO el histórico disponible (bastante
#                 menos profundo que Binance).
#
# Ejemplos:
#   python descargar_bit.py btc 5m 1
#   python descargar_bit.py eth 15m 2023-01-01
# ---------------------------------------------------------------

import csv
import os
import sys
import time
from datetime import datetime, timezone

import ccxt

DIR_LIBRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libro")


def _simbolo(coin):
    """'eth' -> 'ETH/USDT:USDT' (futuros USDT-M); deja intactos los que ya traen '/'."""
    coin = coin.strip()
    if '/' in coin:
        return coin.upper()
    return f"{coin.upper()}/USDT:USDT"


def _archivo(coin, timeframe):
    os.makedirs(DIR_LIBRO, exist_ok=True)
    nombre = f"historico_{_simbolo(coin).split('/')[0]}_{timeframe}_bitget.csv"
    return os.path.join(DIR_LIBRO, nombre)


def _desde_ms(desde, cliente):
    """Interpreta el arg 'desde': fecha ISO, nº de días, o None (todo)."""
    if desde is None:
        return cliente.parse8601('2019-01-01T00:00:00Z')  # Bitget futuros, margen de sobra
    if '-' in str(desde):
        return cliente.parse8601(f"{desde}T00:00:00Z")
    # número de días hacia atrás
    dias = float(desde)
    return cliente.milliseconds() - int(dias * 86_400_000)


def _hasta_ms_cerrado(cliente, timeframe):
    """Excluye la vela EN CURSO - el corte va justo al inicio de la vela que
    todavia se esta formando."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    ahora = cliente.milliseconds()
    return ahora - (ahora % tf_ms)


def _ultimo_timestamp_ms(ruta):
    """Timestamp (ms) de la ULTIMA fila, leyendo solo los ultimos 64KB (igual
    que descargar_bin.py - no tiene sentido cargar el fichero entero solo
    para saber donde se quedo)."""
    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        cola = f.read()
    lineas = [l for l in cola.split(b'\n') if l.strip()]
    return int(lineas[-1].split(b',')[0])


def _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req=200):
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
            print(f"  {len(velas)} velas... "
                  f"({datetime.fromtimestamp(lote[-1][0]/1000, timezone.utc):%Y-%m-%d})")
        if nuevos == 0:
            break
    velas.sort(key=lambda v: v[0])
    return velas


def _escribir_filas(f, velas):
    w = csv.writer(f)
    for t, o, h, l, c, vol in velas:
        fecha = datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        w.writerow([t, fecha, o, h, l, c, vol])


def descargar(coin, timeframe, desde=None, limite_req=200):
    """Descarga completa (o desde 'desde'), SIEMPRE reescribe el fichero
    entero. Para refrescar sin perder lo ya bajado usar actualizar()."""
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    since = _desde_ms(desde, cliente)
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    print(f"Descargando {simbolo} {timeframe} desde "
          f"{datetime.fromtimestamp(since/1000, timezone.utc):%Y-%m-%d} ...")
    velas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
    if not velas:
        print("No se descargó nada (¿símbolo o timeframe inválido?).")
        return None

    nombre = _archivo(coin, timeframe)
    with open(nombre, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
        _escribir_filas(f, velas)

    print(f"\n[OK] {len(velas)} velas guardadas en {nombre}")
    print(f"     {datetime.fromtimestamp(velas[0][0]/1000, timezone.utc):%Y-%m-%d} "
          f"-> {datetime.fromtimestamp(velas[-1][0]/1000, timezone.utc):%Y-%m-%d}")
    return nombre


def actualizar(coin, timeframe, desde=None, limite_req=200):
    """Si NO hay fichero previo para esta coin/tf: descarga desde 'desde'
    (mismo formato que descargar() - None = todo el historico). Si YA
    existe: ignora 'desde', lee la ultima vela guardada y solo pide/AÑADE
    lo que falta, sin reescribir el fichero entero."""
    ruta = _archivo(coin, timeframe)
    if not os.path.exists(ruta):
        return descargar(coin, timeframe, desde=desde, limite_req=limite_req)

    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    ultimo_ts = _ultimo_timestamp_ms(ruta)
    since = ultimo_ts + tf_ms
    if since >= hasta_ms:
        return ruta  # ya al dia - se llama seguido desde grabador_libro.py

    nuevas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
    if not nuevas:
        return ruta

    with open(ruta, 'a', newline='') as f:
        _escribir_filas(f, nuevas)
    print(f"  [OK] {simbolo} {timeframe}: +{len(nuevas)} velas "
          f"(hasta {datetime.fromtimestamp(nuevas[-1][0]/1000, timezone.utc):%Y-%m-%d %H:%M} UTC)")
    return ruta


def main():
    if len(sys.argv) < 3:
        print("Uso: python descargar_bit.py <coin> <timeframe> [desde]")
        print("  coin:      eth, btc, icp, sol...  (o ETH/USDT:USDT)")
        print("  timeframe: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...")
        print("  desde:     'YYYY-MM-DD' o nº de días atrás (opcional; si no, todo)")
        print("\nEjemplos:")
        print("  python descargar_bit.py btc 5m 1")
        print("  python descargar_bit.py eth 15m 2023-01-01")
        return
    coin = sys.argv[1]
    timeframe = sys.argv[2]
    desde = sys.argv[3] if len(sys.argv) > 3 else None
    descargar(coin, timeframe, desde)


if __name__ == "__main__":
    main()
