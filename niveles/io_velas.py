import csv
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIR_NIVELES = Path(__file__).resolve().parent
DIR_NEO = DIR_NIVELES.parent
DIR_VELAS = DIR_NEO / "velas"

TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']

_RE_COIN = re.compile(r"^[A-Za-z0-9]{2,20}$")


def validar_coin(valor):
    if not _RE_COIN.match(valor):
        raise ValueError(f"coin invalido: {valor!r} (solo letras/digitos, 2-20 caracteres)")
    return valor.lower()


def _tf_a_ms(tf):
    if tf.endswith('m'):
        return int(tf[:-1]) * 60 * 1000
    elif tf.endswith('h'):
        return int(tf[:-1]) * 3600 * 1000
    elif tf.endswith('d'):
        return int(tf[:-1]) * 86400 * 1000
    raise ValueError(f"Timeframe invalido: {tf}")


def _ruta_csv(coin, tf, mercado):
    return DIR_VELAS / f"{coin.upper()}" / f"bitget_{coin.upper()}_{tf}_{mercado}.csv"


def _fila_vela(row):
    ts = int(row[0])
    apertura, alto, bajo, cierre = float(row[2]), float(row[3]), float(row[4]), float(row[5])
    volumen = float(row[6])
    if not all(math.isfinite(x) for x in (apertura, alto, bajo, cierre, volumen)):
        raise ValueError("OHLC/volumen no finito")
    if volumen < 0:
        raise ValueError("volumen negativo")
    if alto < max(apertura, cierre) or bajo > min(apertura, cierre) or alto < bajo:
        raise ValueError("OHLC inconsistente")
    return [ts, apertura, alto, bajo, cierre, volumen]


def _parsear(lineas):
    velas = []
    for row in csv.reader(lineas):
        if not row or not row[0].strip().isdigit():
            continue
        try:
            velas.append(_fila_vela(row))
        except (ValueError, IndexError):
            continue
    for i in range(1, len(velas)):
        if velas[i][0] <= velas[i - 1][0]:
            raise ValueError(
                f"velas desordenadas o duplicadas: {velas[i - 1][0]} -> {velas[i][0]}")
    return velas


def _leer_cola(ruta, corte_ms, tf):
    if corte_ms is None:
        with open(ruta, newline='') as f:
            return _parsear(f)

    ahora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    faltan = max(1, (ahora_ms - corte_ms) // _tf_a_ms(tf))
    leer = int(faltan * 70 * 2) + 65536

    while True:
        tam = os.path.getsize(ruta)
        leer = min(tam, leer)
        desde_cero = leer == tam
        with open(ruta, 'rb') as f:
            f.seek(tam - leer)
            crudo = f.read(leer)
        lineas = crudo.decode('utf-8', 'replace').splitlines()
        if not desde_cero:
            lineas = lineas[1:]
        velas = _parsear(lineas)

        if leer >= tam or (velas and velas[0][0] <= corte_ms):
            return [v for v in velas if v[0] >= corte_ms]
        leer = min(tam, leer * 4)


def _cargar_velas(coin, tf, mercado, desde_dias=None):
    ruta = _ruta_csv(coin, tf, mercado)
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No hay velas de {coin.upper()} {tf} {mercado}: {ruta}")

    corte_ms = None
    if desde_dias is not None:
        corte_ms = int((datetime.now(timezone.utc) - timedelta(days=desde_dias)).timestamp() * 1000)

    return _leer_cola(ruta, corte_ms, tf), ruta


class CacheVelas:
    def __init__(self, coin, tf, mercado):
        self.coin = coin
        self.tf = tf
        self.mercado = mercado
        self.velas = []
        self.ruta = _ruta_csv(coin, tf, mercado)
        self._firma = None

    def cambio(self, desde_dias):
        try:
            st = os.stat(self.ruta)
        except OSError:
            return True
        return (st.st_mtime_ns, st.st_size, desde_dias) != self._firma

    def obtener(self, desde_dias):
        try:
            st = os.stat(self.ruta)
            firma = (st.st_mtime_ns, st.st_size, desde_dias)
        except OSError:
            firma = None

        if firma is not None and firma == self._firma:
            return self.velas, False

        self.velas, self.ruta = _cargar_velas(self.coin, self.tf, self.mercado, desde_dias)
        self._firma = firma
        return self.velas, True


def _tfs_disponibles(coin, mercado, solo_tf=None):
    candidatos = [solo_tf] if solo_tf else TIMEFRAMES
    return [tf for tf in candidatos if os.path.exists(_ruta_csv(coin, tf, mercado))]
