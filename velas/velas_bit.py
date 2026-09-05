
import csv
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']
TF_SEGUNDOS = {'1m': 60, '3m': 180, '5m': 300, '15m': 900,
               '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400}

MERCADOS = {
    'spot':    '{}/USDT',
    'futuros': '{}/USDT:USDT',
}

DIR_VELAS = Path(__file__).resolve().parent
DIR_LOCK = DIR_VELAS / 'lock'
DIR_LOG = DIR_VELAS / 'log'

CABECERA = ['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen']

MARGEN_CIERRE = 15
CONFIRMA_SEG = 5

ORIGEN_DIAS = {
    '1m': 7, '3m': 20, '5m': 30, '15m': 90,
    '30m': 180, '1h': 365, '4h': 1095, '1d': None
}

MS_DIA = 86_400_000
MAX_RECIENTE = 1000
MAX_HISTORICO = 200
VENTANA_PETICION_MS = 90 * MS_DIA

LOTE_VELAS = 50_000
REINTENTOS = 5
COLA_BYTES = 65_536

SONDA_ORIGEN = '2017-01-01T00:00:00Z'

_CLIENTE = None
_LOCKS_PROCESO = set()


def _extraer_moneda(coin):
    coin = coin.strip().upper()
    if '/' in coin:
        return coin.split('/')[0]
    if coin.endswith('USDT'):
        return coin[:-4]
    return coin


def _validar_mercado(mercado):
    if mercado not in MERCADOS:
        raise ValueError(
            f"mercado invalido: {mercado!r}. Usa: {', '.join(MERCADOS)}")
    return mercado


def _simbolo(coin, mercado):
    _validar_mercado(mercado)
    return MERCADOS[mercado].format(_extraer_moneda(coin))


def _asegurar_dir(ruta):
    ruta.mkdir(parents=True, exist_ok=True)
    marca = ruta / '.gitkeep'
    if not marca.exists():
        try:
            marca.touch()
        except OSError:
            pass
    return ruta


def _dir_coin(coin):
    return _asegurar_dir(DIR_VELAS / _extraer_moneda(coin))


def _archivo(coin, timeframe, mercado):
    _validar_mercado(mercado)
    return (_dir_coin(coin) /
            f"bitget_{_extraer_moneda(coin)}_{timeframe}_{mercado}.csv")


def _ruta_meta(ruta_csv):
    return Path(ruta_csv).with_suffix('.meta')


def _escribir_meta(ruta_csv, coin, timeframe, mercado):
    datos = {
        'exchange': 'bitget',
        'mercado': mercado,
        'simbolo': _simbolo(coin, mercado),
        'coin': _extraer_moneda(coin),
        'tf': timeframe,
        'creado': f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
    }
    try:
        _ruta_meta(ruta_csv).write_text(
            json.dumps(datos, indent=1) + '\n', encoding='utf-8')
    except OSError as e:
        print(f"  [AVISO] no se pudo escribir el .meta: {e}", flush=True)


def _comprobar_meta(ruta_csv, coin, timeframe, mercado):
    ruta_csv = Path(ruta_csv)
    if not ruta_csv.exists() or ruta_csv.stat().st_size == 0:
        return
    meta = _ruta_meta(ruta_csv)
    if not meta.exists():
        raise RuntimeError(
            f"[META] {ruta_csv.name} no tiene .meta al lado: no hay forma de\n"
            f"       saber de que mercado son sus velas y no se va a tocar.\n"
            f"       Si sabes que es de '{mercado}', crea {meta.name} con:\n"
            f'       {{"exchange": "bitget", "mercado": "{mercado}", '
            f'"coin": "{_extraer_moneda(coin)}", "tf": "{timeframe}"}}')
    try:
        datos = json.loads(meta.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"[META] {meta.name} ilegible: {e}")
    for campo, esperado in (('mercado', mercado),
                            ('coin', _extraer_moneda(coin)),
                            ('tf', timeframe)):
        if datos.get(campo) != esperado:
            raise RuntimeError(
                f"[META] {ruta_csv.name} dice {campo}={datos.get(campo)!r} "
                f"pero este proceso es {campo}={esperado!r}.\n"
                f"       No se escribe nada: mezclar dos series en un CSV lo "
                f"invalida entero y no hay forma de separarlas despues.")


ruta_csv = _archivo


def _ruta_lock(coin, timeframe, mercado):
    _validar_mercado(mercado)
    return (_asegurar_dir(DIR_LOCK) /
            f"{_extraer_moneda(coin)}_{timeframe}_{mercado}.lock")


def _ruta_log(coin, timeframe, mercado):
    _validar_mercado(mercado)
    return (_asegurar_dir(DIR_LOG) /
            f"{_extraer_moneda(coin)}_{timeframe}_{mercado}.log")


def _log(coin, timeframe, mercado, mensaje, consola=True):
    if consola:
        print(f"  {mensaje}", flush=True)
    try:
        with open(_ruta_log(coin, timeframe, mercado), 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {mensaje}\n")
    except OSError as e:
        print(f"  [AVISO] no se pudo escribir el log: {e}", flush=True)


def _cliente():
    global _CLIENTE
    if _CLIENTE is None:
        _CLIENTE = ccxt.bitget({'enableRateLimit': True})
    return _CLIENTE


def _tf_ms(timeframe):
    return TF_SEGUNDOS[timeframe] * 1000


def _fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _pid_vivo(pid):
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        codigo = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(codigo))
        k32.CloseHandle(handle)
        return bool(ok) and codigo.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lock:

    def __init__(self, coin, timeframe, mercado):
        self.timeframe = timeframe
        self.mercado = _validar_mercado(mercado)
        self.clave = (_extraer_moneda(coin), timeframe, mercado)
        self.ruta = _ruta_lock(coin, timeframe, mercado)
        self.propio = False

    def __enter__(self):
        if self.clave in _LOCKS_PROCESO:
            return self
        self._adquirir()
        _LOCKS_PROCESO.add(self.clave)
        self.propio = True
        return self

    def __exit__(self, *exc):
        if self.propio:
            _LOCKS_PROCESO.discard(self.clave)
            self.propio = False
            try:
                self.ruta.unlink()
            except FileNotFoundError:
                pass
        return False

    def _adquirir(self):
        try:
            self._crear()
            return
        except FileExistsError:
            pass
        host_previo, pid_previo = self._leer_marca()
        aqui = platform.node()

        if host_previo is not None and host_previo != aqui:
            raise RuntimeError(
                f"[LOCK] {self.clave[0]} {self.timeframe} {self.mercado} esta tomado "
                f"por OTRA MAQUINA ({host_previo}, PID {pid_previo}).\n"
                f"       Desde aqui no se puede comprobar si sigue vivo.\n"
                f"       Lock: {self.ruta}\n"
                f"       Si es seguro que ya no corre, borra ese fichero a mano."
            )
        if host_previo is None and pid_previo is not None:
            raise RuntimeError(
                f"[LOCK] {self.clave[0]} {self.timeframe} {self.mercado} tiene un "
                f"lock en formato antiguo (PID {pid_previo}, sin maquina).\n"
                f"       No se puede saber si su proceso vive; si no corre nada, "
                f"borralo a mano.\n"
                f"       Lock: {self.ruta}"
            )
        if pid_previo is not None and _pid_vivo(pid_previo):
            raise RuntimeError(
                f"[LOCK] Ya hay un proceso con {self.clave[0]} {self.timeframe} "
                f"{self.mercado} (PID {pid_previo} en {host_previo}).\n"
                f"       Lock: {self.ruta}"
            )
        try:
            self.ruta.unlink()
        except FileNotFoundError:
            pass
        try:
            self._crear()
        except FileExistsError:
            raise RuntimeError(f"[LOCK] Carrera con otro proceso en {self.ruta}")

    def _crear(self):
        fd = os.open(str(self.ruta), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(f"{platform.node()}|{os.getpid()}\n")

    def _leer_marca(self):
        try:
            crudo = self.ruta.read_text(encoding='utf-8').strip().splitlines()[0]
        except (OSError, IndexError):
            return None, None
        if '|' in crudo:
            host, _, pid = crudo.partition('|')
            try:
                return host, int(pid)
            except ValueError:
                return host, None
        try:
            return None, int(crudo)
        except ValueError:
            return None, None


def _ts_actual(timeframe, ahora_ms=None):
    tf_ms = _tf_ms(timeframe)
    ahora_ms = _cliente().milliseconds() if ahora_ms is None else ahora_ms
    return ahora_ms - (ahora_ms % tf_ms)


def _ts_ultima_cerrada(timeframe, ahora_ms=None):
    return _ts_actual(timeframe, ahora_ms) - _tf_ms(timeframe)


def _linea_valida(linea, tf_ms):
    partes = linea.split(',')
    if len(partes) != len(CABECERA):
        return None
    try:
        ts = int(partes[0])
        for p in partes[2:]:
            float(p)
    except ValueError:
        return None
    if ts <= 0 or ts % tf_ms != 0:
        return None
    return ts


def _sanear_cola(ruta, timeframe):
    ruta = Path(ruta)
    if not ruta.exists() or ruta.stat().st_size == 0:
        return None
    tf_ms = _tf_ms(timeframe)

    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        leidos = min(COLA_BYTES, tam)
        f.seek(tam - leidos)
        cola = f.read()

    desde_cero = leidos == tam
    trozos = cola.split(b'\n')
    if not desde_cero:
        trozos = trozos[1:]

    fin = tam
    for i in range(len(trozos) - 1, -1, -1):
        bruto = trozos[i]
        texto = bruto.decode('utf-8', 'replace').strip()
        if texto:
            ts = _linea_valida(texto, tf_ms)
            if ts is not None:
                if fin != tam:
                    with open(ruta, 'r+b') as f:
                        f.truncate(fin)
                    print(f"  [SANEADO] {tam - fin} byte(s) sueltos al final del "
                          f"CSV (transferencia a medias): recortados", flush=True)
                return ts
            if texto.startswith(CABECERA[0]):
                break
        fin -= len(bruto) + 1

    return None


def _lineas_cola(ruta, n_bytes=COLA_BYTES):
    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        leidos = min(n_bytes, tam)
        f.seek(tam - leidos)
        cola = f.read()
    desde_cero = leidos == tam
    trozos = cola.split(b'\n')
    if not desde_cero:
        trozos = trozos[1:]
    lineas = [t.decode('utf-8', 'replace').strip() for t in trozos if t.strip()]
    if lineas and lineas[0].startswith(CABECERA[0]):
        lineas = lineas[1:]
    return lineas, desde_cero


def _primer_ts_guardado(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        return None
    with open(ruta, 'r', encoding='utf-8') as f:
        f.readline()
        linea = f.readline().strip()
    if not linea:
        return None
    try:
        return int(linea.split(',')[0])
    except ValueError:
        return None


def _estado_vela(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        return None
    lineas, _ = _lineas_cola(ruta)
    if not lineas:
        return None
    partes = lineas[-1].split(',')
    if len(partes) != len(CABECERA):
        return None
    try:
        return {'ts': int(partes[0]), 'fecha': partes[1],
                'close': float(partes[5]), 'vol': float(partes[6])}
    except ValueError:
        return None


def _crear_csv(ruta, coin, timeframe, mercado):
    with open(ruta, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f, lineterminator='\n').writerow(CABECERA)
    _escribir_meta(ruta, coin, timeframe, mercado)


def _cerrar_ultima_linea(ruta):
    try:
        with open(ruta, 'r+b') as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b'\n':
                f.write(b'\n')
    except OSError:
        pass


def _anexar(ruta, velas):
    if not velas:
        return 0
    _cerrar_ultima_linea(ruta)
    with open(ruta, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n')
        for t, o, h, l, c, vol in velas:
            w.writerow([t, _fecha(t), o, h, l, c, vol])
    return len(velas)


def _contar_velas(ruta):
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def _dias_ventana_reciente(timeframe):
    mapa = _cliente().options.get('fetchOHLCV', {}).get('maxRecentDaysPerTimeframe', {})
    return mapa.get(timeframe)


def _endpoint(timeframe, since, ahora_ms):
    dias = _dias_ventana_reciente(timeframe)
    if dias is None or since <= ahora_ms - (dias - 1) * MS_DIA:
        return True, MAX_HISTORICO
    tope_ventana = VENTANA_PETICION_MS // _tf_ms(timeframe)
    return False, max(1, min(MAX_RECIENTE, tope_ventana))


def _fetch(simbolo, timeframe, since, limite=None):
    cliente = _cliente()
    historico, limite_auto = _endpoint(timeframe, since, cliente.milliseconds())
    limite = limite_auto if limite is None else min(limite, limite_auto)
    params = {'useHistoryEndpoint': True} if historico else {}
    margen = since - _tf_ms(timeframe)

    for intento in range(1, REINTENTOS + 1):
        try:
            lote = cliente.fetch_ohlcv(simbolo, timeframe, since=margen,
                                       limit=limite, params=dict(params)) or []
            return [v for v in lote if v[0] >= since]
        except ccxt.BaseError as e:
            if intento == REINTENTOS:
                raise
            print(f"  [reintento {intento}/{REINTENTOS}] {e}", flush=True)
            time.sleep(2 * intento)
    return []


def origen_exchange(simbolo, timeframe):
    tf_ms = _tf_ms(timeframe)
    bajo = _cliente().parse8601(SONDA_ORIGEN)
    alto = _cliente().milliseconds() - tf_ms
    mejor = None

    lote = _fetch(simbolo, timeframe, bajo)
    if lote:
        return lote[0][0]
    if not _fetch(simbolo, timeframe, alto):
        return None

    while alto - bajo > tf_ms:
        medio = (bajo + alto) // 2
        lote = _fetch(simbolo, timeframe, medio)
        if lote:
            alto = medio
            mejor = lote[0][0]
        else:
            bajo = medio
    return mejor


def _pedir_rango(simbolo, timeframe, desde_ts, hasta_ts):
    tf_ms = _tf_ms(timeframe)
    vistos = {}
    since = desde_ts
    while since < hasta_ts:
        lote = _fetch(simbolo, timeframe, since)
        if not lote:
            break
        nuevos = 0
        for v in lote:
            if desde_ts <= v[0] < hasta_ts and v[0] not in vistos:
                vistos[v[0]] = v
                nuevos += 1
        avance = lote[-1][0] + tf_ms
        since = avance if avance > since else since + tf_ms
        if nuevos == 0:
            break
    return [vistos[k] for k in sorted(vistos)]


def _validar_lote(coin, simbolo, timeframe, mercado, velas, ts_esperado):
    tf_ms = _tf_ms(timeframe)
    salida = []
    reinicio = False
    descartadas = 0
    esperado = ts_esperado

    for v in velas:
        if esperado is not None and v[0] != esperado:
            if v[0] < esperado:
                continue
            faltan = (v[0] - esperado) // tf_ms
            relleno = _pedir_rango(simbolo, timeframe, esperado, v[0])
            continuo = (len(relleno) == faltan and relleno
                        and relleno[0][0] == esperado
                        and all(relleno[i][0] - relleno[i - 1][0] == tf_ms
                                for i in range(1, len(relleno))))
            if continuo:
                _log(coin, timeframe, mercado,
                     f"[HUECO] {faltan} vela(s) rellenada(s) en {_fecha(esperado)}")
                salida.extend(relleno)
            else:
                _log(coin, timeframe, mercado,
                     f"[HUECO IRRELLENABLE] {faltan} vela(s) que Bitget no tiene "
                     f"desde {_fecha(esperado)}. Sin continuidad los datos no "
                     f"valen: se descarta el historial anterior y se reinicia "
                     f"en {_fecha(v[0])}")
                descartadas += len(salida)
                salida = []
                reinicio = True
        salida.append(v)
        esperado = v[0] + tf_ms

    return salida, reinicio, descartadas


def bajar_por_lotes(coin, timeframe, mercado, destino_ts, origen_ts=None):
    simbolo = _simbolo(coin, mercado)
    tf_ms = _tf_ms(timeframe)
    ruta = _archivo(coin, timeframe, mercado)
    _comprobar_meta(ruta, coin, timeframe, mercado)

    margen_ms = (MARGEN_CIERRE + CONFIRMA_SEG) * 1000
    tope = _cliente().milliseconds() - margen_ms - tf_ms
    tope -= tope % tf_ms
    if destino_ts > tope:
        destino_ts = tope

    ts_ultimo = _sanear_cola(ruta, timeframe)
    if ts_ultimo is None:
        if origen_ts is None:
            origen_ts = origen_exchange(simbolo, timeframe)
            if origen_ts is None:
                _log(coin, timeframe, mercado,
                     f"[AVISO] Bitget no devuelve datos para {simbolo} {timeframe}")
                return 0
            _log(coin, timeframe, mercado,
                 f"[ORIGEN] primer registro del exchange: {_fecha(origen_ts)}")
        _crear_csv(ruta, coin, timeframe, mercado)
        since = origen_ts - (origen_ts % tf_ms)
        esperado = None
    else:
        since = ts_ultimo + tf_ms
        esperado = since

    total = 0
    buffer = []

    def volcar():
        nonlocal total, esperado, buffer
        if not buffer:
            return
        buffer.sort(key=lambda v: v[0])
        velas_ok, reinicio, descartadas = _validar_lote(
            coin, simbolo, timeframe, mercado, buffer, esperado)
        if reinicio:
            previas = _contar_velas(ruta)
            if previas or descartadas:
                _log(coin, timeframe, mercado,
                     f"[DESCARTE] {previas + descartadas:,} vela(s) descartada(s) "
                     f"por falta de continuidad")
            _crear_csv(ruta, coin, timeframe, mercado)
            total = 0
        if velas_ok:
            total += _anexar(ruta, velas_ok)
            esperado = velas_ok[-1][0] + tf_ms
            _log(coin, timeframe, mercado,
                 f"[LOTE] {total:,} vela(s) -> {_fecha(velas_ok[-1][0])}")
        buffer = []

    while since <= destino_ts:
        desde = since
        lote = _fetch(simbolo, timeframe, since)
        if not lote:
            break

        nuevos = 0
        for v in lote:
            if desde <= v[0] <= destino_ts:
                buffer.append(v)
                nuevos += 1

        avance = lote[-1][0] + tf_ms
        since = avance if avance > since else since + tf_ms

        if len(buffer) >= LOTE_VELAS:
            volcar()
        if nuevos == 0:
            break

    volcar()
    return total


def _pedir_sellada(simbolo, timeframe, ts):
    tf_ms = _tf_ms(timeframe)
    primera = _pedir_rango(simbolo, timeframe, ts, ts + tf_ms)
    if not primera or primera[0][0] != ts:
        return None
    time.sleep(CONFIRMA_SEG)
    segunda = _pedir_rango(simbolo, timeframe, ts, ts + tf_ms)
    if not segunda or segunda[0][0] != ts:
        return None
    return primera[0] if primera[0] == segunda[0] else None


def poner_al_dia(coin, timeframe, mercado, origen_ts=None):
    ruta = _archivo(coin, timeframe, mercado)
    simbolo = _simbolo(coin, mercado)
    tf_ms = _tf_ms(timeframe)
    destino = _ts_ultima_cerrada(timeframe)
    _comprobar_meta(ruta, coin, timeframe, mercado)
    ts_ultimo = _sanear_cola(ruta, timeframe)

    if ts_ultimo is not None and ts_ultimo >= destino:
        return 0

    if ts_ultimo is not None and destino - ts_ultimo == tf_ms:
        vela = _pedir_sellada(simbolo, timeframe, destino)
        if vela is None:
            _log(coin, timeframe, mercado,
                 f"[PENDIENTE] la vela {_fecha(destino)} aun no esta sellada")
            return 0
        velas_ok, reinicio, descartadas = _validar_lote(
            coin, simbolo, timeframe, mercado, [vela], destino)
        if reinicio:
            previas = _contar_velas(ruta)
            if previas or descartadas:
                _log(coin, timeframe, mercado,
                     f"[DESCARTE] {previas + descartadas:,} vela(s) descartada(s) "
                     f"por falta de continuidad")
            _crear_csv(ruta, coin, timeframe, mercado)
        if velas_ok:
            _anexar(ruta, velas_ok)
            _log(coin, timeframe, mercado,
                 f"[VELA] {_fecha(velas_ok[0][0])} guardada")
            return len(velas_ok)
        return 0

    return bajar_por_lotes(coin, timeframe, mercado, destino,
                           origen_ts=origen_ts)


def vela_actual(coin, timeframe, mercado):
    _validar_tf(timeframe)
    tf_ms = _tf_ms(timeframe)
    ts = _ts_actual(timeframe)
    for v in _fetch(_simbolo(coin, mercado), timeframe, ts - tf_ms, limite=3):
        if v[0] == ts:
            return {'ts': v[0], 'fecha': _fecha(v[0]), 'open': v[1], 'high': v[2],
                    'low': v[3], 'close': v[4], 'vol': v[5], 'cerrada': False}
    return None


def ultimas_velas(coin, timeframe, mercado, n):
    _validar_tf(timeframe)
    ruta = _archivo(coin, timeframe, mercado)
    if not ruta.exists():
        return []
    n_bytes = max(COLA_BYTES, n * 128)
    while True:
        lineas, desde_cero = _lineas_cola(ruta, n_bytes)
        if len(lineas) >= n or desde_cero:
            break
        n_bytes *= 2
    salida = []
    for linea in lineas[-n:]:
        p = linea.split(',')
        if len(p) != len(CABECERA):
            continue
        try:
            salida.append({'ts': int(p[0]), 'fecha': p[1], 'open': float(p[2]),
                           'high': float(p[3]), 'low': float(p[4]),
                           'close': float(p[5]), 'vol': float(p[6]),
                           'cerrada': True})
        except ValueError:
            continue
    return salida


def _validar_tf(timeframe):
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"TF invalido: {timeframe}. Usa: {','.join(TIMEFRAMES)}")


def resolver_tfs(timeframes_str):
    if not timeframes_str:
        return list(TIMEFRAMES)
    pedidos = [tf.strip() for tf in timeframes_str.split(',') if tf.strip()]
    malos = [tf for tf in pedidos if tf not in TIMEFRAMES]
    if malos:
        raise ValueError(
            f"TF no reconocidos: {', '.join(malos)}. Usa: {','.join(TIMEFRAMES)}")
    return pedidos


def resumen(coin, tfs, mercado):
    print(f"\n[RESUMEN] {_extraer_moneda(coin)} {mercado}:")
    for tf in tfs:
        estado = _estado_vela(_archivo(coin, tf, mercado))
        if estado:
            print(f"  {tf:4} | {estado['fecha']} | close={estado['close']:12.4f} "
                  f"| vol={estado['vol']:14.0f}")
        else:
            print(f"  {tf:4} | [sin datos]")


def parsear_args(argv, banderas=()):
    coin = None
    tfs = None
    opciones = {b: False for b in banderas}
    opciones.setdefault('--help', False)
    sobrantes = []
    for arg in argv:
        if arg in ('-h', '--help'):
            opciones['--help'] = True
            continue
        if arg.startswith('-'):
            if arg in opciones:
                opciones[arg] = True
            else:
                sobrantes.append(arg)
            continue
        if coin is None:
            coin = arg
        elif tfs is None:
            tfs = arg
        else:
            sobrantes.append(arg)
    if sobrantes:
        raise ValueError(
            f"argumentos no reconocidos: {' '.join(sobrantes)}\n"
            f"       banderas validas: {', '.join(banderas) if banderas else 'ninguna'}\n"
            f"       los TF van juntos y con comas: 5m,15m,1h")
    return coin, tfs, opciones
