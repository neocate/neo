# ================================================================================
# descargar_bit.py
# 
# Descarga/actualiza historial de velas OHLCV (futuros USDT-M) desde Bitget.
# Soporta todos los TF (1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d). Solo velas cerradas.
# Independiente de --loop. Protegido contra instancias simultáneas.
# STREAMING: Escribe en chunks de 50k velas, libera memoria continuamente (sin acumular).
#
# ALMACENAMIENTO (multiplataforma):
#   Windows: \\NAS_OFICINA\homes\Fran\neo\herramientas\velas\[COIN]\historico_[COIN]_[TF]_bitget.csv
#   Linux:   /volume1/homes/Fran/neo/herramientas/velas/[COIN]/historico_[COIN]_[TF]_bitget.csv
#
# MÉTODOS DE LLAMADA (Autorregulado):
#
#   1. DESCARGA PUNTUAL - Todos los TF:
#      python descargar_bit.py eth
#      → Si hay gap: descarga bloques de 50k hasta estar al día
#      → Salida
#
#   2. DESCARGA PUNTUAL - TF selectivos:
#      python descargar_bit.py eth 5m,15m,1h
#      → Si hay gap: bloques de 50k
#      → Salida cuando está al día
#
#   3. DAEMON INDEFINIDO - Todos los TF:
#      python descargar_bit.py eth --loop
#      → Bloques 50k si hay gap
#      → Luego: DAEMON (espera velas 1m cada 60s)
#      → Ctrl+C para salir
#
#   4. DAEMON INDEFINIDO - TF selectivos sin 1m:
#      python descargar_bit.py eth 5m,15m,1h --loop
#      → Bloques 50k si hay gap
#      → Luego: DAEMON (duerme 300s = TF mínimo de [5m,15m,1h])
#
#   5. DAEMON - TF selectivos con 1m (base):
#      python descargar_bit.py eth 1m,5m,15m --loop
#      → Bloques 50k si hay gap
#      → Luego: DAEMON (duerme 60s = 1m, la base siempre)
#
#   6. DAEMON - Solo TF grandes (sin 1m):
#      python descargar_bit.py btc 4h,1d --loop
#      → Bloques 50k si hay gap
#      → Luego: DAEMON (duerme 14400s = 4h, TF mínimo de [4h,1d])
#
#   3. USO COMO LIBRERÍA (importar):
#      from descargar_bit import _estado_vela, actualizar, descargar
#      
#      # Leer último estado de vela (sin escritura, independiente)
#      estado = _estado_vela("\\NAS_OFICINA\homes\Fran\neo\herramientas\velas\BTC\historico_BTC_1h_bitget.csv")
#      # Retorna: {'ts': 1723456800000, 'fecha': '2026-08-19 10:00:00', 'close': 42500.5, 'vol': 123.45}
#      
#      # Descargar completamente un TF
#      archivo = descargar('eth', '1h')
#      
#      # Actualizar (añade solo lo que falta)
#      archivo = actualizar('btc', '4h')
#
# LOCKFILE (en --loop):
#   Ubicación: velas/[COIN]/.lock_descargar_[COIN]
#   Se crea al iniciar --loop, se elimina al terminar (Ctrl+C).
#   Si se cuelga: elimina manualmente el archivo .lock para poder reiniciar.
#
# ================================================================================

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']
TF_SEGUNDOS = {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400}

# Detecta SO y usa ruta correcta (Windows o Linux/NAS)
if sys.platform == 'win32':
    NAS_VELAS = Path(r'\\NAS_OFICINA\homes\Fran\neo\herramientas\velas')
else:
    NAS_VELAS = Path('/volume1/homes/Fran/neo/herramientas/velas')


def _obtener_lock(coin):
    """Crea lockfile en carpeta de descarga para evitar instancias simultáneas de --loop.
    Retorna ruta del lock. Si ya existe, lanza excepción."""
    moneda = _extraer_moneda(coin)
    dir_coin = NAS_VELAS / moneda
    lock_file = dir_coin / f".lock_descargar_{moneda}"
    
    if lock_file.exists():
        raise RuntimeError(
            f"[ERROR] Ya hay una instancia de {moneda} en --loop\n"
            f"        Si se colgó, elimina: {lock_file}"
        )
    lock_file.touch()
    return lock_file


def _liberar_lock(lock_file):
    """Elimina lockfile al terminar --loop (Ctrl+C o al finalizar actualización)."""
    try:
        lock_file.unlink()
    except FileNotFoundError:
        pass


def _simbolo(coin):
    """'eth' -> 'ETHUSDT' (símbolo Bitget futuros USDT-M)."""
    coin = coin.strip().upper()
    # Si ya viene con símbolo completo, usa como está
    if coin.endswith('USDT'):
        return coin
    # Si trae /, extrae la moneda: 'ETH/USDT:USDT' -> 'ETHUSDT'
    if '/' in coin:
        return coin.split('/')[0] + 'USDT'
    # Moneda corta: 'eth' -> 'ETHUSDT'
    return f"{coin}USDT"


def _extraer_moneda(coin):
    """Extrae solo la moneda (ej: 'eth' -> 'ETH', 'ETHUSDT' -> 'ETH')."""
    coin = coin.strip().upper()
    # Si trae /, extrae la parte antes: 'ETH/USDT:USDT' -> 'ETH'
    if '/' in coin:
        return coin.split('/')[0]
    # Si termina con USDT, lo quita: 'ETHUSDT' -> 'ETH'
    if coin.endswith('USDT'):
        return coin[:-4]
    return coin


def _validar_directorio_coin(coin):
    """Valida que exista velas/[coin]/ - multiplataforma (Windows + Linux)"""
    moneda = _extraer_moneda(coin)
    dir_coin = NAS_VELAS / moneda
    if not dir_coin.is_dir():
        print(f"[ERROR] Directorio no existe: {dir_coin}")
        print(f"        Crea: mkdir -p {dir_coin}")
        sys.exit(1)
    return dir_coin


def _archivo(coin, timeframe):
    dir_coin = _validar_directorio_coin(coin)
    moneda = _extraer_moneda(coin)
    nombre = f"historico_{moneda}_{timeframe}_bitget.csv"
    return dir_coin / nombre


def _desde_ms(desde, cliente):
    """Interpreta el arg 'desde': fecha ISO, nº de días, o None (todo)."""
    if desde is None:
        return cliente.parse8601('2020-01-01T00:00:00Z')  # Bitget futuros tiene datos desde ~2020
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
    ruta = Path(ruta) if not isinstance(ruta, Path) else ruta
    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        cola = f.read()
    lineas = [l for l in cola.split(b'\n') if l.strip()]
    return int(lineas[-1].split(b',')[0])


def _estado_vela(ruta):
    """Lee la última vela: (timestamp, fecha, close, vol). Retorna None si no existe."""
    ruta = Path(ruta) if not isinstance(ruta, Path) else ruta
    if not ruta.exists():
        return None
    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        cola = f.read()
    lineas = [l.decode().strip() for l in cola.split(b'\n') if l.strip()]
    if len(lineas) < 2:  # solo cabecera
        return None
    partes = lineas[-1].split(',')
    ts, fecha, _, _, _, close, vol = partes[:7]
    return {'ts': int(ts), 'fecha': fecha, 'close': float(close), 'vol': float(vol)}


def _verificar_y_rellenar_gaps(cliente, simbolo, timeframe, archivo):
    """Verifica continuidad en archivo. Si hay gaps, intenta rellenarlos.
    
    Retorna:
        True: archivo está completo y continuo
        False: hay gaps irrecuperables
    """
    archivo = Path(archivo) if not isinstance(archivo, Path) else archivo
    
    if not archivo.exists():
        return True  # No hay archivo = nada que verificar
    
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    gaps = []
    
    # Detectar gaps
    with open(archivo, 'r', encoding='utf-8') as f:
        f.readline()  # skip header
        ts_prev = None
        linea_num = 1
        
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            linea_num += 1
            ts = int(linea.split(',')[0])
            
            if ts_prev is not None and ts - ts_prev != tf_ms:
                gap_velas = (ts - ts_prev) // tf_ms - 1
                gaps.append({
                    'since': ts_prev + tf_ms,
                    'hasta': ts,
                    'velas': gap_velas,
                    'linea': linea_num
                })
            ts_prev = ts
    
    if not gaps:
        return True  # Sin gaps
    
    # Hay gaps - intentar rellenar
    print(f"  [VERIFICACIÓN] {len(gaps)} gap(s) detectado(s) en {timeframe}")
    
    for i, gap in enumerate(gaps, 1):
        print(f"    Gap {i}: {int(gap['velas'])} vela(s) faltante(s) (línea {gap['linea']})")
        
        try:
            velas_gap = _descargar_rango(cliente, simbolo, timeframe, gap['since'], gap['hasta'], limite_req=200)
            
            if len(velas_gap) != int(gap['velas']):
                print(f"    [ERROR] Se esperaban {int(gap['velas'])} velas, se obtuvieron {len(velas_gap)}")
                print(f"    [CRÍTICO] No se pudo completar el historial. Abortando.")
                return False
            
            print(f"    [RELLENANDO] Insertando {len(velas_gap)} vela(s)...")
            _reinsertar_velas(archivo, velas_gap)
            
        except Exception as e:
            print(f"    [ERROR] No se pudo rellenar: {e}")
            print(f"    [CRÍTICO] No se pudo completar el historial. Abortando.")
            return False
    
    print(f"  [OK] Todos los gaps rellenados. {timeframe} completo.")
    return True


def _reinsertar_velas(archivo, velas_nuevas):
    """Reinsererta velas en archivo manteniendo orden cronológico."""
    archivo = Path(archivo) if not isinstance(archivo, Path) else archivo
    
    # Leer todas las velas existentes
    velas_existentes = []
    with open(archivo, 'r', encoding='utf-8') as f:
        f.readline()  # skip header
        for linea in f:
            linea = linea.strip()
            if linea:
                ts = int(linea.split(',')[0])
                velas_existentes.append({'ts': ts, 'linea': linea})
    
    # Crear líneas para nuevas velas
    velas_nuevas_lineas = []
    for v in velas_nuevas:
        ts = int(v[0])
        fecha = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        linea = f"{ts},{fecha},{v[2]},{v[3]},{v[4]},{v[5]},{v[6]}"
        velas_nuevas_lineas.append({'ts': ts, 'linea': linea})
    
    # Combinar y ordenar
    todas = velas_existentes + velas_nuevas_lineas
    todas.sort(key=lambda x: x['ts'])
    
    # Reescribir archivo
    with open(archivo, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
        for item in todas:
            f.write(item['linea'] + '\n')


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


def _descargar_rango_streaming(cliente, simbolo, timeframe, since, hasta_ms, archivo, limite_req=200, chunk_size=50000):
    """Descarga y ESCRIBE EN CHUNKS (50k velas) liberando memoria continuamente.
    VERIFICA y RELLENA GAPS después de CADA chunk (no desperdicia descargas)."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    velas_buffer = []
    vistos = set()
    total_velas = 0
    primer_chunk = True

    print(f"  Descargando con escritura en chunks de {chunk_size:,} velas...")

    with open(archivo, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])

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
                    velas_buffer.append(v)
                    nuevos += 1

            # Si buffer alcanza chunk_size, ordenar, escribir y VERIFICAR
            if len(velas_buffer) >= chunk_size:
                velas_buffer.sort(key=lambda v: v[0])
                _escribir_filas(f, velas_buffer)
                total_velas += len(velas_buffer)
                print(f"  {total_velas:,} velas escritas... "
                      f"({datetime.fromtimestamp(lote[-1][0]/1000, timezone.utc):%Y-%m-%d})")
                
                # VERIFICAR DESPUÉS DE CADA CHUNK
                if not _verificar_y_rellenar_gaps(cliente, simbolo, timeframe, archivo):
                    return False  # Abortar si hay gaps irrecuperables
                
                velas_buffer.clear()
                primer_chunk = False

            since = lote[-1][0] + tf_ms
            if nuevos == 0:
                break

        # Escribir remainder y VERIFICAR
        if velas_buffer:
            velas_buffer.sort(key=lambda v: v[0])
            _escribir_filas(f, velas_buffer)
            total_velas += len(velas_buffer)
            
            if not _verificar_y_rellenar_gaps(cliente, simbolo, timeframe, archivo):
                return False
    
    return total_velas


def _escribir_filas(f, velas):
    w = csv.writer(f)
    for t, o, h, l, c, vol in velas:
        fecha = datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        w.writerow([t, fecha, o, h, l, c, vol])


def descargar(coin, timeframe, desde=None, limite_req=200):
    """Descarga completa (o desde 'desde'), SIEMPRE reescribe el fichero
    entero. STREAMING: escribe en chunks de 50k, libera memoria continuamente.
    Para refrescar sin perder lo ya bajado usar actualizar()."""
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    since = _desde_ms(desde, cliente)
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    print(f"Descargando {simbolo} {timeframe} desde "
          f"{datetime.fromtimestamp(since/1000, timezone.utc):%Y-%m-%d} ...")

    archivo = _archivo(coin, timeframe)
    
    try:
        total_velas = _descargar_rango_streaming(cliente, simbolo, timeframe, since, hasta_ms, archivo, limite_req)
    except ccxt.BaseError as e:
        print(f"[ERROR API] {e}")
        return None
    
    if total_velas is False:
        print(f"[ERROR CRÍTICO] {simbolo} {timeframe} tiene gaps irrecuperables. Abortando.")
        return None
    
    if total_velas == 0:
        print(f"[AVISO] No hay datos para {simbolo} {timeframe} (¿símbolo inválido o sin historial?).")
        return None

    # Leer fechas primera y última para resumen
    first_ts, last_ts = None, None
    with open(archivo, 'r') as f:
        f.readline()  # skip header
        linea_primera = f.readline()
        if linea_primera:
            first_ts = int(linea_primera.split(',')[0])
    
    with open(archivo, 'rb') as f:
        f.seek(0, 2)  # EOF
        f.seek(max(0, f.tell() - 1000))
        cola = f.read()
    ultima_linea = cola.split(b'\n')[-2].decode()
    last_ts = int(ultima_linea.split(',')[0])

    print(f"\n[OK] {total_velas:,} velas guardadas en {archivo}")
    print(f"     {datetime.fromtimestamp(first_ts/1000, timezone.utc):%Y-%m-%d} "
          f"-> {datetime.fromtimestamp(last_ts/1000, timezone.utc):%Y-%m-%d}")
    return archivo


def actualizar(coin, timeframe, desde=None, limite_req=200):
    """Si NO hay fichero previo para esta coin/tf: descarga desde 'desde'
    (mismo formato que descargar() - None = todo el historico). Si YA
    existe: ignora 'desde', lee la ultima vela guardada y solo pide/AÑADE
    lo que falta, sin reescribir el fichero entero.
    STREAMING: escribe en chunks de 50k, libera memoria continuamente."""
    archivo = _archivo(coin, timeframe)
    if not archivo.exists():
        return descargar(coin, timeframe, desde=desde, limite_req=limite_req)

    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    ultimo_ts = _ultimo_timestamp_ms(archivo)
    since = ultimo_ts + tf_ms
    if since >= hasta_ms:
        return archivo  # ya al dia - se llama seguido desde grabador_libro.py

    # Descargador streaming para AÑADIR (append)
    nuevas = _descargar_rango_append(cliente, simbolo, timeframe, since, hasta_ms, archivo, limite_req)
    
    if nuevas is False:
        print(f"  [ERROR CRÍTICO] {simbolo} {timeframe} tiene gaps irrecuperables. Abortando.")
        return None
    
    if nuevas == 0:
        return archivo

    print(f"  [OK] {simbolo} {timeframe}: +{nuevas} velas "
          f"(hasta {datetime.now():%Y-%m-%d %H:%M} UTC)")
    return archivo


def _descargar_rango_append(cliente, simbolo, timeframe, since, hasta_ms, archivo, limite_req=200, chunk_size=50000):
    """Descarga y AÑADE (append) en chunks, liberando memoria continuamente.
    VERIFICA y RELLENA GAPS después de CADA chunk (no desperdicia descargas)."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    velas_buffer = []
    vistos = set()
    total_nuevas = 0

    with open(archivo, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)

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
                    velas_buffer.append(v)
                    nuevos += 1

            # Escribir cuando buffer alcanza chunk_size y VERIFICAR
            if len(velas_buffer) >= chunk_size:
                velas_buffer.sort(key=lambda v: v[0])
                _escribir_filas(f, velas_buffer)
                total_nuevas += len(velas_buffer)
                print(f"    +{total_nuevas:,} velas añadidas... "
                      f"({datetime.fromtimestamp(lote[-1][0]/1000, timezone.utc):%Y-%m-%d})")
                
                # VERIFICAR DESPUÉS DE CADA CHUNK
                if not _verificar_y_rellenar_gaps(cliente, simbolo, timeframe, archivo):
                    return False  # Abortar si hay gaps irrecuperables
                
                velas_buffer.clear()

            since = lote[-1][0] + tf_ms
            if nuevos == 0:
                break

        # Escribir remainder y VERIFICAR
        if velas_buffer:
            velas_buffer.sort(key=lambda v: v[0])
            _escribir_filas(f, velas_buffer)
            total_nuevas += len(velas_buffer)
            
            if not _verificar_y_rellenar_gaps(cliente, simbolo, timeframe, archivo):
                return False
    
    return total_nuevas


def actualizar_todos(coin, timeframes_str=None, loop=False):
    """Autorregulada: descarga bloques de 50k hasta estar al día, luego DAEMON.
    
    Lógica:
    1. Lee última vela guardada en cada TF
    2. Calcula vela_actual - 1 (última vela cerrada)
    3. Si última < (actual-1): descarga bloques 50k hasta ponerse al día
    4. Si última == (actual-1): entra DAEMON (espera velas cerradas, descarga, duerme)
    
    Args:
        coin: moneda (eth, btc, etc.)
        timeframes_str: TF específicos (ej: '5m,15m,1h') o None = TODOS
        loop: True = continúa después de estar al día (DAEMON indefinido)
    """
    simbolo = _simbolo(coin)
    moneda = simbolo.split('/')[0]
    
    # Parsear TF solicitados
    if timeframes_str:
        tfs = [tf.strip() for tf in timeframes_str.split(',')]
        tfs = [tf for tf in tfs if tf in TIMEFRAMES]
    else:
        tfs = TIMEFRAMES
    
    if not tfs:
        print(f"[ERROR] TF inválidos. Usa: {','.join(TIMEFRAMES)}")
        return
    
    tf_min_ms = min(TF_SEGUNDOS[tf] for tf in tfs)
    lock_file = None
    en_daemon = False
    
    if loop:
        lock_file = _obtener_lock(coin)
        print(f"\n{'='*60}")
        print(f"[MODO AUTORREGULADO] {moneda} - TF: {', '.join(tfs)}")
        print(f"  Si hay gap: bloques de 50k")
        print(f"  Si al día: DAEMON (espera {tf_min_ms}s entre ciclos)")
        print(f"  Lock: {lock_file.name}")
        print(f"{'='*60}\n")
    else:
        print(f"\n[MODO AUTORREGULADO] {moneda} - TF: {', '.join(tfs)}")
        print(f"  Descarga bloques de 50k hasta estar al día, luego salida\n")
    
    try:
        while True:
            print(f"\n[{datetime.now():%H:%M:%S}] Analizando estado...")
            estados = {}
            gaps_detectados = False
            
            # Paso 1: Analizar qué hace falta en cada TF
            for tf in tfs:
                try:
                    cliente = ccxt.bitget({'enableRateLimit': True})
                    ruta = _archivo(coin, tf)
                    
                    # Última vela guardada
                    ultima_guardada = _estado_vela(ruta)
                    ultima_ts = ultima_guardada['ts'] if ultima_guardada else 0
                    
                    # Vela actual - 1 (última cerrada)
                    tf_ms = cliente.parse_timeframe(tf) * 1000
                    ahora_ms = cliente.milliseconds()
                    vela_anterior_ts = ahora_ms - (ahora_ms % tf_ms)
                    
                    # ¿Hay gap?
                    if ultima_ts < vela_anterior_ts:
                        gap_velas = (vela_anterior_ts - ultima_ts) // tf_ms
                        print(f"  {tf:4} | GAP: {gap_velas:,} velas faltantes")
                        gaps_detectados = True
                    else:
                        print(f"  {tf:4} | Al día ({ultima_guardada['fecha']})")
                        estados[tf] = ultima_guardada
                        
                except Exception as e:
                    print(f"  {tf:4} | [ERROR análisis] {e}")
            
            # Paso 2: Si hay gaps, descargar bloques de 50k
            if gaps_detectados:
                print(f"\n  Descargando bloques de 50k...")
                for tf in tfs:
                    try:
                        actualizar(coin, tf)
                        estado = _estado_vela(_archivo(coin, tf))
                        if estado:
                            estados[tf] = estado
                    except Exception as e:
                        print(f"    {tf:4} | [ERROR] {e}")
                # Volver a analizar (loop continúa)
                continue
            
            # Paso 3: Si NO hay gaps y NO es loop, salir
            if not loop:
                _resumen(moneda, estados)
                print(f"\n[FIN] {moneda} al día. Salida.")
                return estados
            
            # Paso 4: Modo DAEMON (loop=True)
            if not en_daemon:
                en_daemon = True
                print(f"\n{'='*60}")
                print(f"[DAEMON ACTIVO] {moneda} en vivo")
                print(f"  Duerme {tf_min_ms}s, espera velas cerradas")
                print(f"{'='*60}")
            
            # Espera a que cierre siguiente vela (TF mínimo)
            ahora = datetime.now(timezone.utc)
            tf_min_ms_milis = tf_min_ms * 1000
            ahora_ms = int(ahora.timestamp() * 1000)
            ms_hasta_cierre = tf_min_ms_milis - (ahora_ms % tf_min_ms_milis)
            segundos_espera = (ms_hasta_cierre / 1000) + 1
            
            print(f"  Próxima vela cierra en {segundos_espera:.0f}s... (durmiendo)")
            time.sleep(segundos_espera)
            
    finally:
        if lock_file:
            _liberar_lock(lock_file)


def _resumen(moneda, estados):
    """Imprime resumen de estado actual de todas las velas."""
    if not estados:
        print(f"\n[RESUMEN] {moneda}: sin datos")
        return
    print(f"\n[RESUMEN] {moneda} - estado actual:")
    for tf in TIMEFRAMES:
        if tf in estados:
            e = estados[tf]
            print(f"  {tf:4} | {e['fecha']} | close={e['close']:10.2f} | vol={e['vol']:12.0f}")
        else:
            print(f"  {tf:4} | [sin datos]")


def main():
    if len(sys.argv) < 2:
        print("╔════════════════════════════════════════════════════════════════╗")
        print("║  descargar_bit.py - AUTORREGULADO (bloques 50k → DAEMON)       ║")
        print("╚════════════════════════════════════════════════════════════════╝")
        print("\nUso:")
        print("  python descargar_bit.py <coin> [TF,...] [--loop]")
        print("\nComportamiento:")
        print("  • Si hay gap: descarga bloques de 50k (reanudable)")
        print("  • Cuando está al día:")
        print("    - Sin --loop: salida (FIN)")
        print("    - Con --loop: DAEMON (espera velas cerradas, descarga nuevas, duerme)")
        print("  • Base: TF mínimo es SIEMPRE 1m (60s). Sin especificar TF = TODOS")
        print("\nIntervalos DAEMON:")
        print("  • Sin TF especificados: duerme 60s (1m es la base)")
        print("  • Si pasas TF sin 1m: duerme por el TF mínimo pasado")
        print("\nEjemplos:")
        print("  # Descarga puntual (salida cuando está al día):")
        print("    python descargar_bit.py eth")
        print("    python descargar_bit.py eth 5m,15m,1h")
        print("\n  # DAEMON indefinido:")
        print("    python descargar_bit.py eth --loop              # Sin TF → todos (1m base) → duerme 60s")
        print("    python descargar_bit.py eth 5m,15m --loop       # TF: 5m,15m (sin 1m) → duerme 300s")
        print("    python descargar_bit.py eth 1m,5m,15m --loop    # TF: incluye 1m → duerme 60s")
        print("    python descargar_bit.py btc 4h,1d --loop        # TF: sin 1m → duerme 14400s (4h)")
        print("\nUsage como módulo:")
        print("  from descargar_bit import _estado_vela, actualizar, descargar")
        return
    
    coin = sys.argv[1]
    loop = '--loop' in sys.argv
    
    # Extraer TF si está entre coin y --loop
    timeframes = None
    for arg in sys.argv[2:]:
        if arg != '--loop' and not arg.startswith('-'):
            timeframes = arg
            break
    
    try:
        actualizar_todos(coin, timeframes_str=timeframes, loop=loop)
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\n[SALIDA] {coin} - actualización detenida por usuario")


if __name__ == "__main__":
    main()
