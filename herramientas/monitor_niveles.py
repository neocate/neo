# ---------------------------------------------------------------
# monitor_niveles.py - Monitorea el precio EN VIVO contra los niveles que
# calcula niveles_soporte.py, avisando cuando el precio entra o sale de la
# zona de tolerancia de un nivel vigente (mismo criterio de "toque" que
# _contar_toques: |precio - nivel| <= tolerancia).
#
# Los niveles son una FOTO tomada al arrancar (via _analizar() de
# niveles_soporte.py, sobre el historico ya descargado) - este proceso no
# los recalcula vela a vela. Si el historico cambio mucho desde que arranco,
# cortar (Ctrl+C), volver a bajarlo/correr niveles_soporte.py, y reiniciar.
#
# Lee (tail) el CSV que escribe grabador_libro.py - NO pide nada a la API
# por su cuenta. Asume que grabador_libro.py esta corriendo por separado y
# de forma permanente (version estable: se lanza en el NAS y queda), como
# ya graba bid/ask/mid/imbalance/cvd cada --cada suyo, este proceso solo
# tiene que leerlo. Si grabador_libro.py no esta corriendo (o no genero
# todavia el CSV del dia), este monitor espera y avisa - no lo arranca el
# solo (son procesos separados a proposito, ver cabecera de grabador_libro.py).
#
# Localiza el archivo mas reciente en herramientas/libro/flujo_*.csv que
# incluya la moneda pedida (mismo patron de nombre que grabador_libro.py.
# _archivo). Al arrancar se posiciona al FINAL del archivo (no relee
# historico viejo) y cada --cada segundos lee solo las lineas nuevas
# agregadas desde la ultima vuelta.
#
# OJO: un "toque" en vivo (precio entra en nivel +/- tolerancia) NO es lo
# mismo que "roto" en niveles_soporte.py (que exige --confirmacion-velas
# CIERRES DE VELA consecutivos). Esto es aviso en tiempo real de que el
# precio esta ahi - no una confirmacion definitiva de ruptura. Para eso,
# correr niveles_soporte.py de nuevo despues y mirar el estado.
#
# Con --tf-macro, igual que en niveles_soporte.py: los niveles del TF
# principal se acotan al rango [suelo_macro, techo_macro] antes de vigilarlos
# (mismos k/tolerancia-atr/toques-min para el macro). Los dos topes macro se
# vigilan tambien, marcados [macro] en los avisos.
#
# Avisos: por consola en vivo, Y a un CSV en herramientas/libro/
# (avisos_<fecha>_<coin>_<tf>.csv) - timestamp, evento, nivel, precio,
# imbalance, cvd en ese momento, para revisar despues o cruzar con el resto.
#
# Ademas de los toques de nivel, cada --velas-cada segundos se revisa si
# cerro una vela nueva del TF vigilado y se evaluan sobre ella las señales
# de mercado/senales.py (impulso, ruptura, rechazo, divergencia RSI) - se
# graban en el MISMO CSV de avisos, con origen "vela" y nivel_precio vacio
# (evento="senal", tipo=nombre de la señal), para tenerlo todo junto con
# los toques de nivel y el imbalance/cvd del momento.
#
# Las señales se evaluan CON los niveles vigentes de este mismo TF (la
# misma foto de _armar_watchlist, sin recalcular) - de las 7 que
# mercado/senales.NIVEL_UTIL_GRUPO_A puede renombrar/filtrar segun el
# contexto de nivel, solo las 4 de senales.REFINADAS_CONFIRMADAS (edge
# positivo y consistente en el backtest BTC+ETH 2018-2022) se acaban
# logueando aqui; las 3 de REFINADAS_EN_PRUEBAS se descartan (no
# confirmadas todavia - ver mercado/senales.py). El resto de señales
# (fuera del Grupo A) no se ven afectadas por esto.
#
# Cada una de esas 4 confirmadas TAMBIEN se manda a Telegram via
# alertas.avisos.enviar(). Sin TELEGRAM_TOKEN/TELEGRAM_CHAT_ID en .env,
# enviar() no hace nada - no hace falta configurarlo para que el monitor
# funcione. Los toques de nivel y las señales sin confirmar NO se mandan,
# solo se quedan en el CSV - mandar cada toque seria demasiado ruido.
#
# Historico ANTES de arrancar (2026-08-07): niveles_soporte.py lee de
# herramientas/libro/historico_<COIN>_<TF>_bitget.csv (ver su cabecera,
# ahora Bitget en vez de Binance). Si ese fichero no existe o no llega a
# --desde-dias (90 por defecto) hacia atras, _analizar() calcularia los
# niveles con lo poco que hay - o directamente fallaria si no hay fichero.
# Antes de tocar niveles_soporte.py, este script se ASEGURA de tener esa
# profundidad para TIMEFRAMES_NIVELES (1m a 1d, el mismo TF vigilado y
# --tf-macro incluidos aunque no esten en esa lista) - ver
# _asegurar_historico(). grabador_libro.py YA descarga estos mismos TF (ver
# TIMEFRAMES_VELAS) pero con desde=0 (arranca desde AHORA, sin historia
# atras - le basta para su "foto en vivo") - ese fichero, si ya existe pero
# no llega a los 90 dias, no sirve para detectar niveles y hay que
# re-bajarlo completo.
#
# Uso:
#   python herramientas/monitor_niveles.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 4 [--desde-dias 90] [--tf-macro 4h] [--cada 15] [--velas-cada 60]
#
# Ejemplo:
#   python herramientas/monitor_niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4 --desde-dias 90 --tf-macro 4h
# ---------------------------------------------------------------

import csv
import io
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.niveles_soporte import _analizar
from herramientas.grabador_libro import CAMPOS_CSV as CAMPOS_LIBRO
from herramientas.descargar_bit import (
    DIR_LIBRO, _archivo as _archivo_bitget,
    descargar as _descargar_bitget, actualizar as _actualizar_bitget,
)
from mercado import senales
from alertas import avisos

CAMPOS_AVISOS = ["timestamp_ms", "fecha_utc", "coin", "evento", "tipo", "origen",
                  "nivel_precio", "precio_actual", "imbalance", "cvd"]

# Mismo set que grabador_libro.TIMEFRAMES_VELAS + "1d" (niveles_soporte.py
# SI usa 1d como --tf-macro habitual, grabador_libro.py no lo necesitaba).
TIMEFRAMES_NIVELES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
DIAS_HISTORICO_DEFAULT = 90.0


def _fmt_fecha_ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _flt(s):
    """Parsea un campo del CSV de grabador_libro.py a float, o None si esta
    vacio (API fallo esa vuelta - ver grabador_libro.py._fila)."""
    if s in (None, ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _localizar_csv_libro(coin):
    """Busca en herramientas/libro/ el CSV de grabador_libro.py vigente que
    incluya esta moneda (nombre flujo_<fecha>_<MONEDA1-MONEDA2...>[_vN].csv -
    ver grabador_libro.py._archivo/_ruta_compatible). Si hay varios (dias
    distintos, o _v2/_v3 por cabecera cambiada), toma el modificado mas
    reciente. None si no encuentra ninguno."""
    if not os.path.isdir(DIR_LIBRO):
        return None
    candidatos = []
    for nombre in os.listdir(DIR_LIBRO):
        m = re.match(r"^flujo_\d+_(.+?)(?:_v\d+)?\.csv$", nombre)
        if not m:
            continue
        monedas = m.group(1).split("-")
        if coin.upper() in monedas:
            ruta = os.path.join(DIR_LIBRO, nombre)
            candidatos.append((os.path.getmtime(ruta), ruta))
    if not candidatos:
        return None
    candidatos.sort()
    return candidatos[-1][1]


def _tail_csv(ruta, offset):
    """Lee las lineas COMPLETAS agregadas a 'ruta' desde 'offset' bytes (una
    fila a medio escribir se deja sin consumir para la proxima vuelta).
    Devuelve (filas_dict, nuevo_offset)."""
    with open(ruta, "rb") as f:
        f.seek(offset)
        data = f.read()
    if not data:
        return [], offset
    corte = data.rfind(b"\n")
    if corte == -1:
        return [], offset
    bloque, nuevo_offset = data[:corte + 1], offset + corte + 1
    texto = bloque.decode("utf-8", errors="replace")
    filas = list(csv.DictReader(io.StringIO(texto), fieldnames=CAMPOS_LIBRO))
    return filas, nuevo_offset


def _velas_nuevas(ruta, ultimo_ts):
    """Filas del historico (mismo formato que descargar_bit.py: timestamp,
    fecha_utc,open,high,low,close,volumen) con timestamp > 'ultimo_ts'.

    Lee solo la COLA del fichero (igual que _ultimo_timestamp_ms de
    descargar_bit.py) en vez de reparsear el historico completo en cada
    refresco - puede tener decenas de miles de filas (90 dias de 1m).
    1MB de cola sobra para cualquier '--velas-cada' razonable."""
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 1_000_000))
        cola = f.read()
    filas = []
    for linea in cola.split(b"\n"):
        linea = linea.strip()
        if not linea or linea.startswith(b"timestamp"):
            continue
        partes = linea.decode("utf-8").split(",")
        ts = int(partes[0])
        if ts > ultimo_ts:
            filas.append([ts, float(partes[2]), float(partes[3]), float(partes[4]),
                          float(partes[5]), float(partes[6])])
    filas.sort(key=lambda v: v[0])
    return filas


def _ultima_fila_coin(ruta, coin):
    """Ultima fila COMPLETA de 'ruta' (flujo_*.csv de grabador_libro.py)
    para 'coin' - lee solo la cola del fichero (puede tener decenas de MB
    tras dias corriendo) en vez de todo, mismo patron que _velas_nuevas.
    Se descarta la primera linea de la cola por si quedo cortada a medias
    por el propio seek - sobra margen (256KB) para que la ultima fila de
    CUALQUIER moneda siga estando mas adelante. None si no hay ninguna."""
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 262_144))
        cola = f.read()
    lineas = [l for l in cola.decode("utf-8", errors="replace").split("\n") if l.strip()]
    if len(lineas) > 1:
        lineas = lineas[1:]
    for linea in reversed(lineas):
        campos = next(csv.reader([linea]))
        if len(campos) != len(CAMPOS_LIBRO):
            continue
        fila = dict(zip(CAMPOS_LIBRO, campos))
        if fila.get("coin") == coin.upper():
            return fila
    return None


def _primer_timestamp_ms(ruta):
    """Timestamp (ms) de la PRIMERA fila de velas (justo despues de la
    cabecera) - para saber que tan atras llega ya el historico guardado,
    sin cargar el fichero entero (los 1m de 90 dias son ~130k filas)."""
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r, None)  # cabecera
        primera = next(r, None)
    return int(primera[0]) if primera else None


def _con_lock_historico(ruta, dias, coin, tf, espera=2.0, timeout=1800.0):
    """Descarga completa (descargar()) de UN TF con un lock de fichero
    (creacion atomica O_CREAT|O_EXCL) para que, si hay OTRO monitor_niveles.py
    corriendo a la vez sobre la MISMA moneda/TF, no disparen los dos la misma
    descarga completa en paralelo - descargar() abre el CSV en modo 'w' y lo
    REESCRIBE entero, asi que dos procesos escribiendolo a la vez lo dejarian
    con filas mezcladas/truncadas.

    El que consigue el lock baja de verdad; el que lo pierde espera (polling,
    'espera' segundos) a que el otro termine y borre el lock, y entonces solo
    hace actualizar() - para cuando el primero acabo, el fichero ya tiene la
    profundidad pedida, asi que un update (rapido) es suficiente, no hace
    falta repetir la descarga completa. 'timeout' es una salvaguarda por si
    el lock queda huerfano (proceso matado a mitad) - no se borra solo, pero
    al menos no se espera para siempre."""
    ruta_lock = ruta + ".lock"
    try:
        fd = os.open(ruta_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        esperado = 0.0
        while os.path.exists(ruta_lock) and esperado < timeout:
            time.sleep(espera)
            esperado += espera
        print(f"  {coin.upper()} {tf}: otro proceso ya estaba bajando el historico, "
              f"actualizando en vez de repetir la descarga completa...")
        _actualizar_bitget(coin, tf, desde=dias)
        return

    try:
        print(f"  Bajando historico Bitget {coin.upper()} {tf} ({dias:.0f}d)...")
        _descargar_bitget(coin, tf, desde=dias)
    finally:
        try:
            os.remove(ruta_lock)
        except OSError:
            pass


def _asegurar_historico(coin, tfs, dias):
    """Antes de que niveles_soporte.py pueda calcular nada, se necesita
    historico de Bitget con al menos 'dias' de profundidad para cada TF en
    'tfs' - si no, _analizar()/_cargar_velas fallaria (sin fichero) o
    trabajaria con lo poco que haya desde que arranco este proceso, sin
    detectar niveles reales (ver cabecera del archivo).

    actualizar() por si sola NO sirve para esto si el fichero YA existe:
    ignora 'desde' y solo AÑADE velas nuevas desde la ultima guardada, no
    rellena hacia atras (ver descargar_bit.py) - asi que si grabador_libro.py
    ya creo el CSV con desde=0 (su propio criterio, sin historia), hay que
    detectarlo (mirando el timestamp de la PRIMERA fila) y forzar una
    descarga completa con descargar(desde=dias) para tener los 'dias' de
    verdad. Si el fichero ya llega tan atras (o mas), solo se actualiza
    (rapido, unas pocas velas nuevas). La descarga completa pasa por
    _con_lock_historico (2026-08-08, a peticion de Fran: lanzar dos
    monitor_niveles.py a la vez -p.ej. dos TF distintos de la misma moneda-
    disparaba la misma descarga completa duplicada en los dos)."""
    corte_ms = int((datetime.now(timezone.utc) - timedelta(days=dias)).timestamp() * 1000)
    for tf in tfs:
        ruta = _archivo_bitget(coin, tf)
        primero = _primer_timestamp_ms(ruta) if os.path.exists(ruta) else None
        if primero is None or primero > corte_ms:
            _con_lock_historico(ruta, dias, coin, tf)
        else:
            _actualizar_bitget(coin, tf, desde=dias)


def _armar_watchlist(coin, tf, k, tolerancia_atr, toques_min, desde_dias,
                      confirmacion_velas, tf_macro):
    """Devuelve (watchlist, tolerancia_micro) - watchlist es una lista de
    dicts {precio, tipo, origen, tolerancia} a vigilar. Sin --tf-macro es
    simplemente todo lo vigente del TF principal; con --tf-macro se acota
    al rango macro (igual que niveles_soporte.py) y se agregan los dos
    topes macro marcados como tal."""
    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    tolerancia = r["tolerancia"]

    if tf_macro is None:
        watch = [dict(precio=n["precio"], tipo=n["tipo"], origen="micro", tolerancia=tolerancia)
                 for n in r["techos"] + r["suelos"]]
        return watch, r

    rm = _analizar(coin, tf_macro, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    techo_macro = min((n for n in rm["techos"] if n["dist_pct"] > 0),
                       key=lambda d: d["dist_pct"], default=None)
    suelo_macro = max((n for n in rm["suelos"] if n["dist_pct"] < 0),
                       key=lambda d: d["dist_pct"], default=None)
    lo = suelo_macro["precio"] if suelo_macro else float("-inf")
    hi = techo_macro["precio"] if techo_macro else float("inf")

    watch = [dict(precio=n["precio"], tipo=n["tipo"], origen="micro", tolerancia=tolerancia)
             for n in r["techos"] + r["suelos"] if lo <= n["precio"] <= hi]
    if techo_macro:
        watch.append(dict(precio=techo_macro["precio"], tipo="techo", origen="macro",
                           tolerancia=rm["tolerancia"]))
    if suelo_macro:
        watch.append(dict(precio=suelo_macro["precio"], tipo="suelo", origen="macro",
                           tolerancia=rm["tolerancia"]))
    return watch, r


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return
    coin, tf = args[0], args[1]
    resto = args[2:]

    k = tolerancia_atr = toques_min = None
    desde_dias = None
    confirmacion_velas = 2
    tf_macro = None
    cada = 15.0
    velas_cada = 60.0
    i = 0
    while i < len(resto):
        if resto[i] == "--k":
            i += 1; k = int(resto[i])
        elif resto[i] == "--tolerancia-atr":
            i += 1; tolerancia_atr = float(resto[i])
        elif resto[i] == "--toques-min":
            i += 1; toques_min = int(resto[i])
        elif resto[i] == "--desde-dias":
            i += 1; desde_dias = float(resto[i])
        elif resto[i] == "--confirmacion-velas":
            i += 1; confirmacion_velas = int(resto[i])
        elif resto[i] == "--tf-macro":
            i += 1; tf_macro = resto[i]
        elif resto[i] == "--cada":
            i += 1; cada = float(resto[i])
        elif resto[i] == "--velas-cada":
            i += 1; velas_cada = float(resto[i])
        i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parametros obligatorios: --k, --tolerancia-atr, --toques-min")
        print("(sin defaults a proposito - ver cabecera de niveles_soporte.py)")
        return

    dias_historico = max(desde_dias or 0.0, DIAS_HISTORICO_DEFAULT)
    tfs_asegurar = sorted(set(TIMEFRAMES_NIVELES) | {tf} | ({tf_macro} if tf_macro else set()))
    print(f"Comprobando historico Bitget de {coin.upper()} ({dias_historico:.0f}d, "
          f"{', '.join(tfs_asegurar)})...")
    _asegurar_historico(coin, tfs_asegurar, dias_historico)

    watch, r = _armar_watchlist(coin, tf, k, tolerancia_atr, toques_min, desde_dias,
                                 confirmacion_velas, tf_macro)
    if not watch:
        print("No hay niveles vigentes para vigilar con estos parametros.")
        return

    os.makedirs(DIR_LIBRO, exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    ruta_log = os.path.join(DIR_LIBRO, f"avisos_{fecha}_{coin.upper()}_{tf}.csv")
    nuevo = not os.path.exists(ruta_log)
    log = open(ruta_log, "a", newline="")
    writer = csv.DictWriter(log, fieldnames=CAMPOS_AVISOS)
    if nuevo:
        writer.writeheader()
        log.flush()

    print(f"Vigilando {coin.upper()} {tf}" + (f" (acotado a rango macro {tf_macro})" if tf_macro else ""))
    print(f"Precio de referencia al armar niveles: {r['precio_actual']:.4f}")
    print(f"{len(watch)} niveles vigilados:")
    for niv in sorted(watch, key=lambda d: -d["precio"]):
        etiqueta = f"[{niv['origen']}]" if niv["origen"] == "macro" else ""
        print(f"  {niv['tipo']:<6} {niv['precio']:>12.4f}  tolerancia {niv['tolerancia']:.4f} {etiqueta}")
    print(f"Log de avisos -> {ruta_log}")

    ruta_libro = _localizar_csv_libro(coin)
    while ruta_libro is None:
        print(f"Esperando a que grabador_libro.py genere el CSV de {coin.upper()} en "
              f"{DIR_LIBRO} (¿esta corriendo?). Reintento cada {cada:.0f}s...")
        time.sleep(cada)
        ruta_libro = _localizar_csv_libro(coin)
    print(f"Leyendo de {ruta_libro} (solo lineas nuevas desde ahora).")

    offset = os.path.getsize(ruta_libro)
    for niv in watch:
        niv["tocando"] = False
    ultimo_imbalance = ultimo_cvd = None

    # Estado inicial desde la ULTIMA fila ya grabada (no se relee ni se
    # avisa nada del historico viejo - offset ya quedo al final, arriba -
    # esto solo evita arrancar a ciegas: sin esto, imbalance/cvd empiezan
    # en None y todos los niveles en tocando=False aunque el precio YA
    # estuviera dentro de la tolerancia de alguno, hasta que llegue el
    # primer tick nuevo.
    fila_inicial = _ultima_fila_coin(ruta_libro, coin)
    if fila_inicial:
        precio_inicial = _flt(fila_inicial.get("mid"))
        if precio_inicial is None:
            bid, ask = _flt(fila_inicial.get("bid")), _flt(fila_inicial.get("ask"))
            precio_inicial = (bid + ask) / 2 if (bid is not None and ask is not None) else None
        ultimo_imbalance = _flt(fila_inicial.get("imbalance"))
        ultimo_cvd = _flt(fila_inicial.get("cvd"))
        if precio_inicial is not None:
            for niv in watch:
                niv["tocando"] = (niv["precio"] - niv["tolerancia"]) <= precio_inicial <= (niv["precio"] + niv["tolerancia"])
            print(f"Estado inicial ({fila_inicial.get('fecha_utc', '?')}): precio {precio_inicial:.4f}  "
                  f"imbalance {(ultimo_imbalance or 0.0):+.2f}  cvd {(ultimo_cvd or 0.0):+.4f}")
            for niv in watch:
                if niv["tocando"]:
                    etiqueta = f" [{niv['origen']}]" if niv["origen"] == "macro" else ""
                    print(f"  ya esta tocando {niv['tipo']} {niv['precio']:.4f}{etiqueta}")

    print("Ctrl+C para parar.\n")

    # Solo se necesita historico reciente para las señales (ver
    # senales.VENTANA_MAXIMA) - r["velas"] trae hasta 90 dias completos
    # (para detectar_niveles), pero arrastrar eso en memoria y recortarlo de
    # nuevo en CADA vela nueva durante una sesion larga es trabajo de sobra.
    velas = r["velas"][-senales.VENTANA_MAXIMA:]
    ruta_historico = _archivo_bitget(coin, tf)
    ultimo_ts_vela = velas[-1][0] if velas else None

    # Niveles para el filtro de contexto del Grupo A (ver cabecera) - se
    # usa r["techos"]/r["suelos"] (ya agrupados por ROL EFECTIVO, con el
    # flip aplicado - ver _rol_efectivo en niveles_soporte.py) en vez del
    # campo "tipo" de 'watch', que para un nivel con flip sigue guardando
    # el tipo ORIGINAL. Misma tolerancia que ya usa la watchlist del TF
    # micro (r["tolerancia"]) - sin --tf-macro en esto, igual que el
    # backtest que lo valido (ver herramientas/backtest_senales.py).
    niveles_vigentes = ([(n["precio"], "techo") for n in r["techos"]]
                         + [(n["precio"], "suelo") for n in r["suelos"]])
    tolerancia_nivel = r["tolerancia"]
    ultimo_refresco_velas = time.monotonic()
    print(f"Señales de vela ({tf}) revisadas cada {velas_cada:.0f}s tras refrescar el historico.\n")

    try:
        while True:
            filas, offset = _tail_csv(ruta_libro, offset)
            for fila in filas:
                if fila.get("coin") != coin.upper():
                    continue

                precio_actual = _flt(fila.get("mid"))
                if precio_actual is None:
                    bid, ask = _flt(fila.get("bid")), _flt(fila.get("ask"))
                    precio_actual = (bid + ask) / 2 if (bid is not None and ask is not None) else None
                if precio_actual is None:
                    continue

                imbalance_val = _flt(fila.get("imbalance"))
                if imbalance_val is not None:
                    ultimo_imbalance = imbalance_val
                cvd = _flt(fila.get("cvd"))
                if cvd is not None:
                    ultimo_cvd = cvd

                techo_cercano = min((n for n in watch if n["precio"] > precio_actual),
                                     key=lambda d: d["precio"] - precio_actual, default=None)
                suelo_cercano = max((n for n in watch if n["precio"] < precio_actual),
                                     key=lambda d: d["precio"], default=None)
                resumen = (f"{fila.get('fecha_utc', _fmt_fecha_ahora())}  precio {precio_actual:>12.4f}  "
                           f"imbalance {ultimo_imbalance if ultimo_imbalance is not None else 0.0:>+6.2f}  "
                           f"cvd {ultimo_cvd if ultimo_cvd is not None else 0.0:>+10.4f}")
                if techo_cercano:
                    resumen += f"  | techo mas cerca {techo_cercano['precio']:.4f}"
                if suelo_cercano:
                    resumen += f"  | suelo mas cerca {suelo_cercano['precio']:.4f}"
                print(resumen)

                for niv in watch:
                    cerca = (niv["precio"] - niv["tolerancia"]) <= precio_actual <= (niv["precio"] + niv["tolerancia"])
                    if cerca and not niv["tocando"]:
                        niv["tocando"] = True
                        evento = "tocando"
                    elif not cerca and niv["tocando"]:
                        niv["tocando"] = False
                        lado = "arriba" if precio_actual > niv["precio"] else "abajo"
                        evento = f"sale_hacia_{lado}"
                    else:
                        continue

                    etiqueta = f" [{niv['origen']}]" if niv["origen"] == "macro" else ""
                    print(f"  >>> {evento.upper()} {niv['tipo']} {niv['precio']:.4f}{etiqueta}  "
                          f"precio {precio_actual:.4f}  imbalance {(ultimo_imbalance or 0.0):+.2f}  "
                          f"cvd {(ultimo_cvd or 0.0):+.4f}")
                    writer.writerow({
                        "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                        "fecha_utc": _fmt_fecha_ahora(),
                        "coin": coin.upper(),
                        "evento": evento,
                        "tipo": niv["tipo"],
                        "origen": niv["origen"],
                        "nivel_precio": niv["precio"],
                        "precio_actual": precio_actual,
                        "imbalance": round(ultimo_imbalance, 4) if ultimo_imbalance is not None else "",
                        "cvd": round(ultimo_cvd, 4) if ultimo_cvd is not None else "",
                    })
                    log.flush()

            if ultimo_ts_vela is not None and time.monotonic() - ultimo_refresco_velas >= velas_cada:
                ultimo_refresco_velas = time.monotonic()
                _actualizar_bitget(coin, tf, desde=desde_dias)
                for nueva in _velas_nuevas(ruta_historico, ultimo_ts_vela):
                    velas.append(nueva)
                    del velas[:-senales.VENTANA_MAXIMA]
                    ultimo_ts_vela = nueva[0]
                    activas = senales.detectar(velas, k, niveles_vigentes=niveles_vigentes,
                                                tolerancia_nivel=tolerancia_nivel)
                    for nombre in activas:
                        if nombre in senales.REFINADAS_EN_PRUEBAS:
                            continue
                        fecha_vela = datetime.fromtimestamp(nueva[0] / 1000, timezone.utc)
                        print(f"  >>> SENAL {nombre}  cierre {nueva[4]:.4f}  "
                              f"({fecha_vela:%Y-%m-%d %H:%M} UTC, vela {tf})")
                        writer.writerow({
                            "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                            "fecha_utc": _fmt_fecha_ahora(),
                            "coin": coin.upper(),
                            "evento": "senal",
                            "tipo": nombre,
                            "origen": "vela",
                            "nivel_precio": "",
                            "precio_actual": nueva[4],
                            "imbalance": round(ultimo_imbalance, 4) if ultimo_imbalance is not None else "",
                            "cvd": round(ultimo_cvd, 4) if ultimo_cvd is not None else "",
                        })
                        log.flush()
                        avisos.enviar(
                            f"{coin.upper()} {tf}  {nombre}\n"
                            f"cierre {nueva[4]:.4f}  ({fecha_vela:%Y-%m-%d %H:%M} UTC)"
                        )

            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")
    finally:
        log.close()


if __name__ == "__main__":
    main()
