# ================================================================================
# velas_bit.py
#
# Maquinaria comun de descarga de velas OHLCV de Bitget.
# NO se ejecuta, y NO se importa directamente: es la libreria que hay detras
# de los dos modulos de mercado, que son los que fijan QUE se descarga.
#
#   velas_bit_spot.py      -> ETH/USDT      (mercado al contado)
#   velas_bit_futuros.py   -> ETH/USDT:USDT (perpetuo USDT-M)
#
# Y los frontales, uno por mercado:
#
#   descargar_bit_spot.py / descargar_bit_futuros.py
#       produccion - mantienen el CSV al dia, --loop
#   descargar_hist_bit_spot.py / descargar_hist_bit_futuros.py
#       recoleccion - bajan el historial completo, 2-3 veces
#
# EL MERCADO NUNCA SE DEDUCE. Es un argumento obligatorio de toda la API
# publica de este fichero. La razon esta en el historial: el simbolo 'ETHUSDT'
# es el id de DOS mercados de Bitget a la vez (spot y swap), y ccxt lo resolvia
# al que dijese defaultType, que por defecto es spot. Durante meses se bajaron
# velas de spot creyendo que eran de futuros. Se separan 5 pb, asi que ningun
# control de precios lo habria visto. De ahi que aqui los simbolos sean
# siempre explicitos ('ETH/USDT' vs 'ETH/USDT:USDT'), que el mercado vaya en
# el nombre de CSV, lock y log, y que ademas quede escrito DENTRO del .meta
# que acompaña a cada CSV: el nombre de un fichero se puede equivocar al
# copiarlo entre equipos, el .meta viaja con el y se comprueba antes de
# escribir un solo byte.
#
# Los dos frontales de un mismo mercado comparten CSV, lock y log, asi que se
# bloquean entre si sobre la misma moneda+TF+mercado y conviven sobre TF
# distintos. Spot y futuros no se bloquean entre si: son series distintas.
#
# NOMENCLATURA (la usada en todo el fichero):
#   actual    -> vela EN CURSO, abierta ahora mismo. Nunca se guarda.
#   actual-1  -> ultima vela CERRADA. Es la que debe estar guardada.
#   actual-2  -> la anterior a esa.
#   Al cerrarse una vela todo el indice se desplaza: la que estaba en curso
#   pasa a ser actual-1 (y se guarda), la que era actual-1 pasa a actual-2.
#
# AL DIA  <=>  ultima_guardada == actual-1
#
# ALMACENAMIENTO (relativo a esta carpeta - identico en Windows y Linux).
# La base se resuelve en cada arranque: si mueves o copias el arbol (neo,
# neo_prueba, neo_copia...) se autoajusta.
#   velas/[COIN]/bitget_[COIN]_[TF]_[MERCADO].csv   <- historial (velas cerradas)
#   velas/[COIN]/bitget_[COIN]_[TF]_[MERCADO].meta  <- de que mercado es ese CSV
#   velas/lock/[COIN]_[TF]_[MERCADO].lock  <- un proceso por moneda+TF+mercado
#   velas/log/[COIN]_[TF]_[MERCADO].log    <- append, nunca pisa lo anterior
# Las carpetas se crean solas.
#
# HORAS: todo en UTC, sin excepcion. La columna de fecha del CSV, los logs y
# los relojes de vela. Las velas del exchange se alinean a fronteras UTC (la
# diaria cierra a las 00:00 UTC), asi que cualquier conversion a hora local
# solo añadiria ambiguedad -dos veces al año la hora local se repite o se
# salta- sin ganar nada. El 'timestamp' en ms es la fuente de verdad.
#
# DOS ENDPOINTS (se elige solo, ver _endpoint)
#   Bitget sirve las velas por dos vias y no son intercambiables:
#     candles          1000 velas/peticion, pero solo una ventana reciente
#                      (1m 30 dias, 1h 60, 4h 240, 1d 1440) y rechaza rangos
#                      de mas de 90 dias por peticion.
#     history-candles  200 velas/peticion, historial completo.
#   En produccion interesa el reciente: 5 veces menos peticiones. Para el
#   historial no hay alternativa. Se elige por el 'since' de cada peticion,
#   asi que produccion baja rapido y, si un equipo lleva mas dias parado que
#   la ventana, ese tramo cae solo al historico sin que nadie intervenga.
#
# FLUJO
#   F1 ARRANQUE   lee la ultima guardada; sin CSV, origen = el que pida el
#                 frontal (historial completo, o ultimos N dias).
#   F2 LOTES      lotes verificados EN MEMORIA antes de escribir. Nunca
#                 relee el CSV para validar. Unico modo que aplica F3.
#   F3 HUECOS     rellenable -> se pide y se cose en el lote. No rellenable
#                 -> se DESCARTA lo anterior y el historial arranca despues
#                 del hueco: sin continuidad los datos no valen para
#                 analisis.
#   F4 CONFIRMA   una vela recien cerrada NO esta sellada. Bitget sigue
#                 consolidandola unos segundos: leida a +1s, el 27% de las 3m
#                 y el 80% de las 1d salian con un close que despues cambiaba,
#                 y como el CSV es append-only ese valor falso se quedaba para
#                 siempre. Medido, los cambios se consolidan entre +5 y +8s.
#                 Asi que no se guarda al cerrar: se pide dos veces separadas
#                 CONFIRMA_SEG y solo se guarda si las dos lecturas coinciden.
#                 Si no coinciden no se guarda nada y no pasa nada: la pasada
#                 siguiente la vera como 'falta 1 vela' y la pedira ya sellada.
#   F5 REANUDA    se escribe por lotes ya verificados, asi que un corte deja
#                 el CSV mas corto pero SIEMPRE continuo, y reanudable.
#   F6 LIBRERIA   vela_actual() y ultimas_velas(), sin tocar disco.
# ================================================================================

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

# Los dos mercados, con su plantilla de simbolo ccxt. Explicitos a proposito:
# el id corto ('ETHUSDT') es ambiguo entre ambos y lo resuelve defaultType,
# que es justo como se acabaron bajando velas de spot creyendolas de futuros.
MERCADOS = {
    'spot':    '{}/USDT',        # ETH/USDT
    'futuros': '{}/USDT:USDT',   # ETH/USDT:USDT (perpetuo USDT-M)
}

# Base = carpeta de este fichero, resuelta en cada arranque.
DIR_VELAS = Path(__file__).resolve().parent
DIR_LOCK = DIR_VELAS / 'lock'
DIR_LOG = DIR_VELAS / 'log'

CABECERA = ['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen']

# F4: margen tras el cierre antes de mirar la vela, y separacion entre las dos
# lecturas que tienen que coincidir para darla por sellada. Los 15s son casi
# el doble del peor caso medido (+8,2s); la doble lectura esta ademas para no
# depender de que esa medida siga siendo valida el dia de mañana.
MARGEN_CIERRE = 15       # segundos desde el cierre hasta la primera lectura
CONFIRMA_SEG = 5         # separacion entre las dos lecturas que deben coincidir

# Punto de arranque cuando no hay CSV: ventana en dias que se baja por TF.
# Solo se aplica en el arranque en frio de un equipo nuevo. Una vez que hay
# CSV, descargar_bit continua desde la ultima vela guardada, sin importar esto.
ORIGEN_DIAS = {
    '1m': 7, '3m': 20, '5m': 30, '15m': 90,
    '30m': 180, '1h': 365, '4h': 1095, '1d': None
}

MS_DIA = 86_400_000
MAX_RECIENTE = 1000      # tope de velas/peticion del endpoint reciente
MAX_HISTORICO = 200      # tope de velas/peticion del endpoint historico
VENTANA_PETICION_MS = 90 * MS_DIA   # el endpoint reciente rechaza mas de esto

LOTE_VELAS = 50_000      # velas acumuladas en memoria antes de volcar a disco
REINTENTOS = 5           # reintentos por peticion antes de rendirse
COLA_BYTES = 65_536      # cola del CSV que se lee para saber la ultima vela

# Cota inferior para buscar el origen real del contrato. Pedir por debajo
# del origen devuelve lista vacia (no error), de ahi la biseccion.
SONDA_ORIGEN = '2017-01-01T00:00:00Z'

_CLIENTE = None
_LOCKS_PROCESO = set()   # (COIN, TF) que ya tiene este proceso: lock reentrante


# --------------------------------------------------------------------------
# Nombres, rutas, log y cliente
# --------------------------------------------------------------------------

def _extraer_moneda(coin):
    """'eth' -> 'ETH', 'ETHUSDT' -> 'ETH', 'ETH/USDT:USDT' -> 'ETH'."""
    coin = coin.strip().upper()
    if '/' in coin:
        return coin.split('/')[0]
    if coin.endswith('USDT'):
        return coin[:-4]
    return coin


def _validar_mercado(mercado):
    """El mercado es obligatorio y solo puede ser uno de los dos. Sin defecto
    a proposito: un defecto aqui es exactamente como se cuela el mercado
    equivocado sin que nadie se entere."""
    if mercado not in MERCADOS:
        raise ValueError(
            f"mercado invalido: {mercado!r}. Usa: {', '.join(MERCADOS)}")
    return mercado


def _simbolo(coin, mercado):
    """('eth','spot') -> 'ETH/USDT'; ('eth','futuros') -> 'ETH/USDT:USDT'.

    Simbolo ccxt completo, nunca el id corto: 'ETHUSDT' es el id de los dos
    mercados y ccxt lo desempata con defaultType, que no es de fiar."""
    _validar_mercado(mercado)
    return MERCADOS[mercado].format(_extraer_moneda(coin))


def _asegurar_dir(ruta):
    """Crea la carpeta y deja dentro un .gitkeep.

    Todo lo que guardan estas carpetas (csv, log, lock) esta gitignorado, y
    git no versiona directorios vacios: sin el .gitkeep la estructura no
    llegaria a un equipo nuevo al clonar. El .gitignore tiene la excepcion
    '!.gitkeep' para que sobreviva a los patrones globales.
    """
    ruta.mkdir(parents=True, exist_ok=True)
    marca = ruta / '.gitkeep'
    if not marca.exists():
        try:
            marca.touch()
        except OSError:
            pass
    return ruta


def _dir_coin(coin):
    """velas/[COIN]/ - se crea si no existe."""
    return _asegurar_dir(DIR_VELAS / _extraer_moneda(coin))


def _archivo(coin, timeframe, mercado):
    """Ruta del CSV de historial. Crea la carpeta de la moneda si falta."""
    _validar_mercado(mercado)
    return (_dir_coin(coin) /
            f"bitget_{_extraer_moneda(coin)}_{timeframe}_{mercado}.csv")


def _ruta_meta(ruta_csv):
    """El .meta que acompaña a un CSV: mismo nombre, otra extension."""
    return Path(ruta_csv).with_suffix('.meta')


def _escribir_meta(ruta_csv, coin, timeframe, mercado):
    """Deja al lado del CSV de que mercado son sus velas.

    El nombre del fichero ya lo dice, pero un nombre se equivoca al copiarlo
    entre equipos y nadie se entera: _validar_lote solo mira la continuidad de
    los timestamps, jamas los precios, asi que velas de perp anexadas detras
    de velas de spot pasan todos los controles sin un aviso. Y no vale mirar
    el precio para detectarlo: spot y perp se separan 5 pb.
    """
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
    """Se llama ANTES de escribir nada. Aborta si el CSV no es de este
    mercado, o si no hay forma de saberlo.

    Un CSV sin .meta no se toca: puede ser de cualquiera de los dos mercados
    y no hay manera de averiguarlo mirando los numeros. Si llego por FTP sin
    su .meta, la ausencia ES la alarma - se declara a mano y ya.
    """
    ruta_csv = Path(ruta_csv)
    if not ruta_csv.exists() or ruta_csv.stat().st_size == 0:
        return                                   # CSV nuevo: lo crea _crear_csv
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


# Nombre publico de _archivo: es lo que consumen los ficheros de fuera
# (niveles, monitor...) y no deberian depender de un nombre con guion bajo.
# Que nadie construya la ruta a mano: si cambia el nombre o la carpeta, se
# cambia aqui y ya.
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
    """Escribe en el log de esa moneda+TF+mercado. SIEMPRE en append:
    relanzar el mismo comando mil veces nunca pisa lo anterior.

    La marca de tiempo va en UTC, igual que la columna de fecha del CSV: asi
    una linea del log y una vela se cruzan sin conversiones de por medio.
    """
    if consola:
        print(f"  {mensaje}", flush=True)
    try:
        with open(_ruta_log(coin, timeframe, mercado), 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {mensaje}\n")
    except OSError as e:
        print(f"  [AVISO] no se pudo escribir el log: {e}", flush=True)


def _cliente():
    """Un solo cliente ccxt por proceso (el rate limit se comparte)."""
    global _CLIENTE
    if _CLIENTE is None:
        _CLIENTE = ccxt.bitget({'enableRateLimit': True})
    return _CLIENTE


def _tf_ms(timeframe):
    return TF_SEGUNDOS[timeframe] * 1000


def _fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


# --------------------------------------------------------------------------
# Lock por moneda+TF (atomico, con PID, reentrante dentro del proceso)
# --------------------------------------------------------------------------

def _pid_vivo(pid):
    """True si ese PID sigue corriendo. Multiplataforma sin dependencias.
    OJO: en Windows NO se puede usar os.kill(pid, 0) - alli os.kill llama a
    TerminateProcess y MATARIA el proceso en vez de consultarlo."""
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
        return True    # existe pero es de otro usuario
    return True


class Lock:
    """Un proceso por moneda+TF+mercado. Es el CSV lo que se corrompe, asi que
    esa es la unidad: 'eth 1h spot' y 'eth 4h spot' pueden convivir, y
    'eth 1h spot' con 'eth 1h futuros' tambien -son CSV distintos-, pero dos
    'eth 1h spot' no.

    Sin esto, dos procesos sobre el mismo CSV se destrozan entre si: uno lo
    abre en 'w' (vacia el fichero y escribe desde el principio) mientras el
    otro lo abre en 'a' (añade al final).

    - Creacion ATOMICA con O_CREAT|O_EXCL: el propio SO falla si ya existe,
      no hay hueco entre comprobar y crear.
    - Guarda el PID. Si el lock existe pero su PID ya no vive (kill, corte
      de luz), se pisa solo: no hay que borrarlo a mano.
    - Reentrante dentro del mismo proceso.
    """

    def __init__(self, coin, timeframe, mercado):
        self.timeframe = timeframe
        self.mercado = _validar_mercado(mercado)
        self.clave = (_extraer_moneda(coin), timeframe, mercado)
        self.ruta = _ruta_lock(coin, timeframe, mercado)
        self.propio = False

    def __enter__(self):
        if self.clave in _LOCKS_PROCESO:
            return self                      # ya lo tiene este proceso
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

        # Un PID solo identifica un proceso DENTRO de su maquina. Estos CSV
        # viven en un NAS que se toca desde varios equipos: sin el host,
        # _pid_vivo() consultaba los procesos del equipo LOCAL, daba por muerto
        # un daemon que seguia corriendo en el NAS, borraba su lock y arrancaba
        # encima. Los dos procesos se destrozan el CSV: uno lo abre en 'w' y el
        # otro en 'a'.
        if host_previo is not None and host_previo != aqui:
            raise RuntimeError(
                f"[LOCK] {self.clave[0]} {self.timeframe} {self.mercado} esta tomado "
                f"por OTRA MAQUINA ({host_previo}, PID {pid_previo}).\n"
                f"       Desde aqui no se puede comprobar si sigue vivo.\n"
                f"       Lock: {self.ruta}\n"
                f"       Si es seguro que ya no corre, borra ese fichero a mano."
            )
        if host_previo is None and pid_previo is not None:
            # Formato antiguo, sin host: no se sabe de que maquina salio, asi que
            # no se puede decidir si esta huerfano. Se rechaza en vez de arriesgar
            # el CSV. Desaparece solo al reiniciar los procesos.
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
            self.ruta.unlink()               # lock huerfano: su proceso murio
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
        """(host, pid) del lock. host es None si el fichero trae el formato
        antiguo, que guardaba solo el PID."""
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


# --------------------------------------------------------------------------
# Relojes de vela
# --------------------------------------------------------------------------

def _ts_actual(timeframe, ahora_ms=None):
    """'actual': apertura de la vela EN CURSO."""
    tf_ms = _tf_ms(timeframe)
    ahora_ms = _cliente().milliseconds() if ahora_ms is None else ahora_ms
    return ahora_ms - (ahora_ms % tf_ms)


def _ts_ultima_cerrada(timeframe, ahora_ms=None):
    """'actual-1': ultima vela CERRADA. Es la que debe estar guardada."""
    return _ts_actual(timeframe, ahora_ms) - _tf_ms(timeframe)


# --------------------------------------------------------------------------
# Lectura del CSV (siempre por la cola, nunca entero)
# --------------------------------------------------------------------------

def _linea_valida(linea, tf_ms):
    """Devuelve el ts si la linea es una vela bien formada, None si no.
    Sirve para detectar una ultima linea truncada: un CSV que llega por FTP
    a medias corta por donde sea."""
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
    """Recorta del final del CSV las lineas incompletas o corruptas y
    devuelve el ts de la ultima vela valida (None si no queda ninguna).

    Los CSV se mueven entre equipos por FTP: una transferencia cortada deja
    la ultima linea a medias. Sin esto, el siguiente append se pegaria a esa
    linea rota y el fichero quedaria invalido para siempre.
    """
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
        trozos = trozos[1:]                  # la primera pudo cortarse a medias

    # Posicion final (offset absoluto) de cada trozo, incluyendo su '\n'.
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
                break                        # solo queda la cabecera
        fin -= len(bruto) + 1                # el '\n' que lo separa

    return None


def _lineas_cola(ruta, n_bytes=COLA_BYTES):
    """Lineas completas del final del fichero, sin cabecera."""
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
    """ts de la PRIMERA vela del CSV (la de justo despues de la cabecera).
    Sirve para saber si el historial arranca donde deberia."""
    ruta = Path(ruta)
    if not ruta.exists():
        return None
    with open(ruta, 'r', encoding='utf-8') as f:
        f.readline()                          # cabecera
        linea = f.readline().strip()
    if not linea:
        return None
    try:
        return int(linea.split(',')[0])
    except ValueError:
        return None


def _estado_vela(ruta):
    """Ultima vela guardada: {'ts','fecha','close','vol'} o None."""
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


# --------------------------------------------------------------------------
# Escritura del CSV
# --------------------------------------------------------------------------

def _crear_csv(ruta, coin, timeframe, mercado):
    """Deja el CSV con solo la cabecera, y su .meta al lado. Se usa al empezar
    de cero y al descartar por hueco irrellenable (F3).

    El .meta se reescribe aqui a proposito: si el CSV se vacia, lo que hubiera
    antes deja de contar y el fichero pasa a ser de este mercado sin discusion.
    """
    with open(ruta, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f, lineterminator='\n').writerow(CABECERA)
    _escribir_meta(ruta, coin, timeframe, mercado)


def _cerrar_ultima_linea(ruta):
    """Garantiza que el CSV termina en salto de linea antes de añadir nada.

    Si una escritura se corta (Ctrl+C, kill, corte de luz) el fichero puede
    quedar sin el '\\n' final. El siguiente append se pegaria a esa linea y
    los dos registros quedarian fundidos en uno ilegible - y encima en MEDIO
    del fichero, sepultado bajo las velas que se escriban despues, donde ya
    no lo ve el saneo de la cola. Cuesta leer un byte y elimina esa clase de
    fallo entera: dos anexados no se pueden pegar nunca.
    """
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
    """Añade velas al final del CSV. Se llama SOLO con lotes ya verificados
    como consecutivos, asi que el fichero nunca queda discontinuo."""
    if not velas:
        return 0
    _cerrar_ultima_linea(ruta)
    with open(ruta, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n')
        for t, o, h, l, c, vol in velas:
            w.writerow([t, _fecha(t), o, h, l, c, vol])
    return len(velas)


def _contar_velas(ruta):
    """Velas guardadas (sin cabecera). Solo para el aviso de descarte, que
    es un evento excepcional - por eso puede permitirse recorrer todo."""
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


# --------------------------------------------------------------------------
# Peticiones a la API - eleccion de endpoint
# --------------------------------------------------------------------------

def _dias_ventana_reciente(timeframe):
    """Dias que cubre el endpoint reciente para ese TF. Se lee del propio
    ccxt en vez de hardcodearlo: si actualizas ccxt, se actualiza solo."""
    mapa = _cliente().options.get('fetchOHLCV', {}).get('maxRecentDaysPerTimeframe', {})
    return mapa.get(timeframe)


def _endpoint(timeframe, since, ahora_ms):
    """Decide endpoint y limite para una peticion que empieza en 'since'.
    Devuelve (usar_historico, limite).

    Misma frontera que aplica ccxt internamente. En modo reciente el limite
    se acota ademas a 90 dias de rango, que es el tope duro de ese endpoint
    (con 1d serian 200 velas = 200 dias y responderia error 400); lo que se
    salga de ahi es trabajo del historico.
    """
    dias = _dias_ventana_reciente(timeframe)
    if dias is None or since <= ahora_ms - (dias - 1) * MS_DIA:
        return True, MAX_HISTORICO
    tope_ventana = VENTANA_PETICION_MS // _tf_ms(timeframe)
    return False, max(1, min(MAX_RECIENTE, tope_ventana))


def _fetch(simbolo, timeframe, since, limite=None):
    """Velas desde 'since' INCLUIDA hacia adelante, con reintentos.
    Devuelve [] si el exchange no tiene nada ahi.

    El margen de un TF no es cosmetico: el endpoint historico de Bitget trata
    startTime como EXCLUSIVO. Pidiendo since=2024-06-08 devuelve a partir del
    09, y esa vela se pierde. Al paginar, cada lote se dejaba su primera vela
    y aparecia un hueco de 1 vela cada ~90 dias que no habia forma de
    rellenar (el relleno pedia esa misma vela y volvia a caer en el borde),
    asi que F3 daba el historial por discontinuo y lo descartaba entero.
    Se pide un TF antes y se filtra: el borde nunca cae en la vela que
    interesa, y da igual si el endpoint es inclusivo o exclusivo.
    """
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
    """F1: primer timestamp que el exchange tiene para ese simbolo+TF.

    No hay endpoint que lo diga, y pedir por debajo del origen devuelve
    lista vacia en vez de las primeras velas. Se busca por biseccion entre
    SONDA_ORIGEN (vacio) y ahora (con datos): ~23 peticiones en el peor
    caso, y solo cuando no existe el CSV. None si el exchange no tiene nada.
    """
    tf_ms = _tf_ms(timeframe)
    bajo = _cliente().parse8601(SONDA_ORIGEN)
    alto = _cliente().milliseconds() - tf_ms
    mejor = None

    lote = _fetch(simbolo, timeframe, bajo)
    if lote:
        return lote[0][0]                    # el historial empieza antes de la sonda
    if not _fetch(simbolo, timeframe, alto):
        return None                          # no hay datos ni ahora mismo

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
    """Velas en [desde_ts, hasta_ts) ordenadas y sin duplicados. Se usa para
    rellenar huecos: puede devolver menos de las pedidas."""
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


# --------------------------------------------------------------------------
# F2 + F3: lotes, verificacion y huecos
# --------------------------------------------------------------------------

def _validar_lote(coin, simbolo, timeframe, mercado, velas, ts_esperado):
    """Verifica EN MEMORIA que el lote es consecutivo y empalma con lo ya
    guardado. Devuelve (velas_continuas, reinicio, descartadas_del_lote).

    - Hueco rellenable    -> se pide y se cose dentro del lote.
    - Hueco NO rellenable -> se tira todo lo acumulado hasta ahi y el
      historial arranca en la primera vela posterior al hueco (F3). El
      llamador vacia el CSV cuando 'reinicio' es True.
    """
    tf_ms = _tf_ms(timeframe)
    salida = []
    reinicio = False
    descartadas = 0
    esperado = ts_esperado

    for v in velas:
        if esperado is not None and v[0] != esperado:
            if v[0] < esperado:
                continue                      # duplicado o vela ya guardada
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
    """F2: pide lotes hasta destino_ts (inclusive), verificando cada lote
    antes de escribirlo. Devuelve el numero de velas añadidas.

    Si el CSV existe, continua desde su ultima vela valida y 'origen_ts' se
    ignora. Si no existe, arranca en 'origen_ts'; si tampoco se da, en el
    origen real del contrato.

    Se escribe en append por lotes ya verificados: si el proceso muere, el
    CSV queda mas corto pero SIEMPRE continuo, y reanudable (F5).
    """
    simbolo = _simbolo(coin, mercado)
    tf_ms = _tf_ms(timeframe)
    ruta = _archivo(coin, timeframe, mercado)
    _comprobar_meta(ruta, coin, timeframe, mercado)

    # F4 tambien aqui: el lote no confirma vela por vela, asi que se corta
    # antes de llegar a ninguna que pueda seguir consolidandose. La ultima
    # cerrada se la deja a poner_al_dia, que si la confirma. Sin esto, un
    # equipo que arranca justo al cerrar una vela la escribiria sin sellar y
    # ese valor se quedaria en el CSV para siempre.
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
        """Valida el buffer y lo escribe."""
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

        # 'since' avanza siempre, aunque el exchange devuelva algo raro: sin
        # esto un lote que no avanzase dejaria el bucle girando en el sitio.
        avance = lote[-1][0] + tf_ms
        since = avance if avance > since else since + tf_ms

        if len(buffer) >= LOTE_VELAS:
            volcar()
        if nuevos == 0:
            break

    volcar()
    return total


def _pedir_sellada(simbolo, timeframe, ts):
    """F4: la vela 'ts' solo si ya esta sellada. None si aun se mueve.

    Se pide dos veces separadas CONFIRMA_SEG y se comparan los cinco campos.
    Una vela recien cerrada sigue consolidandose en Bitget unos segundos: si
    las dos lecturas coinciden esta quieta, y si no, no se guarda nada.

    No hay reintento en bucle a proposito. Devolver None deja el CSV como
    estaba, y la pasada siguiente vera 'falta 1 vela' y la pedira otra vez,
    ya con un ciclo entero de antiguedad. Insistir aqui solo serviria para
    bloquear el resto de TF que esperan turno.
    """
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
    """F1: compara la ultima guardada con actual-1 y decide el camino.

      falta 0 velas -> al dia, no hace nada
      falta 1 vela  -> se pide CONFIRMADA (F4), se valida con F3 y se añade.
      faltan mas    -> LOTES (F2), que si aplica F3.

    Devuelve el numero de velas añadidas.
    """
    ruta = _archivo(coin, timeframe, mercado)
    simbolo = _simbolo(coin, mercado)
    tf_ms = _tf_ms(timeframe)
    destino = _ts_ultima_cerrada(timeframe)
    _comprobar_meta(ruta, coin, timeframe, mercado)
    ts_ultimo = _sanear_cola(ruta, timeframe)

    if ts_ultimo is not None and ts_ultimo >= destino:
        return 0                                     # al dia

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


# --------------------------------------------------------------------------
# F6: uso como libreria (no toca disco)
# --------------------------------------------------------------------------

def vela_actual(coin, timeframe, mercado):
    """Vela EN CURSO tal como esta ahora mismo: no la anterior, y no espera
    a que cierre. Una peticion a la API, sin tocar disco.

    OJO: 'cerrada' es False a proposito. Esta vela NO es del historial y no
    debe mezclarse con el (el CSV solo tiene velas cerradas).
    """
    _validar_tf(timeframe)
    tf_ms = _tf_ms(timeframe)
    ts = _ts_actual(timeframe)
    for v in _fetch(_simbolo(coin, mercado), timeframe, ts - tf_ms, limite=3):
        if v[0] == ts:
            return {'ts': v[0], 'fecha': _fecha(v[0]), 'open': v[1], 'high': v[2],
                    'low': v[3], 'close': v[4], 'vol': v[5], 'cerrada': False}
    return None


def ultimas_velas(coin, timeframe, mercado, n):
    """Ultimas n velas CERRADAS, de la mas antigua a la mas reciente, leidas
    de la cola del CSV. Es la via rapida para indicadores: el CSV ya esta
    validado sin huecos. Devuelve [] si no hay CSV, y menos de n si el CSV
    tiene menos.
    """
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


# --------------------------------------------------------------------------
# Utilidades compartidas por los frontales
# --------------------------------------------------------------------------

def _validar_tf(timeframe):
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"TF invalido: {timeframe}. Usa: {','.join(TIMEFRAMES)}")


def resolver_tfs(timeframes_str):
    """'5m,15m,1h' -> ['5m','15m','1h']. None/vacio -> todos.

    Un TF que no existe es un error, no algo que se descarte por lo bajo:
    antes '5m,15min,1h' devolvia ['5m','1h'] y te ibas convencido de estar
    bajando tres TF cuando eran dos.
    """
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
    """(coin, tfs_str, opciones) de la linea de comandos. Las 'banderas'
    son los --flag booleanos que acepta el frontal.

    Lo que no se reconoce ES UN ERROR. Antes se descartaba en silencio, y eso
    convertia una errata en un comando distinto del que pediste sin avisar:
    '--rehacerr' dejaba rehacer=False y en vez de reconstruir el CSV desde el
    origen seguia hacia adelante; '--lop' hacia una pasada y salia en vez de
    quedarse de daemon, y no te enterabas hasta que faltaban velas horas
    despues. Un 'eth 5m 15m' con espacio en vez de coma perdia el 15m.

    Se filtra por '-' y no por '--' porque con un solo guion era peor todavia:
    '-rehacer' no entraba por la rama de banderas y se tomaba como el nombre
    de la moneda.
    """
    coin = None
    tfs = None
    opciones = {b: False for b in banderas}
    sobrantes = []
    for arg in argv:
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
