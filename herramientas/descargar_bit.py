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
# Tres formas de usarlo:
#   descargar(coin, tf, desde=...)  - SIEMPRE reescribe el fichero entero
#                                      desde 'desde' (o todo el historico).
#                                      Uso manual/puntual.
#   actualizar(coin, tf)            - si no hay fichero previo, baja
#                                      exactamente las velas de 'dias_objetivo'
#                                      dias (no de mas); si ya existe, lee la ultima
#                                      vela guardada y solo pide/AÑADE lo que
#                                      falta (append, sin reescribir), y
#                                      recorta el fichero si se pasa del cap
#                                      por margen de holgura. Pensado para
#                                      refrescar seguido sin bajar todo cada
#                                      vez.
#   --feed                          - modo daemon: corre sin parar en el NAS
#                                      (independiente de grabador_libro.py,
#                                      que solo graba libro/OI/funding/CVD -
#                                      ver su cabecera) llamando a
#                                      actualizar() por cada coin/tf cada
#                                      '--cada' segundos. Unico dueño de
#                                      mantener al dia herramientas/libro/
#                                      historico_<COIN>_<TF>_bitget.csv - el
#                                      resto de modulos (monitor_senales.py,
#                                      backtest_senales.py) son lectores puros.
#   --velas <coin[,c2,...]> [tf]    - historico PERMANENTE (sin cap de dias,
#     [--cada segundos]               a diferencia de actualizar()/--feed) en
#                                      herramientas/velas/<COIN>/<TF>_bitget.csv
#                                      - lector unico: niveles.py
#                                      (--actualizar). Sin fichero previo
#                                      descarga TODO el historico disponible;
#                                      si ya existe, solo AÑADE lo nuevo
#                                      (append, nunca recorta) - mismo modelo
#                                      de cache permanente que
#                                      descargar_bin.py, pero sobre Bitget/
#                                      futuros (ver arriba: los niveles se
#                                      vigilan contra el precio de Bitget, no
#                                      Binance). Sin 'tf' hace las 7 de
#                                      TIMEFRAMES_NIVELES seguidas.
#                                      Sin --cada: de un tiro (Linux o
#                                      Windows, sin diferencia - no toca nada
#                                      especifico de plataforma), se lanza a
#                                      mano cada vez que se quiera refrescar.
#                                      Con --cada: modo daemon (2026-08-14,
#                                      mismo patron que --feed) - mantiene
#                                      herramientas/velas/ siempre al dia sin
#                                      relanzar a mano. actualizar_velas() ya
#                                      hace su propio lock por fichero, asi
#                                      que niveles.py --actualizar
#                                      puede seguir llamando a --velas de un
#                                      tiro por su cuenta sin pisarse con
#                                      este daemon si hace falta un refresco
#                                      instantaneo entre medias.
#
# El cap de velas guardadas se calcula por DIAS_OBJETIVO (90 dias, igual
# para todos los TF), no por un numero fijo de velas. Se probo lo contrario
# primero (VELAS_OBJETIVO=500 velas flat, "un nivel de hace 90 dias en 1m
# ya esta roto o irrelevante") y se revirtio el 2026-08-11: al re-validar
# el backtest de senales.REFINADAS_CONFIRMADAS con la profundidad real que
# eso daba en vivo (~21d en 1h, ~5d en 15m en vez de 90), el edge se
# rompia de verdad (ruptura_baja_en_soporte pasaba a negativo en 15m) -
# ver mirar.md. Los 90 dias SI hacen falta, la intuicion de "irrelevante
# en TF finos" no se sostuvo con datos. max() contra senales.VENTANA_MAXIMA
# por si 90 dias dieran menos velas que eso (le pasa a 1d: 90 dias = 90
# velas, menos que las 500 que pediria un --tf-macro 1d).
#
# Uso:
#   python descargar_bit.py <coin> <timeframe> [desde]
#     coin:       eth, btc, icp, sol...  (o símbolo completo ETH/USDT:USDT)
#     timeframe:  1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...
#     desde:      opcional. 'YYYY-MM-DD'  o  número de días hacia atrás.
#                 Si se omite, baja TODO el histórico disponible (bastante
#                 menos profundo que Binance).
#   python descargar_bit.py --feed [coin[,coin2,...]] [--tfs 1m,5m,15m,30m,1h,4h,1d]
#                            [--dias-objetivo 90] [--cada 60]
#   python descargar_bit.py --velas <coin[,coin2,...]> [tf] [--cada segundos]
#
# Ejemplos:
#   python descargar_bit.py btc 5m 1
#   python descargar_bit.py eth 15m 2023-01-01
#   python descargar_bit.py --feed btc,eth --cada 60
#   python descargar_bit.py --velas btc          (las 7 TIMEFRAMES_NIVELES, de un tiro)
#   python descargar_bit.py --velas btc 1h        (solo 1h, de un tiro)
#   python descargar_bit.py --velas btc,eth --cada 60   (daemon, todas las TF, dos monedas)
# ---------------------------------------------------------------

import csv
import math
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import ccxt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado.senales import VENTANA_MAXIMA
from mercado import datos

DIR_LIBRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libro")
DIR_VELAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "velas")

DIAS_OBJETIVO = 90.0  # confirmado por backtest (2026-08-11, ver mirar.md) -
                       # menos dias rompe el edge de REFINADAS_CONFIRMADAS.

TIMEFRAMES_FEED = ["1m","3m","5m", "15m", "30m", "1h", "4h", "1d"]

# TFs que usa niveles.py via --velas (2026-08-14) - mismo criterio
# que TIMEFRAMES_FEED pero sin 30m (niveles.py/monitor_niveles.py
# nunca lo piden, ni como TF principal ni como --tf-macro).
TIMEFRAMES_NIVELES = ["1m", "3m", "5m", "15m", "1h", "4h", "1d"]


def _simbolo(coin):
    """'eth' -> 'ETH/USDT:USDT' (futuros USDT-M); deja intactos los que ya
    traen '/'. Delega en datos.normalizar_simbolo(coin, "f") - antes esto
    reimplementaba la misma conversion a mano, con riesgo de que las dos
    copias divergieran silenciosamente si cambiaba la convencion de
    simbolos de Bitget."""
    return datos.normalizar_simbolo(coin.strip(), "f")[0]


def _archivo(coin, timeframe):
    os.makedirs(DIR_LIBRO, exist_ok=True)
    nombre = f"historico_{_simbolo(coin).split('/')[0]}_{timeframe}_bitget.csv"
    return os.path.join(DIR_LIBRO, nombre)


def _archivo_velas(coin, timeframe):
    """Ruta del historico PERMANENTE de --velas: herramientas/velas/<COIN>/
    <TF>_bitget.csv - una carpeta por moneda (2026-08-14, a peticion de
    Fran), separada de herramientas/libro/ (que sigue siendo el historico
    de 90 dias que mantiene --feed para el resto de monitores en vivo)."""
    carpeta = os.path.join(DIR_VELAS, _simbolo(coin).split('/')[0])
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, f"{timeframe}_bitget.csv")


MAX_PASOS_BISECCION = 50  # generoso de sobra: 1m sobre 8 anios necesita ~22
                           # pasos en el caso determinista perfecto (ver abajo)


def _primera_vela_ms(cliente, simbolo, timeframe):
    """Busca por biseccion el 'since' mas antiguo que Bitget realmente
    devuelve datos. Comprobado en vivo (2026-08-14): a diferencia de
    Binance, Bitget NO clampa un 'since' anterior al inicio real del
    historico a la vela mas antigua disponible - devuelve una lista VACIA
    (fetch_ohlcv('BTC/USDT:USDT','1d',since=2019-01-01) -> [], mismo
    sintoma para ETH/ICP) - _desde_ms(None) con una fecha fija se comia en
    silencio TODO el historico de golpe (descargar() sin 'desde' llevaba
    este bug ya antes de esta funcion). 'lo' arranca en 2018 (anterior a
    cualquier listado real en Bitget futuros), 'hi' en ahora (siempre tiene
    datos) - convergen al primer momento en que fetch_ohlcv empieza a
    devolver algo. Verificado: BTC 1d converge exacto a 2020-01-01.

    Tope de MAX_PASOS_BISECCION iteraciones (2026-08-14, bug real en vivo):
    con ETH 4h, la biseccion se quedo oscilando sin converger nunca (500+
    pasos, siempre entre las mismas dos fechas) - la respuesta de Bitget
    para un 'since' cerca del borde real NO es perfectamente determinista
    (la misma consulta a veces devuelve datos y a veces vacio), rompiendo
    la asuncion de biseccion pura de que el oraculo es una funcion estable
    de 'since'. Sin tope esto colgaba el proceso para siempre (parecia un
    lock, no lo era). Con tope: si no converge del todo, se acepta el 'hi'
    mas ajustado conseguido (sigue siendo un punto CONFIRMADO con datos,
    solo que no se afino mas alla de eso) en vez de seguir para siempre."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    lo = cliente.parse8601('2018-01-01T00:00:00Z')
    hi = cliente.milliseconds()
    print(f"  Buscando inicio real de historico de {simbolo} {timeframe} "
          f"(sin cache previa, primera vez)...")
    lote = cliente.fetch_ohlcv(simbolo, timeframe, since=lo, limit=1)
    if lote:
        return lote[0][0] - 1  # -1ms: 'since' es exclusivo, sin esto se
                                # perderia esta primera vela confirmada
    for paso in range(1, MAX_PASOS_BISECCION + 1):
        if hi - lo <= tf_ms:
            break
        mid = (lo + hi) // 2
        lote = cliente.fetch_ohlcv(simbolo, timeframe, since=mid, limit=1)
        if lote:
            hi = lote[0][0]
        else:
            lo = mid + tf_ms
        if paso % 5 == 0:
            print(f"    ...acotando ({datetime.fromtimestamp(lo/1000, timezone.utc):%Y-%m-%d} - "
                  f"{datetime.fromtimestamp(hi/1000, timezone.utc):%Y-%m-%d})")
    else:
        print(f"  AVISO: no convergio del todo tras {MAX_PASOS_BISECCION} pasos "
              f"(Bitget respondio de forma inconsistente cerca del borde) - "
              f"se usa el punto mas ajustado confirmado con datos.")
    print(f"  Inicio real: {datetime.fromtimestamp(hi/1000, timezone.utc):%Y-%m-%d}")
    return hi - 1  # -1ms: 'since' es exclusivo, mismo motivo que arriba -
                   # 'hi' es una vela CONFIRMADA con datos, no perderla


def _desde_ms(desde, cliente, simbolo=None, timeframe=None):
    """Interpreta el arg 'desde': fecha ISO, nº de días, o None (todo - busca
    el inicio real por biseccion via _primera_vela_ms si se pasan simbolo/
    timeframe; si no, 2019-01-01 como antes, sabiendo que puede devolver
    vacio para TFs/monedas cuyo historico empiece despues, ver arriba)."""
    if desde is None:
        if simbolo and timeframe:
            return _primera_vela_ms(cliente, simbolo, timeframe)
        return cliente.parse8601('2019-01-01T00:00:00Z')
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


def _segundos_tf(timeframe):
    mult = {"m": 60, "h": 3600, "d": 86400}
    return int(timeframe[:-1]) * mult[timeframe[-1]]


def _velas_objetivo(timeframe, dias=DIAS_OBJETIVO):
    """Nº de velas para cubrir 'dias' de historico en este TF - escala solo
    (mas velas en TF finos, menos en TF gruesos) para llegar siempre a la
    MISMA profundidad en tiempo, no a un nº de velas fijo (ver cabecera)."""
    por_dias = math.ceil(dias * 86400 / _segundos_tf(timeframe))
    return max(por_dias, VENTANA_MAXIMA)


def _recortar_si_hace_falta(ruta, cap, margen=0.10):
    """Recorta 'ruta' a las ultimas 'cap' velas, pero SOLO si se pasa del cap
    por un margen de holgura (10% por defecto) - con cap=500 esto reescribe
    el fichero cada ~50 velas nuevas, no en cada vuelta del feed. Reescritura
    en streaming (sin cargar todo en memoria) + os.replace() atomico, para
    que ningun lector en vivo vea nunca el fichero a medio escribir."""
    with open(ruta, 'r', newline='') as f:
        total = sum(1 for _ in f) - 1  # menos cabecera
    if total <= cap * (1 + margen):
        return
    tmp = ruta + ".tmp"
    with open(ruta, 'r', newline='') as fin, open(tmp, 'w', newline='') as fout:
        fout.write(fin.readline())  # cabecera
        a_saltar = total - cap
        for i, linea in enumerate(fin):
            if i >= a_saltar:
                fout.write(linea)
    os.replace(tmp, ruta)


@contextmanager
def _con_lock(ruta, timeout=1800.0, espera=2.0):
    """Lock de fichero por creacion atomica (O_CREAT|O_EXCL, '<ruta>.lock')
    para que dos escritores (el feed y una ejecucion manual puntual) no
    toquen el mismo historico a la vez - el peor momento para pisarse es
    durante un recorte (_recortar_si_hace_falta reescribe el fichero
    entero). Los lectores (niveles.py, monitor_niveles.py) NO
    necesitan este lock: ya tratan una fila a medio escribir al final del
    fichero como no consumida todavia (mismo patron que _tail_csv usa para
    flujo_*.csv), y _recortar_si_hace_falta usa os.replace() atomico, asi
    que un lector con el fichero ya abierto sigue viendo el contenido viejo
    completo hasta que lo reabra."""
    ruta_lock = ruta + ".lock"
    inicio = time.monotonic()
    avisado = False
    while True:
        try:
            fd = os.open(ruta_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if not avisado:
                print(f"  Esperando lock de {ruta_lock} (¿otra descarga en curso, o restos de "
                      f"una anterior sin terminar limpio? maximo {timeout:.0f}s antes de fallar)...")
                avisado = True
            if time.monotonic() - inicio > timeout:
                raise TimeoutError(f"lock de {ruta} no liberado tras {timeout:.0f}s")
            time.sleep(espera)
    try:
        yield
    finally:
        os.remove(ruta_lock)


def _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req=200):
    """Pagina fetch_ohlcv desde 'since' hasta 'hasta_ms' (exclusive).
    Devuelve velas ordenadas y sin duplicados.

    'since' en Bitget/ccxt es EXCLUSIVO (devuelve velas > since, no >=) -
    comprobado en vivo el 2026-08-14 con ETH 15m: pedir since=X (un
    timestamp de vela real) devuelve la vela SIGUIENTE, nunca X misma. El
    codigo viejo avanzaba la pagina con since=ultima_vela+tf_ms, que sumado
    a esa exclusividad se comia la vela justo en ese punto - hueco de
    EXACTAMENTE 1 vela en cada frontera de pagina (cada 'limite_req' velas,
    200 por defecto), reproducido en los 7 TF de ETH sin excepcion. Fix:
    avanzar con since=ultima_vela (sin sumar tf_ms) - la exclusividad de la
    API ya deja fuera esa vela y empieza justo en la siguiente."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    velas = []
    vistos = set()
    pagina = 0
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
        since = lote[-1][0]
        pagina += 1
        # cada 10 paginas (no cada 20000 velas: con TFs finos y --dias-objetivo
        # capado, o TFs gruesos con poco historico, esto podia no imprimir NUNCA
        # y una descarga larga parecia colgada sin estarlo - 2026-08-14)
        if pagina % 10 == 0:
            print(f"  {len(velas)} velas... "
                  f"({datetime.fromtimestamp(lote[-1][0]/1000, timezone.utc):%Y-%m-%d})")
        if nuevos == 0:
            break
    if velas:
        print(f"  {len(velas)} velas descargadas "
              f"({datetime.fromtimestamp(velas[-1][0]/1000, timezone.utc):%Y-%m-%d}).")
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
    since = _desde_ms(desde, cliente, simbolo, timeframe)
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


def actualizar(coin, timeframe, dias_objetivo=DIAS_OBJETIVO, limite_req=200):
    """Si NO hay fichero previo para esta coin/tf: descarga exactamente las
    velas necesarias para cubrir 'dias_objetivo' (calculado directamente
    por tiempo, sin bajar de mas para luego recortar). Si YA existe: lee la
    ultima vela guardada y solo pide/AÑADE lo que falta, sin reescribir el
    fichero entero. En ambos casos, al final recorta el fichero si se pasa
    del cap por margen de holgura (ver _recortar_si_hace_falta)."""
    velas_objetivo = _velas_objetivo(timeframe, dias_objetivo)
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    ruta = _archivo(coin, timeframe)
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    if not os.path.exists(ruta):
        since = hasta_ms - velas_objetivo * _segundos_tf(timeframe) * 1000
        print(f"Descargando {simbolo} {timeframe}: {velas_objetivo:.0f} velas ...")
        velas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
        if not velas:
            print("No se descargó nada (¿símbolo o timeframe inválido?).")
            return ruta
        with open(ruta, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
            _escribir_filas(f, velas)
        print(f"  [OK] {simbolo} {timeframe}: {len(velas)} velas guardadas en {ruta}")
    else:
        tf_ms = cliente.parse_timeframe(timeframe) * 1000
        ultimo_ts = _ultimo_timestamp_ms(ruta)
        # since=ultimo_ts (NO +tf_ms): 'since' en Bitget es EXCLUSIVO, ya
        # deja fuera ultimo_ts por su cuenta - sumar tf_ms aqui se comia
        # SIEMPRE la primera vela nueva de cada actualizacion (ver
        # _descargar_rango, mismo bug, mismo fix).
        since = ultimo_ts
        if ultimo_ts + tf_ms < hasta_ms:
            nuevas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
            if nuevas:
                with open(ruta, 'a', newline='') as f:
                    _escribir_filas(f, nuevas)
                print(f"  [OK] {simbolo} {timeframe}: +{len(nuevas)} velas "
                      f"(hasta {datetime.fromtimestamp(nuevas[-1][0]/1000, timezone.utc):%Y-%m-%d %H:%M} UTC)")

    _recortar_si_hace_falta(ruta, velas_objetivo)
    return ruta


def actualizar_velas(coin, timeframe, limite_req=200):
    """Version PERMANENTE de actualizar(): sin DIAS_OBJETIVO, nunca recorta.
    Escribe en _archivo_velas() (herramientas/velas/<COIN>/), no en
    herramientas/libro/. Sin fichero previo descarga TODO el historico
    disponible (igual que descargar(coin, tf, desde=None)); si ya existe,
    lee la ultima vela guardada y solo AÑADE lo que falta - mismo modelo de
    cache permanente que descargar_bin.py (Binance), aplicado aqui a
    Bitget/futuros porque niveles.py (unico consumidor de esta
    carpeta) necesita el precio exacto que ve monitor.py, no el de otro
    exchange (ver cabecera del fichero). De un tiro: se lanza a mano cuando
    se quiera refrescar, no es un daemon (a diferencia de --feed) - por eso
    no necesita nada especifico de plataforma, corre igual en Linux (NAS)
    que en Windows."""
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    ruta = _archivo_velas(coin, timeframe)
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    with _con_lock(ruta):
        if not os.path.exists(ruta):
            since = _desde_ms(None, cliente, simbolo, timeframe)
            print(f"Descargando {simbolo} {timeframe}: historico completo ...")
            velas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
            if not velas:
                print("  No se descargó nada (¿símbolo o timeframe inválido?).")
                return ruta
            with open(ruta, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
                _escribir_filas(f, velas)
            print(f"  [OK] {simbolo} {timeframe}: {len(velas)} velas guardadas en {ruta}")
            print(f"       {datetime.fromtimestamp(velas[0][0]/1000, timezone.utc):%Y-%m-%d} "
                  f"-> {datetime.fromtimestamp(velas[-1][0]/1000, timezone.utc):%Y-%m-%d}")
        else:
            tf_ms = cliente.parse_timeframe(timeframe) * 1000
            ultimo_ts = _ultimo_timestamp_ms(ruta)
            since = ultimo_ts  # ver nota de "since exclusivo" en actualizar()
            if ultimo_ts + tf_ms >= hasta_ms:
                print(f"  {simbolo} {timeframe} ya esta al dia.")
                return ruta
            nuevas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
            if not nuevas:
                print(f"  {simbolo} {timeframe}: no hay velas nuevas todavia.")
                return ruta
            with open(ruta, 'a', newline='') as f:
                _escribir_filas(f, nuevas)
            print(f"  [OK] {simbolo} {timeframe}: +{len(nuevas)} velas "
                  f"(hasta {datetime.fromtimestamp(nuevas[-1][0]/1000, timezone.utc):%Y-%m-%d %H:%M} UTC)")
    return ruta


def _feed(coins, tfs, dias_objetivo, cada):
    """Modo daemon: mantiene al dia (y acotado a 'dias_objetivo' dias) el
    historico de cada coin/tf, sin parar, con lock para no pisarse con una
    ejecucion manual concurrente (ver _con_lock). Un fallo puntual de una
    coin/tf (red, símbolo) no debe tumbar el proceso - se avisa y se sigue
    con el resto, misma filosofia de resiliencia que grabador_libro.py."""
    print(f"Feed de velas Bitget: {', '.join(coins)} / {', '.join(tfs)} "
          f"(dias_objetivo={dias_objetivo:.0f}, cada={cada:.0f}s). Ctrl+C para parar.")
    try:
        while True:
            for coin in coins:
                for tf in tfs:
                    try:
                        with _con_lock(_archivo(coin, tf)):
                            actualizar(coin, tf, dias_objetivo=dias_objetivo)
                    except Exception as e:
                        print(f"  (aviso) {coin} {tf}: {e}")
            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")


def _feed_velas(coins, tfs, cada):
    """Modo daemon para --velas (2026-08-14): igual que _feed() pero sobre
    el historico PERMANENTE de herramientas/velas/ (sin cap de dias) en
    vez de herramientas/libro/. actualizar_velas() ya hace su propio
    _con_lock() por dentro, asi que cualquier otro proceso (ej.
    niveles.py --actualizar) puede llamarla directamente para un
    refresco instantaneo sin pisarse con este bucle - el lock de fichero
    ya evita que dos escrituras coincidan, sea cual sea quien la dispare."""
    print(f"Feed de velas permanentes: {', '.join(coins)} / {', '.join(tfs)} "
          f"(cada={cada:.0f}s). Ctrl+C para parar.")
    try:
        while True:
            for coin in coins:
                for tf in tfs:
                    try:
                        actualizar_velas(coin, tf)
                    except Exception as e:
                        print(f"  (aviso) {coin} {tf}: {e}")
            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--velas":
        args = args[1:]
        if not args:
            print("Uso: python descargar_bit.py --velas <coin[,coin2,...]> [tf] [--cada segundos]")
            return
        coins = [c.strip().upper() for c in args[0].split(",")]
        resto = args[1:]
        tf_unico = None
        if resto and not resto[0].startswith("--"):
            tf_unico = resto[0]
            resto = resto[1:]
        cada = None
        i = 0
        while i < len(resto):
            if resto[i] == "--cada":
                i += 1; cada = float(resto[i])
            i += 1
        tfs = [tf_unico] if tf_unico else TIMEFRAMES_NIVELES

        if cada is None:
            for coin in coins:
                for tf in tfs:
                    actualizar_velas(coin, tf)
        else:
            _feed_velas(coins, tfs, cada)
        return

    if args and args[0] == "--feed":
        args = args[1:]
        if args and not args[0].startswith("--"):
            coins = [c.strip().upper() for c in args[0].split(",")]
            args = args[1:]
        else:
            coins = ["BTC", "ETH"]
        tfs = TIMEFRAMES_FEED
        dias_objetivo = DIAS_OBJETIVO
        cada = 60.0
        i = 0
        while i < len(args):
            if args[i] == "--tfs":
                i += 1; tfs = [t.strip() for t in args[i].split(",")]
            elif args[i] == "--dias-objetivo":
                i += 1; dias_objetivo = float(args[i])
            elif args[i] == "--cada":
                i += 1; cada = float(args[i])
            i += 1
        _feed(coins, tfs, dias_objetivo, cada)
        return

    if len(sys.argv) < 3:
        print("Uso: python descargar_bit.py <coin> <timeframe> [desde]")
        print("  coin:      eth, btc, icp, sol...  (o ETH/USDT:USDT)")
        print("  timeframe: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...")
        print("  desde:     'YYYY-MM-DD' o nº de días atrás (opcional; si no, todo)")
        print("\nEjemplos:")
        print("  python descargar_bit.py btc 5m 1")
        print("  python descargar_bit.py eth 15m 2023-01-01")
        print("\nModo daemon (siempre corriendo, ver cabecera del fichero):")
        print("  python descargar_bit.py --feed [coin[,coin2,...]] [--tfs 1m,5m,15m,30m,1h,4h,1d]")
        print("                           [--dias-objetivo 90] [--cada 60]")
        print("\nHistorico permanente para niveles.py (ver cabecera):")
        print("  python descargar_bit.py --velas <coin[,coin2,...]> [tf] [--cada segundos]")
        return
    coin = sys.argv[1]
    timeframe = sys.argv[2]
    desde = sys.argv[3] if len(sys.argv) > 3 else None
    descargar(coin, timeframe, desde)


if __name__ == "__main__":
    main()
