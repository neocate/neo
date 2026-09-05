# ---------------------------------------------------------------
# flujo.py
#
# Graba el flujo agresor (tape, delta, CVD) con cobertura completa, en vivo y
# reparandose solo. Fusiona lo que antes eran dos programas: la captura en
# directo y el backfill historico. Son el mismo motor -- misma peticion, mismo
# endpoint, misma funcion -- y solo cambia el cuando y el por que, asi que
# separarlos obligaba a mantener dos copias de la misma logica.
#
# Por que existe (y por que libro.py no basta):
#
#   libro.py paginaba los trades con since= (startTime). Bitget IGNORA
#   startTime en /v2/mix/market/fills-history: devuelve siempre los 1000 mas
#   recientes. La paginacion no avanzaba nunca y cada captura se quedaba con la
#   COLA de la ventana. Medido sobre 7 dias: capturaba entre el 17% y el 20%
#   del tape, y el delta salia con el signo cambiado en 4 de 7 dias.
#
#   El endpoint SI respeta endTime. _paseo_atras retrocede fijando endTime en
#   el trade mas antiguo del lote anterior hasta cubrir la ventana entera.
#
#   Validacion sobre 2.18M de trades recuperados:
#     corr(delta, retorno) por ventana:  original +0.088  ->  corregido +0.557
#
# El paseo es obligatorio a CUALQUIER cadencia. A 60s el cap de 1000 sigue
# mordiendo en el 4.97% de las ventanas, con un maximo real de 9718 trades en
# 60 segundos. Una peticion fija sin paseo perderia justo ese 5%, que son las
# rafagas: el mismo sesgo correlacionado con la actividad, pero mas raro y por
# tanto mas dificil de ver. La cadencia decide el coste, no la correccion.
#
#   Coste medido del paseo, por cadencia (semana completa):
#     cadencia   req/ventana(med)   p99   max   req/s
#        60s            1            3     10   0.0179   <- recomendada
#       300s            2           11     22   0.0072
#       900s            4           28     42   0.0060
#   El limite publico de Bitget es 20 req/s: a 60s se usa el 0.1%.
#
# Capa de seguridad por cortes:
#
#   El tape se puede pedir hacia atras durante 7.00 dias exactos (medido por
#   biseccion: responde a -167.91 h, error 40707 desde -168.00 h). Eso convierte
#   una caida en algo reparable en vez de en perdida definitiva.
#
#   No hay fichero de estado: LA REJILLA ES EL ESTADO. Cada ventana escrita deja
#   su fila con ventana_fin_ms; lo que falta en la secuencia es el hueco. Un
#   fichero de control aparte podria desincronizarse de los datos, la rejilla no.
#
#   Los huecos se reparan de MAS VIEJO A MAS NUEVO: un hueco de 6.9 dias caduca
#   en horas, uno de 1 hora tiene una semana de margen.
#
#   La reparacion nunca retrasa la captura viva. Cada ciclo captura primero
#   (1 peticion, ~0.3s) y solo despues gasta en reparar el tiempo que sobra
#   hasta el siguiente ciclo, con tope.
#
# Lo que este programa NO hace, porque el exchange no lo sirve hacia atras:
#   - libro de ordenes: no hay endpoint historico. Irrecuperable.
#   - open interest: fetchOpenInterestHistory = False. Solo snapshot.
#   - long_short_ratio: ventana fija (29 h en 1h, 92 h en 4h).
#   Esos tres solo los tiene libro.py capturando en vivo, y por eso libro.py
#   sigue siendo el unico proceso cuya caida provoca perdida irreversible.
#
#   Las velas OHLCV tampoco: ya las baja velas/descargar_hist_bit_futuros.py.
#
# Autonomo a proposito, igual que libro.py: su unica dependencia es ccxt. Un
# refactor ajeno que le rompiera un import lo dejaria fallando en silencio
# hasta que la ventana de 7 dias se hubiera cerrado.
#
# Uso:
#   python libro/flujo.py [monedas] [opciones]
#   Monedas: btc,eth,sol (default: eth)
#
#   Modo vivo (produccion):
#     --loop            captura continua + reparacion de huecos
#     --cada N          segundos por ventana (default: 60)
#     --reparar-max N   segundos de reparacion por ciclo (default: 20)
#     --auditar-cada N  ciclos entre auditorias de huecos (default: 60)
#
#   Modo reparacion (una pasada y salir):
#     --desde YYYY-MM-DD[THH:MM]   inicio UTC (default: pared -7d)
#     --hasta YYYY-MM-DD[THH:MM]   fin UTC    (default: ahora)
#     --sobrescribir               rehace ventanas que ya existen
#
#   Comunes:
#     --salida RUTA     carpeta destino (default: datos/flujo)
#     --mercado NOMBRE  etiqueta del mercado (default: futuros)
#     --funding         baja tambien el funding historico (~90 dias)
#
# Ejemplos:
#   python libro/flujo.py eth --loop                       # produccion
#   python libro/flujo.py eth --loop --cada 60
#   python libro/flujo.py eth                              # repara 7 dias y sale
#   python libro/flujo.py eth --desde 2026-08-25 --hasta 2026-08-27
#   python libro/flujo.py eth --desde 2026-06-03 --funding
#
# Ficheros de salida (en --salida, rotados por dia UTC):
#
#   flujo_<COIN>_<mercado>_YYYYMMDD.csv
#     Un agregado por ventana. Es lo que consume el analizador.
#     Siempre ordenado y sin duplicados: al escribir se fusiona con lo que ya
#     hubiera y se reescribe el dia entero (max 1440 filas a 60s, es barato).
#
#     cvd se recalcula como suma corrida de delta_vol dentro del dia UTC cada
#     vez que se escribe. Asi una reparacion arregla tambien la cadena de CVD
#     posterior, sin cursores ni session_id que se desincronicen.
#
#     cobertura_pct = (ts_ultimo - ts_primero) / ventana. Por debajo del 95% la
#     fila no es fiable y se avisa en el log.
#
#   trades_<COIN>_<mercado>_YYYYMMDD.csv
#     Detalle de cada trade. Se ANEXA, no se reescribe: son cientos de miles de
#     filas por dia y reescribirlo en cada reparacion costaria decenas de MB de
#     escritura. Consecuencia: tras reparar un hueco antiguo el fichero deja de
#     estar ordenado por tiempo. Quien lo necesite ordenado que ordene por
#     timestamp_exchange_ms al leer.
#
# Idempotente: relanzarlo sobre el mismo rango da el mismo resultado. Sin
# cursor, sin session_id, sin estado que sobreviva al proceso.
#
# Parada segura (libera el lock y cierra los CSV):
#   Ctrl-C, o kill -INT <PID>
#
# ---------------------------------------------------------------

import argparse
import asyncio
import csv
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

import ccxt

# File locking multiplataforma
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

# Encoding UTF-8 para stderr/stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Los logs van en UTC, como los datos. velas_bit.py ya lo hacia asi por un
# motivo bueno: "una linea del log y una vela se cruzan sin conversiones de por
# medio". El resto usaba hora local, asi que correlacionar un incidente entre
# modulos obligaba a sumar o restar horas a ojo -- y en un NAS que ademas se
# toca desde otra maquina, a adivinar de que huso era cada marca.
logging.Formatter.converter = time.gmtime

DIR_BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATOS = os.path.join(DIR_BASE, "datos")
DIR_LOGS = os.path.join(DIR_BASE, "logs")
DIR_SALIDA = os.path.join(DIR_DATOS, "flujo")

CAMPOS_FLUJO = [
    "ventana_fin_ms", "fecha_utc", "coin",
    "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
    "ts_primer_trade", "ts_ultimo_trade", "cobertura_pct",
    "precio_apertura", "precio_cierre", "precio_max", "precio_min", "vwap",
]

CAMPOS_TRADES = [
    "id", "timestamp_exchange_ms", "ventana_fin_ms", "fecha_utc",
    "precio", "volumen", "lado", "coin",
]

CAMPOS_FUNDING = ["timestamp_ms", "fecha_utc", "coin", "funding_rate_pct"]

# fills-history admite hasta 1000 por peticion y el rate limit se cobra por
# peticion, no por tamaño: pedir 1000 sale igual de barato que pedir 500.
TRADES_LIMITE = 1000

# Pared dura del endpoint, con margen para que un rango calculado al arrancar
# no se caiga por el otro lado a mitad de ejecucion.
VENTANA_HISTORICO_DIAS = 7
MARGEN_PARED_MS = 10 * 60 * 1000

# Tope de paginas por tramo. Frena un bucle que no avanza; con actividad normal
# ni se acerca (el maximo medido en una semana fue 42 para una ventana de 900s).
MAX_PAGINAS_TRAMO = 4000

# Aviso por silencio sospechoso. El hueco natural entre trades es una duracion
# ABSOLUTA (p1 medido: ~20 s), no una fraccion de la ventana: un umbral en % que
# funciona a 900 s marca el 33% de las ventanas SANAS a 60 s. Se avisa cuando el
# silencio supera esta tolerancia, con un minimo relativo por si la ventana es
# muy grande.
#   ventana 900s -> tolera 45 s (equivale al 95% de antes)
#   ventana  60s -> tolera 45 s (es decir, casi nunca avisa: p1 real = 66%)
TOLERANCIA_SILENCIO_S = 45.0
TOLERANCIA_SILENCIO_FRAC = 0.05

# Cuantas ventanas seguidas se reparan de una tirada. Un hueco largo se trocea
# para no monopolizar el presupuesto de un solo ciclo.
VENTANAS_POR_TRAMO = 15

# --- WebSocket (fuente primaria en vivo) -------------------------------------
# El flush del WS corre en el hilo del bucle de eventos y la reparacion REST en
# un executor. Ambos hacen leer-modificar-escribir sobre el MISMO CSV del dia:
# sin este lock una pisa a la otra y se pierden ventanas enteras.
_LOCK_ESCRITURA = threading.Lock()

# Margen tras cerrar una ventana antes de volcarla: da tiempo a los trades que
# caen dentro de ella pero llegan con retraso. Lo que aun asi se escape lo
# repone la reparacion REST, que es la red de seguridad.
GRACIA_FLUSH_MS = 3000

# Si el WS no entrega nada en este tiempo se vuelve al bucle igualmente, para
# que el flush y la reparacion sigan corriendo con el mercado parado.
TIMEOUT_WS_S = 20.0


# ---------------------------------------------------------------
# Acceso al exchange (Bitget via ccxt)
#
# Inline y sin depender de mercado/, por el mismo motivo que libro.py.
# Los endpoints que usa son publicos: funciona sin credenciales.
# ---------------------------------------------------------------

_cliente = None


def _ex_cliente():
    global _cliente
    if _cliente is None:
        _cliente = ccxt.bitget({'enableRateLimit': True})
        _cliente.load_markets()
    return _cliente


def _ex_simbolo(coin, mercado="futuros"):
    """'eth' -> 'ETH/USDT:USDT' en futuros, 'ETH/USDT' en spot.

    Antes devolvia SIEMPRE el perpetuo y 'mercado' solo entraba en el nombre
    del fichero: con --mercado spot se grababa libro_ETH_spot_*.csv lleno de
    datos de perpetuo. El fichero mentia sobre su contenido, que es la peor
    clase de bug en un historico que luego se analiza a ciegas.

    En spot no existen open interest, funding ni long/short ratio: esas
    columnas quedan vacias (cada llamada ya tiene su try/except y escribe "").
    """
    return ("{}/USDT".format(coin.upper()) if mercado == "spot"
            else "{}/USDT:USDT".format(coin.upper()))


def _ex_trades_hasta(simbolo, fin_ms=None, limite=TRADES_LIMITE):
    """Los `limite` trades inmediatamente anteriores a fin_ms.

    endTime es el unico eje por el que Bitget deja moverse en el tiempo aqui:
    pasar since/startTime no hace nada, devuelve siempre lo mas reciente."""
    params = {} if fin_ms is None else {'until': int(fin_ms)}
    return _ex_cliente().fetch_trades(simbolo, limit=limite, params=params)


def _ex_funding_historico(simbolo, desde_ms):
    """Funding con la paginacion nativa de ccxt. Sin paginate se queda en 100
    registros; con el, retrocede ~90 dias."""
    return _ex_cliente().fetch_funding_rate_history(
        simbolo, since=int(desde_ms), limit=100, params={'paginate': True}
    )


# ---------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------

def _paseo_atras(simbolo, desde_ms, hasta_ms, logger, etiqueta=""):
    """Recorre [desde_ms, hasta_ms) hacia atras y devuelve los trades unicos.

    Cada lote empieza donde acabo el anterior por abajo, asi que no hay huecos.
    La deduplicacion por id hace falta porque los bordes se solapan: un trade
    con el mismo ts que el corte vuelve a salir en el lote siguiente."""
    vistos = {}
    fin = hasta_ms
    paginas = 0
    fallos = 0

    while paginas < MAX_PAGINAS_TRAMO:
        try:
            lote = _ex_trades_hasta(simbolo, fin_ms=fin)
        except ccxt.BaseError as e:
            texto = str(e)
            if '40707' in texto:
                logger.error(
                    "%spared de los %d dias alcanzada en %s: ese tramo ya no existe"
                    % (etiqueta, VENTANA_HISTORICO_DIAS, _fmt(fin))
                )
                break
            fallos += 1
            if fallos > 5:
                logger.error("%s6 fallos seguidos, abandono el tramo: %s" % (etiqueta, texto[:160]))
                break
            espera = 2 ** fallos
            logger.warning("%sfallo %d (%s), reintento en %ds" % (etiqueta, fallos, texto[:100], espera))
            time.sleep(espera)
            continue

        fallos = 0
        paginas += 1

        if not lote:
            break

        for t in lote:
            if t.get('id') is not None:
                vistos[t['id']] = t

        mas_antiguo = min(t['timestamp'] for t in lote)

        if mas_antiguo <= desde_ms:
            break

        # El cursor tiene que retroceder o el bucle no termina. Pasa si una
        # rafaga de mas de 1000 trades comparte milisegundo: el lote entero cabe
        # en el mismo ts y endTime no puede bajar de ahi por si solo.
        if mas_antiguo >= fin:
            logger.warning(
                "%sel cursor no retrocede en %s (rafaga de >%d trades en el mismo ms), fuerzo 1 ms"
                % (etiqueta, _fmt(mas_antiguo), TRADES_LIMITE)
            )
            fin = mas_antiguo - 1
            continue

        fin = mas_antiguo
    else:
        logger.error(
            "%stope de %d paginas sin cubrir el tramo; queda sin bajar desde %s hasta %s"
            % (etiqueta, MAX_PAGINAS_TRAMO, _fmt(desde_ms), _fmt(fin))
        )

    # Semiabierto [desde_ms, hasta_ms), para que coincida con la convencion de
    # _agregar (fin = ((ts // ventana_ms) + 1) * ventana_ms mete un ts que cae
    # EXACTAMENTE en un multiplo de ventana_ms en la ventana SIGUIENTE, no en
    # la que termina ahi mismo). Con cierre inclusivo en hasta_ms (probado y
    # revertido) ese trade se pedia aqui, _agregar lo colocaba en la ventana de
    # despues, ese ciclo la descartaba por no haber cerrado aun (ver el filtro
    # de _capturar), y el ciclo SIGUIENTE ya no lo volvia a pedir porque su
    # propio desde_ms excluia justo ese valor: se perdia para siempre y sin que
    # _huecos() lo notase, porque la ventana en si SI se escribia -- solo que
    # de menos. Verificado con una simulacion antes de tocarlo otra vez.
    dentro = [t for t in vistos.values() if desde_ms <= t['timestamp'] < hasta_ms]
    dentro.sort(key=lambda t: (t['timestamp'], str(t.get('id'))))
    return dentro, paginas


def _agregar(trades, ventana_ms, coin):
    """Agrupa los trades en la rejilla y devuelve {ventana_fin_ms: fila}.

    La rejilla se ancla a medianoche UTC, igual que la rotacion de ficheros,
    para que dos ejecuciones distintas produzcan siempre los mismos cortes.
    El cvd se deja a 0: lo calcula _fusionar_flujo al escribir, sobre el dia
    entero, para que una reparacion arregle tambien la cadena posterior."""
    cubos = {}
    for t in trades:
        fin = ((t['timestamp'] // ventana_ms) + 1) * ventana_ms
        cubos.setdefault(fin, []).append(t)

    filas = {}
    for fin, lote in cubos.items():
        lote_ordenado = sorted(lote, key=lambda t: t['timestamp'])
        vb = sum(t['amount'] for t in lote if t['side'] == 'buy')
        vs = sum(t['amount'] for t in lote if t['side'] == 'sell')
        marcas = [t['timestamp'] for t in lote]
        precios = [t['price'] for t in lote]
        volumen = vb + vs
        vwap = (sum(t['price'] * t['amount'] for t in lote) / volumen) if volumen > 0 else 0.0

        filas[fin] = {
            "ventana_fin_ms": fin,
            "fecha_utc": _fmt(fin, segundos=True),
            "coin": coin,
            "n_trades": len(lote),
            "vol_buy": round(vb, 6),
            "vol_sell": round(vs, 6),
            "delta_vol": round(vb - vs, 6),
            "cvd": 0.0,
            "ts_primer_trade": min(marcas),
            "ts_ultimo_trade": max(marcas),
            "cobertura_pct": round(100.0 * (max(marcas) - min(marcas)) / ventana_ms, 2),
            "precio_apertura": lote_ordenado[0]['price'],
            "precio_cierre": lote_ordenado[-1]['price'],
            "precio_max": max(precios),
            "precio_min": min(precios),
            "vwap": round(vwap, 4),
        }
    return filas


# ---------------------------------------------------------------
# Rejilla: el estado vive en los propios ficheros
# ---------------------------------------------------------------

def _ruta(salida, prefijo, coin, mercado, ms):
    fecha = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%d")
    return os.path.join(salida, "%s_%s_%s_%s.csv" % (prefijo, coin, mercado, fecha))


def _leer_flujo(ruta):
    """Devuelve {ventana_fin_ms: fila} de un dia ya escrito."""
    if not os.path.exists(ruta):
        return {}
    filas = {}
    with open(ruta, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                filas[int(r["ventana_fin_ms"])] = r
            except (KeyError, ValueError):
                continue
    return filas


def _ventanas_presentes(salida, coin, mercado, desde_ms, hasta_ms):
    """Todas las ventana_fin_ms ya escritas en el rango. Es el unico estado que
    consulta el programa: no hay fichero de control que pueda desincronizarse."""
    presentes = set()
    dia = datetime.fromtimestamp(desde_ms / 1000, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    tope = datetime.fromtimestamp(hasta_ms / 1000, timezone.utc)
    while dia <= tope:
        ruta = _ruta(salida, "flujo", coin, mercado, int(dia.timestamp() * 1000))
        presentes.update(_leer_flujo(ruta).keys())
        dia += timedelta(days=1)
    return set(v for v in presentes if desde_ms < v <= hasta_ms)


def _huecos(salida, coin, mercado, desde_ms, hasta_ms, ventana_ms):
    """Ventanas de la rejilla que faltan, agrupadas en tramos contiguos y
    ordenadas de mas vieja a mas nueva: lo mas antiguo es lo que antes caduca."""
    presentes = _ventanas_presentes(salida, coin, mercado, desde_ms, hasta_ms)
    primera = ((desde_ms // ventana_ms) + 1) * ventana_ms
    esperadas = []
    v = primera
    while v <= hasta_ms:
        esperadas.append(v)
        v += ventana_ms

    faltan = [v for v in esperadas if v not in presentes]
    if not faltan:
        return []

    tramos = []
    ini = prev = faltan[0]
    for v in faltan[1:]:
        if v - prev > ventana_ms or (v - ini) // ventana_ms >= VENTANAS_POR_TRAMO:
            tramos.append((ini, prev))
            ini = v
        prev = v
    tramos.append((ini, prev))
    return tramos


# ---------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------

def _escribir_atomico(ruta, campos, filas):
    """Se genera un .tmp y se renombra, para que una interrupcion no deje un CSV
    a medias que parezca completo."""
    tmp = ruta + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(filas)
    os.replace(tmp, ruta)


def _fusionar_flujo(salida, coin, mercado, nuevas, sobrescribir, logger):
    """Mezcla las filas nuevas con las del dia y reescribe ordenado.

    Recalcula el cvd como suma corrida del delta_vol dentro del dia UTC: asi
    rellenar un hueco corrige tambien todo el cvd posterior de ese dia, sin
    cursores ni cadenas de sesion que puedan quedar descolgadas."""
    por_dia = {}
    for fin, fila in nuevas.items():
        por_dia.setdefault(_ruta(salida, "flujo", coin, mercado, fin - 1), {})[fin] = fila

    escritas = 0
    # El lock protege el leer-modificar-escribir completo, no solo la escritura:
    # dos hilos leyendo el mismo dia antes de que ninguno escriba se pisarian
    # aunque cada escritura por separado fuese atomica.
    with _LOCK_ESCRITURA:
      for ruta, filas_nuevas in por_dia.items():
        existentes = _leer_flujo(ruta)
        for fin, fila in filas_nuevas.items():
            if fin in existentes and not sobrescribir:
                continue
            existentes[fin] = fila
            escritas += 1

        ordenadas = [existentes[k] for k in sorted(existentes)]
        cvd = 0.0
        for fila in ordenadas:
            cvd += float(fila["delta_vol"])
            fila["cvd"] = round(cvd, 6)
        _escribir_atomico(ruta, CAMPOS_FLUJO, ordenadas)

    return escritas


# Cuantos ficheros trades_*.csv distintos se mantienen en cache de claves ya
# escritas. Cada uno puede acumular cientos de miles de tuplas (un dia
# completo a maxima actividad); limitar el numero de ficheros evita que un
# proceso --loop de semanas acumule en memoria dias ya cerrados y olvidados.
_CACHE_CLAVES_MAX_FICHEROS = 4
_claves_por_ruta = OrderedDict()  # ruta -> set de claves ya escritas en ese CSV


def _clave_trade(id_, ts, precio, volumen, lado):
    """El id real del trade es la unica clave que no puede coincidir entre
    dos trades DISTINTOS -- se prefiere siempre que el exchange lo de. Si
    falta (None o vacio), se cae a (timestamp, precio, volumen, lado), que en
    una rafaga que comparte milisegundo, precio Y tamano si puede colisionar
    entre dos trades reales distintos (ver _paseo_atras: 'rafaga de >1000
    trades en el mismo ms'). El prefijo separa los dos espacios de claves para
    que un id que por casualidad coincida con un valor de la tupla no se
    confunda con ella."""
    if id_:
        return ("id", str(id_))
    return ("tpl", int(ts), float(precio), float(volumen), lado)


def _claves_existentes(ruta):
    """Claves (ver _clave_trade) ya presentes en un CSV de trades. Se lee una
    vez por fichero y proceso; de ahi en adelante vive en memoria via
    _claves_cache. Los ficheros escritos antes de que 'id' fuera una columna
    no la tienen: r.get('id') da None y cae sola al fallback por tupla."""
    claves = set()
    if os.path.exists(ruta):
        with open(ruta, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    claves.add(_clave_trade(r.get("id"), r["timestamp_exchange_ms"],
                                             r["precio"], r["volumen"], r["lado"]))
                except (KeyError, ValueError):
                    continue
    return claves


def _claves_cache(ruta):
    if ruta in _claves_por_ruta:
        _claves_por_ruta.move_to_end(ruta)
        return _claves_por_ruta[ruta]
    claves = _claves_existentes(ruta)
    _claves_por_ruta[ruta] = claves
    if len(_claves_por_ruta) > _CACHE_CLAVES_MAX_FICHEROS:
        _claves_por_ruta.popitem(last=False)
    return claves


def _anexar_trades(salida, coin, mercado, trades, ventana_ms):
    """Anexa el detalle. No reescribe: son cientos de miles de filas por dia.
    Tras reparar un hueco antiguo el fichero queda desordenado; quien lo
    necesite en orden que ordene por timestamp_exchange_ms al leer.

    Deduplica por id real del trade (ver _clave_trade) antes de escribir: el
    WS en vivo y la reparacion REST pueden procesar la misma ventana mas de
    una vez (auditoria que corre justo antes de que el WS vuelque, un
    reinicio del proceso, o la visibilidad de escrituras via SMB -- ver
    _tomar_lock), y _paseo_atras solo deduplica DENTRO de una misma llamada,
    no entre llamadas distintas.

    NOTA: un trades_*.csv de HOY ya abierto antes de este cambio no tiene la
    columna 'id' en su cabecera; las filas que se le anexen ahora si la
    llevaran, con lo que ese fichero concreto queda con mas columnas en las
    filas nuevas que en la cabecera vieja. Nada mas en el proyecto lo lee
    todavia, asi que no rompe nada -- pero si te importa la consistencia,
    rota (renombra) el CSV de hoy a mano y deja que se cree uno nuevo."""
    por_dia = {}
    for t in trades:
        fin = ((t['timestamp'] // ventana_ms) + 1) * ventana_ms
        ruta = _ruta(salida, "trades", coin, mercado, t['timestamp'])
        por_dia.setdefault(ruta, []).append({
            "id": t.get("id") or "",
            "timestamp_exchange_ms": t['timestamp'],
            "ventana_fin_ms": fin,
            "fecha_utc": _fmt(t['timestamp'], segundos=True),
            "precio": t['price'],
            "volumen": t['amount'],
            "lado": t['side'],
            "coin": coin,
        })

    with _LOCK_ESCRITURA:
      for ruta, filas in por_dia.items():
        nuevo = not os.path.exists(ruta)
        vistas = _claves_cache(ruta)

        a_escribir = []
        for fila in filas:
            clave = _clave_trade(fila["id"], fila["timestamp_exchange_ms"],
                                  fila["precio"], fila["volumen"], fila["lado"])
            if clave in vistas:
                continue
            vistas.add(clave)
            a_escribir.append(fila)

        if not a_escribir:
            continue

        with open(ruta, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS_TRADES)
            if nuevo:
                w.writeheader()
            w.writerows(a_escribir)


# ---------------------------------------------------------------
# Unidad de trabajo
# ---------------------------------------------------------------

def _capturar(simbolo, coin, mercado, desde_ms, hasta_ms, ventana_ms,
              salida, sobrescribir, logger, etiqueta=""):
    """Baja un tramo, lo agrega y lo escribe. Devuelve (ventanas, trades, peticiones)."""
    trades, paginas = _paseo_atras(simbolo, desde_ms, hasta_ms, logger, etiqueta)
    if not trades:
        return 0, 0, paginas

    filas = _agregar(trades, ventana_ms, coin)
    # La primera y la ultima ventana del tramo pueden estar cortadas por los
    # bordes del propio tramo. Solo se escriben las que caen enteras dentro.
    # (el "+ ventana_ms" que hubo aqui colaba una ventana extra que el tramo
    # siguiente -- o el ciclo en vivo siguiente -- volvia a escribir igual)
    filas = {k: v for k, v in filas.items() if desde_ms < k <= hasta_ms}

    escritas = _fusionar_flujo(salida, coin, mercado, filas, sobrescribir, logger)
    _anexar_trades(salida, coin, mercado, trades, ventana_ms)

    tolerancia = max(TOLERANCIA_SILENCIO_S, ventana_ms / 1000.0 * TOLERANCIA_SILENCIO_FRAC)
    pobres = [f for f in filas.values()
              if (ventana_ms - (f['ts_ultimo_trade'] - f['ts_primer_trade'])) / 1000.0 > tolerancia]
    if pobres:
        logger.warning(
            "%s%d ventanas con mas de %.0fs de silencio: %s"
            % (etiqueta, len(pobres), tolerancia,
               ", ".join(f['fecha_utc'][11:19] for f in sorted(pobres, key=lambda x: x['ventana_fin_ms'])[:6]))
        )

    return escritas, len(trades), paginas


def _reparar_tramos(simbolo, coin, mercado, tramos, ventana_ms, salida,
                    sobrescribir, logger, presupuesto_s=None):
    """Rellena tramos ya localizados y devuelve (repuestas, trades, pendientes).

    Los tramos llegan ordenados de mas viejo a mas nuevo: lo mas antiguo es lo
    que antes caduca. Con presupuesto_s se para al agotarlo y DEVUELVE lo que
    queda, para seguir en el ciclo siguiente sin volver a auditar."""
    t0 = time.time()
    repuestas = trades_tot = 0
    for i, (a, b) in enumerate(tramos):
        if presupuesto_s is not None and time.time() - t0 >= presupuesto_s:
            return repuestas, trades_tot, tramos[i:]
        n, tr, _ = _capturar(simbolo, coin, mercado, a - ventana_ms, b, ventana_ms,
                             salida, sobrescribir, logger,
                             etiqueta="[%s reparar %s] " % (coin, _fmt(a)))
        repuestas += n
        trades_tot += tr
    return repuestas, trades_tot, []


def _reparar(simbolo, coin, mercado, desde_ms, hasta_ms, ventana_ms, salida,
             sobrescribir, logger):
    """Audita y repara de una tirada, sin presupuesto. Modo una-pasada."""
    tramos = _huecos(salida, coin, mercado, desde_ms, hasta_ms, ventana_ms)
    if not tramos:
        return 0, 0
    total = sum((b - a) // ventana_ms + 1 for a, b in tramos)
    logger.info("[%s] %d ventanas a reponer en %d tramos" % (coin, total, len(tramos)))
    rep, tr, _ = _reparar_tramos(simbolo, coin, mercado, tramos, ventana_ms,
                                 salida, sobrescribir, logger)
    return rep, tr


def _funding(simbolo, coin, mercado, desde_ms, salida, logger):
    try:
        serie = _ex_funding_historico(simbolo, desde_ms)
    except Exception as e:
        logger.error("[%s] funding: %s" % (coin, str(e)[:160]))
        return
    if not serie:
        logger.warning("[%s] funding: sin datos" % coin)
        return
    serie.sort(key=lambda x: x['timestamp'])
    ruta = os.path.join(salida, "funding_%s_%s.csv" % (coin, mercado))
    _escribir_atomico(ruta, CAMPOS_FUNDING, [{
        "timestamp_ms": x['timestamp'],
        "fecha_utc": _fmt(x['timestamp'], segundos=True),
        "coin": coin,
        "funding_rate_pct": x['fundingRate'] * 100 if x.get('fundingRate') is not None else "",
    } for x in serie])
    logger.info("[%s] funding: %d puntos, %s -> %s"
                % (coin, len(serie), _fmt(serie[0]['timestamp']), _fmt(serie[-1]['timestamp'])))


# ---------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------

def _fmt(ms, segundos=False):
    patron = "%Y-%m-%d %H:%M:%S" if segundos else "%Y-%m-%d %H:%M"
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime(patron)


def _parsear_fecha(texto, etiqueta):
    for patron in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(texto, patron).replace(tzinfo=timezone.utc)
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    raise SystemExit("--%s '%s' no es una fecha UTC valida (YYYY-MM-DD[THH:MM])" % (etiqueta, texto))


def _configurar_logging(coins, mercado):
    monedas = "-".join(c.upper() for c in coins)
    logger = logging.getLogger("flujo")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        logger.handlers.clear()
    formato = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S')
    # A 60s escribe ~1.440 lineas/dia: sin rotacion serian decenas de MB al
    # ano. 5 MB x 3 backups.
    fh = RotatingFileHandler(os.path.join(DIR_LOGS, "flujo_%s_%s.log" % (monedas, mercado)),
                             maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    fh.setFormatter(formato)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(formato)
    logger.addHandler(sh)
    return logger


def _crear_estructura(salida):
    for directorio in (DIR_DATOS, DIR_LOGS, salida):
        os.makedirs(directorio, exist_ok=True)
        gitkeep = os.path.join(directorio, ".gitkeep")
        if not os.path.exists(gitkeep):
            open(gitkeep, "a").close()


# Cuanto vale una marca del lock antes de considerarla de un proceso muerto.
# Debe superar de sobra el ciclo mas lento (una reparacion larga).
LOCK_VIGENCIA_S = 300


def _tomar_lock(salida, monedas, mercado, logger):
    """Impide dos instancias escribiendo sobre la misma salida.

    Dos mecanismos, porque uno solo no basta:

    1. Lock del SO (fcntl / msvcrt). Fiable ENTRE PROCESOS DE LA MISMA
       PLATAFORMA, pero NO entre ellas: este proyecto corre en el NAS (Linux,
       fcntl) y se toca desde Windows por SMB (msvcrt). Son mecanismos
       distintos que no se ven, asi que un proceso Windows arrancaba encima
       del de Linux sin enterarse -- comprobado en vivo el 2026-09-02.

    2. Marca host|pid|timestamp dentro del propio fichero, que el proceso vivo
       refresca. Si la marca es reciente hay otro corriendo, venga de la
       plataforma que venga. Es lo unico que cruza SMB.
    """
    ruta = os.path.join(salida, ".flujo_%s_%s.lock" % (monedas, mercado))

    try:
        with open(ruta, "r", encoding="utf-8") as f:
            partes = f.read().strip().split("|")
        if len(partes) == 3 and (time.time() - float(partes[2])) < LOCK_VIGENCIA_S:
            logger.error(
                "Ya corre otra instancia sobre %s (host=%s pid=%s, marca de hace "
                "%.0fs). Saliendo." % (salida, partes[0], partes[1],
                                       time.time() - float(partes[2])))
            sys.exit(1)
    except (IOError, OSError, ValueError, IndexError):
        pass  # sin fichero, ilegible o con formato viejo: se toma igualmente

    arch = open(ruta, "w+", encoding="utf-8")
    try:
        if platform.system() == "Windows":
            msvcrt.locking(arch.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(arch.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        logger.error("Otra instancia ya corre sobre %s. Saliendo." % salida)
        arch.close()
        sys.exit(1)
    _refrescar_lock(arch)
    return arch


def _refrescar_lock(arch):
    """Renueva la marca. Si se deja de llamar, a los LOCK_VIGENCIA_S el lock
    se considera huerfano y otro proceso puede tomarlo."""
    try:
        arch.seek(0)
        arch.truncate()
        arch.write("%s|%d|%.0f" % (platform.node(), os.getpid(), time.time()))
        arch.flush()
    except (IOError, OSError):
        pass


def _soltar_lock(arch):
    try:
        if platform.system() == "Windows":
            msvcrt.locking(arch.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(arch.fileno(), fcntl.LOCK_UN)
    except (IOError, OSError):
        pass
    arch.close()


# ---------------------------------------------------------------
# Modos
# ---------------------------------------------------------------

def _modo_vivo(coins, mercado, ventana_ms, salida, cada, reparar_max,
               auditar_cada, con_funding, logger, lock=None):
    simbolos = dict((c, _ex_simbolo(c, mercado)) for c in coins)
    ciclo = 0
    ultima_ventana = {}
    # Cola de huecos por moneda. Auditar (escanear los CSV de 7 dias) es caro y
    # se hace cada --auditar-cada ciclos; reparar es barato y se hace en TODOS
    # los ciclos mientras quede cola. Atar las dos cosas al mismo periodo haria
    # que reponer un corte largo tardase dias en vez de minutos.
    pendientes = dict((c, []) for c in coins)

    logger.info("modo vivo: ventana %ds, reparacion <=%ds/ciclo, auditoria cada %d ciclos"
                % (ventana_ms / 1000, reparar_max, auditar_cada))

    while True:
        t_ciclo = time.time()
        ciclo += 1
        ahora = int(time.time() * 1000)

        for coin in coins:
            # 1) La ventana viva primero: es 1 peticion y es lo que caduca en
            #    frescura. Reparar el pasado puede esperar al hueco de tiempo
            #    que quede despues.
            fin = (ahora // ventana_ms) * ventana_ms
            if ultima_ventana.get(coin) == fin:
                continue
            try:
                n, tr, pags = _capturar(simbolos[coin], coin, mercado,
                                        fin - ventana_ms, fin, ventana_ms,
                                        salida, True, logger, etiqueta="[%s vivo] " % coin)
                ultima_ventana[coin] = fin
                logger.info("[%s vivo] %s | %d trades, %d peticiones" % (coin, _fmt(fin), tr, pags))
            except Exception as e:
                logger.error("[%s vivo] %s" % (coin, str(e)[:200]))

            # 2) Auditar solo de vez en cuando: escanear 7 dias de CSV es caro.
            #    Se reaudita tambien si la cola se vacio, para confirmar.
            if ciclo == 1 or ciclo % auditar_cada == 1 or not pendientes[coin]:
                pared = ahora - VENTANA_HISTORICO_DIAS * 86400000 + MARGEN_PARED_MS
                try:
                    nuevos = _huecos(salida, coin, mercado, pared, fin - ventana_ms, ventana_ms)
                except Exception as e:
                    logger.error("[%s auditar] %s" % (coin, str(e)[:200]))
                    nuevos = []
                if nuevos and nuevos != pendientes[coin]:
                    total = sum((b - a) // ventana_ms + 1 for a, b in nuevos)
                    logger.info("[%s] auditoria: %d ventanas a reponer en %d tramos"
                                % (coin, total, len(nuevos)))
                pendientes[coin] = nuevos

            # 3) Reparar en CADA ciclo mientras quede cola, con lo que sobre del
            #    ciclo. La captura viva ya esta hecha: el pasado tiene dias de
            #    margen, el momento no.
            if pendientes[coin]:
                resto = cada - (time.time() - t_ciclo)
                presupuesto = max(0.0, min(reparar_max, resto - 1.0))
                if presupuesto > 0:
                    try:
                        rep, _, queda = _reparar_tramos(
                            simbolos[coin], coin, mercado, pendientes[coin], ventana_ms,
                            salida, False, logger, presupuesto_s=presupuesto)
                        pendientes[coin] = queda
                        if rep:
                            faltan = sum((b - a) // ventana_ms + 1 for a, b in queda)
                            logger.info("[%s] %d ventanas repuestas, %d pendientes"
                                        % (coin, rep, faltan))
                    except Exception as e:
                        logger.error("[%s reparar] %s" % (coin, str(e)[:200]))

            if con_funding and (ciclo % auditar_cada == 1 or ciclo == 1):
                _funding(simbolos[coin], coin, mercado,
                         ahora - 90 * 86400000, salida, logger)

        if lock is not None:
            _refrescar_lock(lock)

        dormir = cada - (time.time() - t_ciclo)
        if dormir > 0:
            time.sleep(dormir)
        else:
            logger.warning("ciclo %d tardo %.1fs, mas que la cadencia de %.0fs"
                           % (ciclo, time.time() - t_ciclo, cada))


def _parche_dns():
    """aiohttp elige AsyncResolver (aiodns) si esta instalado, y en Windows
    falla con 'Could not contact DNS servers' aunque el cliente sincrono
    resuelva sin problema. Hay que parchear aiohttp.connector, no
    aiohttp.resolver: connector.py importa el simbolo al cargar el modulo, asi
    que tocar el segundo no tiene ningun efecto.

    Portable: en Linux el ThreadedResolver funciona igual, no hace falta
    condicionar por plataforma."""
    try:
        import aiohttp.connector as _c
        from aiohttp.resolver import ThreadedResolver
        _c.DefaultResolver = ThreadedResolver
    except ImportError:
        pass


def _volcar_cerradas(buffers, simbolo_coin, ventana_ms, ahora_ms, mercado,
                     salida, logger, forzar=False):
    """Escribe las ventanas ya cerradas que hay en el buffer y las saca de el.

    Una ventana se considera cerrada cuando ha pasado GRACIA_FLUSH_MS desde su
    fin: el WS puede entregar con retraso un trade cuyo timestamp cae dentro.
    Lo que aun asi llegue tarde lo repone la reparacion REST."""
    limite = ahora_ms if forzar else ahora_ms - GRACIA_FLUSH_MS
    tope = (limite // ventana_ms) * ventana_ms
    total = 0
    for coin, buf in buffers.items():
        if not buf:
            continue
        cerradas = {}
        for tid in list(buf):
            t = buf[tid]
            fin = ((t["timestamp"] // ventana_ms) + 1) * ventana_ms
            if fin <= tope:
                cerradas.setdefault(fin, []).append(t)
                del buf[tid]
        if not cerradas:
            continue
        trades = sorted((t for lote in cerradas.values() for t in lote),
                        key=lambda x: (x["timestamp"], str(x.get("id"))))
        filas = _agregar(trades, ventana_ms, coin)
        _fusionar_flujo(salida, coin, mercado, filas, True, logger)
        _anexar_trades(salida, coin, mercado, trades, ventana_ms)
        total += len(filas)
        logger.info("[%s ws] %s | %d ventanas, %d trades"
                    % (coin, _fmt(max(cerradas)), len(filas), len(trades)))
    return total


async def _bucle_ws(coins, mercado, ventana_ms, salida, reparar_max,
                    auditar_cada, logger, lock=None):
    """WebSocket como fuente primaria; REST solo para auditar y reponer.

    El WS no puede reponer lo que se perdio mientras estaba caido: por eso la
    reparacion por endTime sigue aqui, corriendo en un executor para no frenar
    el consumo de trades. Es lo que convierte una desconexion en algo
    reparable en vez de en un hueco definitivo."""
    _parche_dns()
    try:
        import ccxt.pro as ccxtpro
    except ImportError:
        raise SystemExit("ccxt.pro no disponible: hace falta ccxt >= 4 para --ws")

    ex = ccxtpro.bitget({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    await ex.load_markets()
    simbolos = dict((c, _ex_simbolo(c, mercado)) for c in coins)
    por_simbolo = dict((v, k) for k, v in simbolos.items())
    buffers = dict((c, {}) for c in coins)
    pendientes = dict((c, []) for c in coins)
    tarea_rep = None
    ciclo = 0
    # get_running_loop y no get_event_loop: el segundo dejo de crear bucle
    # implicito en 3.12+. Y run_in_executor y no asyncio.to_thread, que es 3.9+
    # mientras que el venv del servidor es 3.8. Ambas valen en 3.8 y en 3.14.
    loop = asyncio.get_running_loop()

    logger.info("modo vivo WS: ventana %ds, reparacion <=%ds, auditoria cada %d vueltas"
                % (ventana_ms / 1000, reparar_max, auditar_cada))

    def _reponer(coin):
        """Corre en un hilo aparte: REST sincrono, no toca el bucle de eventos.
        Devuelve (coin, ventanas_repuestas) para que quien lo lance sepa de
        que moneda era el resultado."""
        ahora = int(time.time() * 1000)
        pared = ahora - VENTANA_HISTORICO_DIAS * 86400000 + MARGEN_PARED_MS
        tope = (ahora // ventana_ms) * ventana_ms - ventana_ms
        if not pendientes[coin]:
            pendientes[coin] = _huecos(salida, coin, mercado, pared, tope, ventana_ms)
            if pendientes[coin]:
                n = sum((b - a) // ventana_ms + 1 for a, b in pendientes[coin])
                logger.info("[%s] auditoria: %d ventanas a reponer en %d tramos"
                            % (coin, n, len(pendientes[coin])))
        if not pendientes[coin]:
            return coin, 0
        rep, _, queda = _reparar_tramos(simbolos[coin], coin, mercado, pendientes[coin],
                                        ventana_ms, salida, False, logger,
                                        presupuesto_s=reparar_max)
        pendientes[coin] = queda
        return coin, rep

    try:
        while True:
            ciclo += 1
            try:
                trades = await asyncio.wait_for(
                    ex.watch_trades_for_symbols(list(simbolos.values())),
                    timeout=TIMEOUT_WS_S)
                for t in trades:
                    coin = por_simbolo.get(t.get("symbol"))
                    if coin and t.get("id") is not None:
                        buffers[coin][t["id"]] = t
            except asyncio.TimeoutError:
                # Mercado parado o WS mudo: no es un error, se sigue para que
                # el volcado y la reparacion no se queden bloqueados.
                pass
            except Exception as e:
                logger.error("ws: %s" % str(e)[:200])
                await asyncio.sleep(5)
                continue

            _volcar_cerradas(buffers, por_simbolo, ventana_ms,
                             int(time.time() * 1000), mercado, salida, logger)
            if lock is not None:
                _refrescar_lock(lock)

            # La reparacion va en segundo plano y de una en una: si ya hay una
            # corriendo no se lanza otra, para no duplicar peticiones ni pelear
            # por el lock de escritura. Se ROTA entre monedas: antes se pasaba
            # siempre coins[0] y con dos o mas monedas las demas no se reparaban
            # nunca en modo --ws.
            if (tarea_rep is None or tarea_rep.done()) and ciclo % auditar_cada == 1:
                if tarea_rep is not None and tarea_rep.done():
                    try:
                        rep_coin, n = tarea_rep.result()
                        if n:
                            logger.info("[%s] %d ventanas repuestas" % (rep_coin, n))
                    except Exception as e:
                        logger.error("reparar: %s" % str(e)[:200])
                objetivo = coins[(ciclo // max(auditar_cada, 1)) % len(coins)]
                tarea_rep = loop.run_in_executor(None, _reponer, objetivo)
    finally:
        # Al salir se vuelca lo que quede: si no, el ultimo minuto se perderia
        # hasta que la reparacion lo detectase.
        try:
            _volcar_cerradas(buffers, por_simbolo, ventana_ms,
                             int(time.time() * 1000), mercado, salida, logger, forzar=True)
        finally:
            await ex.close()


def _modo_vivo_ws(coins, mercado, ventana_ms, salida, reparar_max, auditar_cada,
                  logger, lock=None):
    asyncio.run(
        _bucle_ws(coins, mercado, ventana_ms, salida, reparar_max, auditar_cada,
                  logger, lock))


def _modo_reparacion(coins, mercado, desde_ms, hasta_ms, ventana_ms, salida,
                     sobrescribir, con_funding, logger):
    for coin in coins:
        simbolo = _ex_simbolo(coin, mercado)
        t0 = time.time()
        rep, tr = _reparar(simbolo, coin, mercado, desde_ms, hasta_ms, ventana_ms,
                           salida, sobrescribir, logger)
        if rep:
            logger.info("[%s] %d ventanas, %d trades, %.0fs" % (coin, rep, tr, time.time() - t0))
        else:
            logger.info("[%s] sin huecos en %s -> %s" % (coin, _fmt(desde_ms), _fmt(hasta_ms)))
        if con_funding:
            _funding(simbolo, coin, mercado, desde_ms, salida, logger)


def main():
    p = argparse.ArgumentParser(
        description="Flujo agresor de Bitget: captura en vivo con reparacion automatica de cortes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("monedas", nargs="?", default="eth", help="monedas separadas por coma (default: eth)")
    p.add_argument("--loop", action="store_true", help="modo vivo continuo")
    p.add_argument("--ws", action="store_true",
                   help="en modo vivo, usar WebSocket como fuente primaria "
                        "(REST queda solo para auditar y reponer huecos)")
    p.add_argument("--cada", type=float, default=60.0, help="segundos por ventana (default: 60)")
    p.add_argument("--reparar-max", type=float, default=20.0,
                   help="segundos de reparacion por ciclo en modo vivo (default: 20)")
    p.add_argument("--auditar-cada", type=int, default=60,
                   help="ciclos entre auditorias de huecos (default: 60)")
    p.add_argument("--desde", help="inicio UTC YYYY-MM-DD[THH:MM] (default: pared -7d)")
    p.add_argument("--hasta", help="fin UTC YYYY-MM-DD[THH:MM] (default: ahora)")
    p.add_argument("--salida", default=DIR_SALIDA, help="carpeta destino (default: datos/flujo)")
    p.add_argument("--mercado", default="futuros", help="etiqueta de mercado (default: futuros)")
    p.add_argument("--funding", action="store_true", help="baja tambien el funding historico")
    p.add_argument("--sobrescribir", action="store_true", help="rehace ventanas que ya existen")
    args = p.parse_args()

    coins = [c.strip().upper() for c in args.monedas.split(",") if c.strip()]
    if not coins:
        raise SystemExit("no se indico ninguna moneda")
    if args.cada <= 0:
        raise SystemExit("--cada debe ser mayor que 0")
    if args.auditar_cada < 1:
        raise SystemExit("--auditar-cada debe ser 1 o mas")

    _crear_estructura(args.salida)
    logger = _configurar_logging(coins, args.mercado)
    lock = _tomar_lock(args.salida, "-".join(coins), args.mercado, logger)

    ventana_ms = int(args.cada * 1000)
    ahora = int(time.time() * 1000)
    pared = ahora - VENTANA_HISTORICO_DIAS * 86400000 + MARGEN_PARED_MS

    try:
        if args.loop:
            if args.desde or args.hasta:
                logger.warning("--desde/--hasta se ignoran en modo --loop")
            logger.info("flujo %s %s | ventana %.0fs | fuente %s | salida %s"
                        % (",".join(coins), args.mercado, args.cada,
                           "WebSocket (REST repara)" if args.ws else "REST", args.salida))
            logger.info("caduca lo anterior a %s"
                        % _fmt(ahora - VENTANA_HISTORICO_DIAS * 86400000))
            if args.ws:
                # El WS no espacia por --cada: entrega segun llega. --cada solo
                # define el ancho de la ventana de agregado.
                _modo_vivo_ws(coins, args.mercado, ventana_ms, args.salida,
                              args.reparar_max, args.auditar_cada, logger, lock)
            else:
                _modo_vivo(coins, args.mercado, ventana_ms, args.salida, args.cada,
                           args.reparar_max, args.auditar_cada, args.funding, logger, lock)
        else:
            hasta_ms = _parsear_fecha(args.hasta, "hasta") if args.hasta else ahora
            desde_ms = _parsear_fecha(args.desde, "desde") if args.desde else pared
            # La ventana en curso todavia no ha cerrado: escribirla la dejaria
            # incompleta y ya presente en la rejilla, asi que nadie volveria a
            # repararla. Se recorta al ultimo cierre de ventana.
            ultimo_cierre = (ahora // ventana_ms) * ventana_ms
            if hasta_ms > ultimo_cierre:
                if hasta_ms > ahora:
                    logger.warning("--hasta %s es futuro" % _fmt(hasta_ms))
                hasta_ms = ultimo_cierre
            if desde_ms < pared:
                logger.warning(
                    "--desde %s cae fuera de los %d dias que sirve el exchange; se recorta a %s"
                    % (_fmt(desde_ms), VENTANA_HISTORICO_DIAS, _fmt(pared))
                )
                desde_ms = pared
            if desde_ms >= hasta_ms:
                raise SystemExit(
                    "no queda rango que bajar (%s -> %s): el tramo pedido ya caduco"
                    % (_fmt(desde_ms), _fmt(hasta_ms))
                )
            logger.info("flujo %s %s | %s -> %s (%.1f h) | ventana %.0fs | salida %s"
                        % (",".join(coins), args.mercado, _fmt(desde_ms), _fmt(hasta_ms),
                           (hasta_ms - desde_ms) / 3600000, args.cada, args.salida))
            _modo_reparacion(coins, args.mercado, desde_ms, hasta_ms, ventana_ms,
                             args.salida, args.sobrescribir, args.funding, logger)
    finally:
        _soltar_lock(lock)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrumpido")
        sys.exit(130)
