# ---------------------------------------------------------------
# telegram_control.py - Despachador de comandos de Telegram (2026-08-03,
# botones inline añadidos 2026-08-04, adaptado a la rama unica 2026-08-07)
#
# UNICO proceso que escucha Telegram (getUpdates) - varios monitor.py no
# pueden hacerlo cada uno por su cuenta sin pisarse (Telegram reparte los
# mensajes entre quien pregunte primero, no los duplica). Los monitor.py ya
# NO mandan avisos automaticos (ver monitor.py 2026-08-03) - todo es bajo
# demanda, a traves de este script.
#
# Desde el 2026-08-07 monitor.py corre UNA sola configuracion (ver
# anotaciones.md "Rama unica" y monitor.py main()): veto DI del arbitro +
# filtro de regimen + salida RR fija. Ya no existen las ramas "veto"/"sma"
# como procesos separados - la clave "libre" se mantiene solo por
# compatibilidad con los CSV/comandos existentes. Por eso el asistente de
# botones YA NO pregunta "que rama": ajustar un parametro va directo de
# "que quiero hacer" a "que indicador".
#
# Los comandos de texto de siempre siguen funcionando tal cual (utiles para
# guiones/automatizacion). Ademas, /help (o help) manda un menu de BOTONES:
#   abiertas (todo) / por moneda / por TF - operaciones ABIERTAS ahora mismo
#     (lado, entrada, stop, objetivo, pnl), no un log de eventos - ver
#     cmd_resumen. Las opciones de moneda/TF salen de los procesos
#     monitor.py EN EJECUCION ahora mismo (via WMI, ver _en_ejecucion), no
#     de una lista fija.
#   cartera (todo) - agregado n operaciones/+/-/capital por moneda+TF,
#     ver cmd_cartera. Solo el boton "todo" - coin/tf de cartera son texto
#     libre por ahora (cartera/coin/tf), sin asistente de botones.
#   open - pulsar pide la moneda como TEXTO LIBRE (escribela y la manda),
#     luego pide el TF igual (texto libre) y concatena open/coin/tf.
#   ajustar parametro - todo por botones: indicador (SOLO los de
#     PARAMS_TELEGRAM - 2026-08-10, recorte a los que de verdad deciden
#     entrada/riesgo/veto/salida/tamaño, el resto de monitor.PARAMS_AJUSTABLES
#     ya no se ofrece aqui, ver el comentario junto a PARAMS_TELEGRAM) ->
#     valor (2-3 sugeridos + un boton "escribir valor" que cae a texto
#     libre) -> TF (las franjas en ejecucion + "todas").
#   reset (2026-08-10) - devuelve TODOS los PARAMS_TELEGRAM a su valor de
#     ARRANQUE (ver cmd_reset/_valores_originales) de una vez, deshaciendo
#     cualquier ajuste en caliente hecho desde este script -> TF (las
#     franjas en ejecucion + "todas").
# Un mensaje de texto libre a medias (open o "escribir valor") se puede
# cortar con el boton "cancelar", o pulsando cualquier otro boton del menu.
#
# Comandos (mandalos como texto normal al bot):
#   resumen                 - operaciones ABIERTAS ahora, todas las monedas/TF
#   resumen/coin            - igual, filtrado a esa moneda (todas las TF)
#   resumen/tf              - igual, filtrado a esa franja (todas las monedas)
#   resumen/coin/tf         - igual, filtrado a esa moneda y franja
#   cartera                 - agregado por moneda/TF DESDE QUE ARRANCO CADA
#                             PROCESO (no solo hoy): nº operaciones
#                             cerradas, ganadas/perdidas y capital actual
#   cartera/coin            - igual, filtrado a esa moneda (todas las TF)
#   cartera/coin/tf         - igual, filtrado a esa moneda y franja
#   open/coin/tf            - abre una consola nueva de monitor.py para esa
#                             moneda/franja (si no hay ya una corriendo)
#   indicador/valor         - cambia un parametro EN CALIENTE, en TODAS las
#                             franjas corriendo (ej. exit_rr/2.5) - solo
#                             para los de PARAMS_TELEGRAM
#   indicador/valor/tf      - igual, solo en esa franja (ej. stop_atr/1.2/15m)
#   reset                   - vuelve TODOS los parametros de PARAMS_TELEGRAM a
#                             su valor de ARRANQUE (ver _valores_originales),
#                             en TODAS las franjas - deshace cualquier ajuste
#                             en caliente hecho desde aqui
#   reset/tf                - igual, solo en esa franja (ej. reset/15m)
#   /help                   - manda el menu de botones
#   /help/indicador         - descripcion de ese parametro (ej. /help/exit_rr)
#
# Requiere TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en .env (mismos que avisos.py).
# Solo acepta mensajes/botones de ESE chat_id, ignora cualquier otro.
#
# Uso:
#   python telegram_control.py
# ---------------------------------------------------------------

import csv
import glob
import json
import os
import re
import subprocess
import time

import requests

from alertas import avisos
import monitor

COMANDOS_DIR = "comandos"
OFFSET_FILE = "telegram_offset.txt"

# Contador de proceso para _nombre_comando() - se reinicia si
# telegram_control.py se reinicia, no importa: solo hace falta que no
# colisione DENTRO de una rafaga de comandos seguidos (ver cmd_reset()).
_CONTADOR_CMD = 0


def _nombre_comando():
    """Nombre de archivo UNICO para un comando/*.json.

    2026-08-10: time.time()*1000 (resolucion de milisegundo) colisionaba
    cuando varios comandos se escriben en rafaga - cmd_reset() escribe uno
    por CADA parametro de PARAMS_TELEGRAM en el mismo bucle Python, en
    bastante menos de 1ms, y el segundo pisaba al primero antes de que
    monitor.py llegara a leerlo (visto en vivo: 13 llamadas, 8 archivos).
    Nanosegundos (time.time_ns()) + un contador de proceso: incluso si dos
    llamadas cayeran en el mismo nanosegundo (no deberia, pero por si
    acaso), el contador ya las distingue."""
    global _CONTADOR_CMD
    _CONTADOR_CMD += 1
    return f"cmd_{time.time_ns()}_{_CONTADOR_CMD}.json"

# Rama unica que corre monitor.py hoy (ver monitor.py main(), 2026-08-07):
# la clave literal del (unico) dict `ramas` en main() sigue siendo "libre"
# por compatibilidad con los CSV/comandos de antes de la fusion, aunque ya
# no significa "sin veto" - es la fusion completa (veto DI + regimen + RR
# fijo). Todo comando/callback de este script apunta aqui, fijo a proposito.
RAMA = "libre"

# 2-3 valores razonables por parametro, para que el paso "valor" del
# asistente de ajuste sea botones y no obligue a escribir siempre.
# Curados a partir de lo discutido en anotaciones.md/VETO_TF.md - no son
# limites, son puntos de partida razonables para tantear. Recortado
# 2026-08-10 al mismo set que PARAMS_TELEGRAM (ver mas abajo) - los
# parametros que ya no se ofrecen en Telegram no necesitan sugerencias aqui.
VALORES_SUGERIDOS = {
    "ventana": ["20", "30", "50"],
    "fraccion_entrada": ["1", "2", "4"],
    "leverage": ["5", "10", "20"],
    "stop_atr": ["0.5", "1.0", "1.5"],
    "impulso_lookback": ["3", "4", "6"],
    "impulso_min_atr": ["2.5", "3.0", "3.5"],
    "aceleracion_ventana": ["4", "6", "8"],
    "aceleracion_min_atr": ["0.5", "0.8", "1.2"],
    "aceleracion_mult": ["2.0", "2.5", "3.0"],
    "regimen_tf": ["1d", "4h", "-"],
    "regimen_sma": ["20", "50", "100"],
    "exit_rr": ["2", "3", "4"],
    "posiciones_max": ["1", "3", "5"],
}

# Etiquetas legibles SOLO para los botones del menu "Ajustar parametro" -
# los nombres crudos de PARAMS_AJUSTABLES (fraccion_entrada, etc.) estan
# pensados para escribirse en comandos de texto/scripts, lioso para quien
# los ve por primera vez en un boton (Fran, 2026-08-05). La CLAVE interna
# (indicador, usada en el comando/callback_data) NO cambia - solo el texto
# que se muestra. Recortado 2026-08-10 al set de PARAMS_TELEGRAM.
ETIQUETAS_INDICADOR = {
    "ventana": "Ventana de rango (velas)",
    "fraccion_entrada": "% margen por entrada",
    "leverage": "Apalancamiento (x)",
    "stop_atr": "Stop inicial (x ATR)",
    "impulso_lookback": "Velas para medir impulso",
    "impulso_min_atr": "Impulso minimo (x ATR)",
    "aceleracion_ventana": "Velas para medir aceleracion",
    "aceleracion_min_atr": "Aceleracion minima (x ATR)",
    "aceleracion_mult": "Aceleracion vs ritmo previo (x)",
    "regimen_tf": "TF del regimen",
    "regimen_sma": "Periodo SMA del regimen",
    "exit_rr": "Objetivo fijo (R:R)",
    "posiciones_max": "Posiciones maximas concurrentes",
}


def _etiqueta(indicador):
    """Texto de boton para un indicador - la clave cruda si no hay etiqueta
    curada (fallback seguro si PARAMS_AJUSTABLES gana un parametro nuevo
    antes de que alguien lo añada aqui)."""
    return ETIQUETAS_INDICADOR.get(indicador, indicador)


# UNICOS parametros tocables desde Telegram (2026-08-10, recorte a peticion
# de Fran: "de todos los valores, cuales son los mas necesarios de ir
# tocando" - antes se exponian los 31 de monitor.PARAMS_AJUSTABLES enteros,
# esenciales primero y el resto detras de un boton "Ampliar"). El resto
# SIGUE existiendo en monitor.py (CLI --flag, y comandos/*.json escritos a
# mano) - aqui simplemente ya no se ofrecen ni se aceptan, porque no vale
# la pena explicar 31 perillas cuando la mayoria son:
#   - cadencia operativa sin edge propio (cada, cada_orden_pendiente,
#     orden_espera_mercado, orden_max_velas, tf_vecino_rapido)
#   - tarifas fijas del exchange, no parametro de estrategia (comision_maker/
#     taker)
#   - señales/vetos que HOY no discriminan o no pueden abrir nada (rsi_bajo/
#     alto ya no abren, solo cierran en una rama inactiva; tf_arbitro/
#     di_separacion - el propio backtest de monitor.py dice "no discrimina
#     nada, ~87% vs ~91%")
#   - codigo MUERTO con la config fija actual (escalera_*, trailing_giveback/
#     armado_atr - exit_rr>0 SIEMPRE desde 2026-08-07, ver monitor.py main())
#   - efecto menor (imb_umbral/imb_confirmaciones)
# ORDEN deliberado (no alfabetico): el ciclo de una operacion real - que
# abre, cuanto arriesga al entrar, el unico veto de direccion validado,
# cuanto puede tener abiertas a la vez, y la ventana de referencia.
PARAMS_TELEGRAM = [
    "impulso_lookback", "impulso_min_atr",
    "aceleracion_ventana", "aceleracion_min_atr", "aceleracion_mult",
    "stop_atr", "exit_rr",
    "regimen_sma", "regimen_tf",
    "fraccion_entrada", "leverage", "posiciones_max",
    "ventana",
]

# Descripciones en lenguaje llano para el menu de botones (2026-08-05) -
# las de PARAMS_AJUSTABLES (monitor.py) son mas tecnicas/precisas, pensadas
# para quien ya conoce el vocabulario del sistema (ATR, tramo, vela de
# señal...) y se siguen usando tal cual en /help/indicador. Estas son solo
# para el paso "¿que valor?" del asistente, así que priorizan que se
# entienda de un vistazo sobre la precision total. Recortado 2026-08-10 al
# set de PARAMS_TELEGRAM - si algun dia gana uno nuevo sin entrada aqui,
# _menu_valores cae al texto tecnico de monitor.py como fallback.
DESCRIPCIONES_MENU = {
    "impulso_lookback": "Nº de velas hacia atrás para medir el impulso. "
        "Señal validada con backtest, pero llega tarde: el movimiento ya "
        "pasó cuando dispara.",
    "impulso_min_atr": "Cuánto tiene que haberse movido el precio (en ATR) "
        "en esas velas para que dispare la entrada por impulso.",
    "aceleracion_ventana": "Nº de velas para medir el ritmo previo de la "
        "señal de aceleración (nueva, sin backtest de trade completo). "
        "Entra antes que impulso, pero con más ruido.",
    "aceleracion_min_atr": "Movimiento mínimo (en ATR) de la ÚLTIMA vela "
        "para que dispare la entrada por aceleración.",
    "aceleracion_mult": "Cuántas veces más rápido que el ritmo previo "
        "tiene que ir la última vela para contar como aceleración real. "
        "Más alto = dispara menos veces, pero más fiables.",
    "stop_atr": "Colchón del stop inicial, en ATR, sobre el extremo de la "
        "vela de señal. Más alto = stop más lejos: pierdes más si falla, "
        "pero menos ruido normal te saca antes de tiempo.",
    "exit_rr": "Objetivo de salida FIJO, en múltiplos del riesgo inicial "
        "(el stop): 3 = cierra al triple de lo que arriesgaste, o al stop "
        "si llega antes. Es el único de 7 mecanismos de salida probados "
        "que dio positivo en backtest de 1 año.",
    "regimen_sma": "Periodo de la media (SMA) del régimen de fondo: precio "
        "por encima = alcista, por debajo = bajista, vetea aperturas "
        "contrarias. 50 es el valor validado con backtest de 9 años "
        "(BTC+ETH) - subirlo lo hace más lento y conservador. El ÚNICO "
        "veto de dirección con evidencia real de que discrimina.",
    "regimen_tf": "Temporalidad de las velas donde se mide el régimen de "
        "fondo (precio vs su media). '1d' por defecto.",
    "fraccion_entrada": "% del capital que arriesgas como margen en cada "
        "entrada NUEVA (se escribe en % humano: 2 = 2%).",
    "leverage": "Apalancamiento: multiplica el margen para dar la "
        "exposición real (nocional) de cada entrada NUEVA.",
    "posiciones_max": "Nº máximo de posiciones de papel CONCURRENTES por "
        "moneda/franja. Una señal en dirección opuesta a alguna ya "
        "abierta no apila un hedge - no abre hasta que esa(s) se cierren.",
    "ventana": "Nº de velas cerradas para el rango máx/mín reciente que "
        "se muestra en consola, y la ventana de referencia de los filtros "
        "de open interest/CVD.",
}


# Parametros donde el numero que se escribe por Telegram es un PORCENTAJE
# humano (2 = 2%), no la fraccion cruda que usa monitor.py internamente
# (0.02) - mismo criterio que ya aplican comision_maker/taker en su propio
# codigo (posicion/posicion.py divide /100.0 ahi). fraccion_entrada NO tenia
# esa conversion y Fran metio "2" queriendo decir 2%, que se guardo literal
# como 200% de margen (ver memoria del incidente, 2026-08-05) - cmd_ajustar()
# divide /100 antes de escribir el comando para los indicadores de este set,
# monitor.py sigue recibiendo/aplicando la fraccion cruda de siempre. La
# linea de comandos (--fraccion-entrada) NO pasa por aqui y sigue en fraccion
# cruda (0.02), sin cambios.
PARAMS_PORCENTAJE_HUMANO = {"fraccion_entrada"}

# Asistente de texto libre a medias, por chat (en memoria - si el proceso
# se reinicia con un paso pendiente, se pierde y hay que volver a pulsar
# el boton; aceptable, no es estado de trading). chat_id (str) -> dict.
PENDIENTE = {}


# ---------------------------------------------------------------- Telegram

def _leer_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE) as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            return 0
    return 0


def _guardar_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def _get_updates(offset, timeout=30):
    url = f"https://api.telegram.org/bot{avisos.TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": timeout}, timeout=timeout + 10)
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as e:
        print(f"(error consultando Telegram: {e})")
        time.sleep(5)
        return []


def _enviar(texto):
    """Trocea en bloques de <4000 caracteres - Telegram corta mensajes largos."""
    for i in range(0, len(texto), 4000):
        avisos.enviar(texto[i:i + 4000])


def _api(metodo, **params):
    """POST generico a la Bot API. None en cualquier fallo (red, 4xx) - el
    llamante decide si eso es grave o no, aqui solo se registra."""
    url = f"https://api.telegram.org/bot{avisos.TOKEN}/{metodo}"
    try:
        r = requests.post(url, json=params, timeout=15)
        if not r.ok:
            print(f"(error Telegram.{metodo}: {r.status_code} {r.text[:200]})")
            return None
        return r.json().get("result")
    except Exception as e:
        print(f"(error llamando a Telegram.{metodo}: {e})")
        return None


def _teclado(filas):
    """filas: lista de filas, cada fila una lista de (texto, callback_data)."""
    return {"inline_keyboard": [
        [{"text": texto, "callback_data": datos} for texto, datos in fila]
        for fila in filas
    ]}


def _enviar_menu(chat_id, texto, filas=None):
    kwargs = {"chat_id": chat_id, "text": texto}
    if filas:
        kwargs["reply_markup"] = _teclado(filas)
    return _api("sendMessage", **kwargs)


def _editar_menu(chat_id, message_id, texto, filas=None):
    """Reescribe el propio mensaje del boton pulsado (el asistente vive en
    un unico mensaje que se va actualizando, no uno nuevo por paso). Si el
    mensaje ya no se puede editar (borrado, o mas de 48h), manda uno nuevo
    para no perder la respuesta en silencio."""
    kwargs = {"chat_id": chat_id, "message_id": message_id, "text": texto}
    kwargs["reply_markup"] = _teclado(filas) if filas else {"inline_keyboard": []}
    if _api("editMessageText", **kwargs) is None:
        _enviar_menu(chat_id, texto, filas)


def _responder_callback(callback_id, texto=None):
    """Apaga el 'reloj de carga' del boton. Sin esto Telegram lo deja
    girando hasta el timeout (~confuso para quien lo pulso desde el movil)."""
    kwargs = {"callback_query_id": callback_id}
    if texto:
        kwargs["text"] = texto
    _api("answerCallbackQuery", **kwargs)


# ---------------------------------------------------------------- resumen

def _archivos_monitor(coin=None, tf=None):
    """Todos los monitor_*.csv en disco, de CUALQUIER fecha en el nombre.

    La fecha del nombre es la del arranque del proceso (_archivo_registro,
    registro/csv_monitor.py) - un --loop que lleva dias corriendo sigue
    escribiendo en el fichero que abrio, aunque ya no sea 'hoy' en UTC."""
    archivos = []
    for ruta in glob.glob("monitor_*.csv"):
        nombre = os.path.basename(ruta)
        # monitor_<fecha>_<monedas>_<tf>[_veto|_sma].csv
        cuerpo = nombre[len("monitor_"):-len(".csv")]
        fecha_nombre, _, resto = cuerpo.partition("_")
        if not fecha_nombre.isdigit() or not resto:
            continue
        partes = resto.split("_")
        rama = "libre"
        if partes[-1] == "veto":
            rama = "veto"; partes = partes[:-1]
        elif partes[-1] == "sma":
            rama = "sma"; partes = partes[:-1]
        tf_archivo = partes[-1]
        monedas = "_".join(partes[:-1])
        if tf and tf_archivo != tf:
            continue
        if coin and coin.upper() not in monedas.upper().split("-"):
            continue
        archivos.append((ruta, tf_archivo, rama))
    return archivos


def _posiciones_abiertas_de(ruta, tf, rama, coin_filtro=None):
    """Operaciones ABIERTAS o con una orden PENDIENTE ahora mismo en
    'ruta' - no un log de eventos, el ESTADO actual de cada una.

    Reconstruido de la fila MAS RECIENTE por moneda: desde que monitor.py
    admite varias posiciones concurrentes (2026-08-10, ver
    monitor.py._revisar/_fila_vuelta), cada una se escribe como una fila
    SEPARADA dentro de la MISMA vuelta - se agrupan todas las filas de esa
    moneda que comparten el fecha_utc mas reciente visto (mismo segundo).

    'estado' distingue una posicion YA ABIERTA (lado/entrada/stop/objetivo/
    pnl) de una orden LIMITE todavia esperando a llenarse (lado/precio
    limite/motivo/vueltas esperando - sin stop/objetivo/pnl, no hay
    posicion real detras todavia). orden_pendiente_* es estado COMPARTIDO
    de la moneda esta vuelta (una sola orden a la vez, ver
    posicion/posicion.py:OrdenPendiente) y puede aparecer repetido en mas
    de una fila del mismo batch - se agrega una sola vez por moneda."""
    ultima_fecha = {}
    filas_por_coin = {}
    with open(ruta, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            coin = row.get("coin")
            if not coin:
                continue
            if coin_filtro and coin.upper() != coin_filtro.upper():
                continue
            fecha = row.get("fecha_utc", "")
            if coin not in ultima_fecha or fecha > ultima_fecha[coin]:
                ultima_fecha[coin] = fecha
                filas_por_coin[coin] = [row]
            elif fecha == ultima_fecha[coin]:
                filas_por_coin[coin].append(row)
    abiertas = []
    for coin, filas in filas_por_coin.items():
        orden_ya_agregada = False
        for row in filas:
            if row.get("posicion_lado"):
                abiertas.append({
                    "coin": coin, "tf": tf, "rama": rama, "estado": "ABIERTA",
                    "lado": row["posicion_lado"],
                    "entrada": row.get("posicion_entrada", ""),
                    "stop": row.get("posicion_stop", ""),
                    "objetivo": row.get("posicion_objetivo", ""),
                    "pnl_neto_usdt": row.get("pnl_neto_usdt", ""),
                    "pnl_neto_pct": row.get("pnl_neto_pct_margen", ""),
                    "hora": row.get("fecha_utc", ""),
                })
            elif row.get("orden_pendiente_lado") and not orden_ya_agregada:
                orden_ya_agregada = True
                abiertas.append({
                    "coin": coin, "tf": tf, "rama": rama, "estado": "PENDIENTE",
                    "lado": row["orden_pendiente_lado"],
                    "entrada": row.get("orden_pendiente_precio", ""),
                    "motivo": row.get("orden_pendiente_motivo", ""),
                    "vueltas": row.get("orden_pendiente_vueltas", ""),
                    "hora": row.get("fecha_utc", ""),
                })
    return abiertas


def _cartera_de(ruta, tf, rama, coin_filtro=None):
    """Agregado por moneda de UN CSV: nº de cierres, ganados/perdidos, pnl
    neto acumulado y capital actual (ultima fila vista de esa moneda) - a
    diferencia de _posiciones_abiertas_de (estado de lo que sigue vivo),
    esto es el resumen de cartera (n operaciones, +/-, capital) que pidio
    Fran. Recorre TODO el fichero, no solo 'hoy' (capital_actual es
    acumulado desde que arranco el proceso - filtrar por hoy dejaria el
    capital a medias). Usa id_cierre (no evento=="cierre") para no perder
    cierres con evento compuesto, ej. "cierre;orden_puesta" - mismas
    trampas de parseo ya documentadas en anotaciones.md.

    'vetos_rvol' (2026-08-07, ver monitor.py cfg["rvol_veta"]): cuenta las
    vueltas con veto_rvol=="SI" - NO son cierres (no tienen id_cierre), son
    ordenes de aceleracion_alza/baja que iban a llenarse y no se abrieron
    por RVOL bajo. Se cuentan aparte, no se mezclan con 'n'/'perdidas'."""
    datos = {}
    with open(ruta, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            coin = row.get("coin")
            if not coin:
                continue
            if coin_filtro and coin.upper() != coin_filtro.upper():
                continue
            d = datos.setdefault(coin, {"n": 0, "ganadas": 0, "perdidas": 0,
                                         "pnl_neto": 0.0, "capital": None,
                                         "vetos_rvol": 0})
            cap = row.get("capital_actual")
            if cap not in ("", None):
                d["capital"] = float(cap)
            if row.get("id_cierre"):
                pnl = row.get("cierre_pnl_neto_usdt")
                if pnl not in ("", None):
                    pnl = float(pnl)
                    d["n"] += 1
                    d["pnl_neto"] += pnl
                    if pnl > 0:
                        d["ganadas"] += 1
                    else:
                        d["perdidas"] += 1
            if (row.get("veto_rvol") or "").strip() == "SI":
                d["vetos_rvol"] += 1
    return [{"coin": coin, "tf": tf, "rama": rama, **vals}
            for coin, vals in sorted(datos.items())]


def cmd_cartera(coin=None, tf=None):
    archivos = _archivos_monitor(coin, tf)
    if not archivos:
        return "Sin datos con ese filtro."
    filas = []
    for ruta, tf_a, rama in archivos:
        filas += _cartera_de(ruta, tf_a, rama, coin)
    if not filas:
        return "Sin datos con ese filtro."
    filas.sort(key=lambda d: (d["tf"], d["rama"], d["coin"]))
    L = ["CARTERA" + (f" | {coin}" if coin else "") + (f" | {tf}" if tf else "")]
    n_tot = pnl_tot = vetos_tot = 0
    for d in filas:
        cap = f"{d['capital']:.4f}" if d["capital"] is not None else "?"
        veto_txt = f"  vetosRVOL={d['vetos_rvol']}" if d["vetos_rvol"] else ""
        L.append(f"{d['tf']}/{d['rama']} {d['coin']}: {d['n']} ops "
                 f"({d['ganadas']}+/{d['perdidas']}-) "
                 f"pnl={d['pnl_neto']:+.4f} cap={cap}{veto_txt}")
        n_tot += d["n"]; pnl_tot += d["pnl_neto"]; vetos_tot += d["vetos_rvol"]
    veto_tot_txt = f", {vetos_tot} vetos RVOL" if vetos_tot else ""
    L.append(f"TOTAL: {n_tot} ops, pnl neto {pnl_tot:+.4f} USDT{veto_tot_txt}")
    return "\n".join(L)


def cmd_resumen(coin=None, tf=None):
    """Operaciones ABIERTAS o con una orden PENDIENTE ahora mismo
    (2026-08-10, ver _posiciones_abiertas_de - antes era un log de
    aperturas/cierres de HOY). No necesita que el proceso siga vivo, solo
    que su CSV este actualizado - lee la fila mas reciente de cada moneda.

    2026-08-10 (2): se agregaron las PENDIENTES - una orden limite puesta
    pero todavia sin llenar quedaba invisible del todo en el primer corte
    de este comando (solo miraba posicion_lado)."""
    archivos = _archivos_monitor(coin, tf)
    if not archivos:
        return "Sin procesos con ese filtro."
    todas = []
    for ruta, tf_a, rama in archivos:
        todas += _posiciones_abiertas_de(ruta, tf_a, rama, coin)
    filtro_txt = (f" | {coin}" if coin else "") + (f" | {tf}" if tf else "")
    if not todas:
        return f"Sin operaciones abiertas ni ordenes pendientes{filtro_txt}."
    todas.sort(key=lambda d: (d["tf"], d["coin"], d["estado"], d["hora"]))
    L = [f"OPERACIONES{filtro_txt}"]
    for d in todas:
        if d["estado"] == "ABIERTA":
            pnl_usdt, pnl_pct = d["pnl_neto_usdt"], d["pnl_neto_pct"]
            pnl_txt = (f"{float(pnl_usdt):+.4f} USDT ({float(pnl_pct):+.1f}%)"
                       if pnl_usdt not in ("", None) else "?")
            L.append(f"{d['tf']} {d['coin']} ABIERTA {d['lado'].upper()} @ {d['entrada']}  "
                     f"| stop {d['stop']}  obj {d['objetivo']}  | pnl {pnl_txt}")
        else:
            L.append(f"{d['tf']} {d['coin']} PENDIENTE {d['lado'].upper()} @ {d['entrada']}  "
                     f"| '{d['motivo']}'  ({d['vueltas']} vueltas esperando)")
    return "\n".join(L)


# ---------------------------------------------------------------- open/coin/tf

_SEP_PROCESOS = "@@SEP@@"


def _procesos_monitor():
    """Lista de command lines de procesos python.exe en marcha (via WMI).

    Get-WmiObject, NO Get-CimInstance/ConvertTo-Json (2026-08-04, visto en
    vivo: uno de los PC de Fran corre Windows 7 de 32 bits con PowerShell
    2.0, donde esos dos cmdlets no existen - son de PS 3.0+. Get-WmiObject
    SI esta desde PS 1.0). Se unen las CommandLine con un delimitador
    propio (en vez de JSON, que tampoco existe en PS2) - no por saltos de
    linea sueltos: un CommandLine con salto de linea (ej. un -c
    multilinea) rompería un split por lineas y mezclaria procesos
    distintos entre si. `@()` fuerza array vacio si no hay ningun proceso
    (si no, [string]::Join con $null revienta)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"[string]::Join('{_SEP_PROCESOS}', @(Get-WmiObject Win32_Process "
             "-Filter \"name='python.exe'\" | Select-Object "
             "-ExpandProperty CommandLine))"],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return [d for d in r.stdout.split(_SEP_PROCESOS) if d.strip()]
    except Exception as e:
        detalle = getattr(e, "stderr", "") or str(e)
        print(f"(no se pudo listar procesos: {detalle})")
        return []


def _en_ejecucion():
    """Set de (coin, tf) a partir de los procesos monitor.py vivos AHORA
    (no de los CSV - un CSV de hoy puede ser de un proceso ya muerto, ver
    memoria 'monitor-csv-son-copia-pega'). Sirve para que el menu de
    botones solo ofrezca combinaciones reales, no una lista fija a mano.
    Un proceso puede llevar varias monedas ('btc,eth --tf 5m'), de ahi el
    split - mismo patron que _ya_corriendo pero sin fijar coin/tf antes."""
    combos = set()
    for linea in _procesos_monitor():
        if "monitor.py" not in linea:
            continue
        m_tf = re.search(r"--tf\s+(\S+)", linea)
        m_coins = re.search(r"monitor\.py[\"']?\s+[\"']?([A-Za-z0-9,]+)", linea)
        if not m_tf or not m_coins:
            continue
        tf = m_tf.group(1)
        for coin in m_coins.group(1).split(","):
            coin = coin.strip().upper()
            if coin:
                combos.add((coin, tf))
    return combos


def _coins_en_ejecucion():
    return sorted({coin for coin, _tf in _en_ejecucion()})


def _tfs_en_ejecucion(coin=None):
    if coin:
        return sorted({tf for c, tf in _en_ejecucion() if c == coin.upper()})
    return sorted({tf for _c, tf in _en_ejecucion()})


def _ya_corriendo(coin, tf):
    coin_u = coin.upper()
    for linea in _procesos_monitor():
        if "monitor.py" not in linea:
            continue
        if coin_u not in linea.upper():
            continue
        if f"--tf {tf}" not in linea and f"--tf  {tf}" not in linea:
            continue
        return True
    return False


def cmd_open(coin, tf):
    if _ya_corriendo(coin, tf):
        return f"{coin.upper()} {tf} ya esta corriendo, no abro otra consola."
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", f"monitor {coin.upper()} {tf}", "cmd", "/k",
             f"python monitor.py {coin.upper()} --tf {tf} --loop"],
            shell=False,
        )
    except Exception as e:
        return f"No se pudo abrir la consola: {e}"
    return f"Abriendo consola nueva: monitor.py {coin.upper()} --tf {tf} --loop"


# ---------------------------------------------------------------- indicador/valor

def cmd_ajustar(rama, indicador, valor_txt, tf=None):
    """'rama' siempre es RAMA ("libre") desde la rama unica (2026-08-07) -
    se sigue pasando como parametro (en vez de fijarlo aqui dentro) porque
    coincide con la clave "rama" que espera el JSON de comandos/
    (monitor.py _aplicar_comandos). 'tf'=None (indicador/valor) = aplica a
    TODOS los procesos, en cualquier franja. 'tf' puesto (indicador/valor/tf
    - mismo patron que resumen/coin/tf, el tf siempre al final) = solo al
    proceso de esa franja concreta.

    Valida contra PARAMS_TELEGRAM (2026-08-10), NO contra
    monitor.PARAMS_AJUSTABLES entero - el resto de parametros sigue
    existiendo en monitor.py (CLI, comandos/*.json a mano) pero ya no se
    ofrece ni se acepta desde aqui, ver el comentario junto a
    PARAMS_TELEGRAM."""
    if indicador not in PARAMS_TELEGRAM:
        permitidos = ", ".join(PARAMS_TELEGRAM)
        return f"'{indicador}' no es un parametro tocable desde Telegram. Permitidos: {permitidos}"
    tipo, _desc = monitor.PARAMS_AJUSTABLES[indicador]
    try:
        valor_escrito = tipo(valor_txt)
    except (TypeError, ValueError):
        return f"Valor invalido para {indicador} (se espera {tipo.__name__}): {valor_txt!r}"

    if indicador in PARAMS_PORCENTAJE_HUMANO:
        valor = valor_escrito / 100.0
    else:
        valor = valor_escrito

    os.makedirs(COMANDOS_DIR, exist_ok=True)
    nombre = _nombre_comando()
    cmd = {"rama": rama, "indicador": indicador, "valor": valor}
    if tf:
        cmd["tf"] = tf
    with open(os.path.join(COMANDOS_DIR, nombre), "w", encoding="utf-8") as f:
        json.dump(cmd, f)
    alcance = f"franja {tf}" if tf else "TODAS las franjas"
    if indicador in PARAMS_PORCENTAJE_HUMANO:
        detalle = f"{valor_escrito}% (se guarda como {valor})"
    else:
        detalle = f"{valor}"
    return (f"Comando escrito: {indicador} = {detalle} ({alcance}). "
            f"Se aplica en la proxima vuelta.")


def _valores_originales():
    """Los valores de ARRANQUE de cada parametro de PARAMS_TELEGRAM - los
    que tendria un monitor.py recien lanzado, sin ningun ajuste en caliente
    por Telegram. Se derivan llamando a monitor._parse_args() en vez de
    duplicarlos aqui a mano (si algun dia cambia un default en monitor.py y
    se olvida actualizar aqui, esto se desincroniza solo - duplicarlos a
    mano no se habria enterado nunca).

    'exit_rr' es la excepcion: su default CRUDO en _parse_args() es 0.0
    (desactivado), pero main() lo fuerza SIEMPRE a 3.0 si es <=0 desde la
    rama unica (2026-08-07, ver monitor.py main() y el comentario junto a
    cfg["exit_rr"]) - ese 3.0 es el valor real de arranque de cualquier
    monitor.py que se lance hoy, no el 0.0 crudo del dict por defecto."""
    cfg = monitor._parse_args(["_RESET_"])
    if cfg["exit_rr"] <= 0:
        cfg["exit_rr"] = 3.0
    return {p: cfg[p] for p in PARAMS_TELEGRAM}


def cmd_reset(tf=None):
    """Escribe un comando de ajuste por CADA parametro de PARAMS_TELEGRAM,
    devolviendolo a su valor de arranque (ver _valores_originales) - deshace
    de una vez cualquier ajuste en caliente hecho desde Telegram. No toca el
    resto de monitor.PARAMS_AJUSTABLES (fuera de Telegram, ver
    PARAMS_TELEGRAM) - esos nunca se tocaron desde aqui, nada que resetear."""
    valores = _valores_originales()
    lineas = []
    for indicador, valor in valores.items():
        # fraccion_entrada se escribe en HUMANO (2 = "2%") para pasar por
        # la misma conversion /100 que cmd_ajustar ya aplica a cualquier
        # ajuste de este parametro - ver PARAMS_PORCENTAJE_HUMANO.
        valor_txt = (str(valor * 100) if indicador in PARAMS_PORCENTAJE_HUMANO
                     else str(valor))
        cmd_ajustar(RAMA, indicador, valor_txt, tf=tf)
        lineas.append(f"{indicador} = {valor}")
    alcance = f"franja {tf}" if tf else "TODAS las franjas"
    return (f"RESET a valores de arranque ({alcance}):\n" + "\n".join(lineas) +
            "\nSe aplica en la proxima vuelta de cada proceso.")


# ---------------------------------------------------------------- /help

def cmd_help(indicador):
    """Sin argumento, /help manda el menu de botones (ver _menu_principal) -
    esta funcion solo cubre /help/indicador, la descripcion de un parametro.
    Valida contra PARAMS_TELEGRAM, igual que cmd_ajustar."""
    if indicador not in PARAMS_TELEGRAM:
        return f"'{indicador}' no es un parametro conocido desde Telegram."
    tipo, desc = monitor.PARAMS_AJUSTABLES[indicador]
    return f"{indicador} ({tipo.__name__}): {desc}"


# ---------------------------------------------------------------- menu de botones

def _menu_principal():
    return "¿Qué quieres hacer?", [
        [("📋 Abiertas (todo)", "res_todo")],
        [("📋 Abiertas por moneda", "res_coin")],
        [("📋 Abiertas por TF", "res_tf")],
        [("💼 Cartera (todo)", "car_todo")],
        [("🖥 Abrir monitor (open)", "open")],
        [("⚙ Ajustar parámetro", "adj")],
        [("♻ Reset (valores de arranque)", "reset")],
    ]


def _menu_reset():
    filas = [[(tf, f"reset_tf:{tf}")] for tf in _tfs_en_ejecucion()]
    filas.append([("🌐 todas las franjas", "reset_tf:todas")])
    filas.append([("‹ menú", "menu")])
    return ("♻ Reset: vuelve TODOS los parámetros de PARAMS_TELEGRAM a su "
            "valor de arranque, deshaciendo cualquier ajuste hecho desde "
            "aquí. ¿En qué franja?"), filas


def _menu_coins(prefijo):
    coins = _coins_en_ejecucion()
    if not coins:
        return "No hay ninguna franja corriendo ahora mismo.", [[("‹ menú", "menu")]]
    filas = [[(c, f"{prefijo}:{c}")] for c in coins]
    filas.append([("‹ menú", "menu")])
    return "¿Qué moneda? (en ejecución ahora mismo)", filas


def _menu_tfs(prefijo):
    tfs = _tfs_en_ejecucion()
    if not tfs:
        return "No hay ninguna franja corriendo ahora mismo.", [[("‹ menú", "menu")]]
    filas = [[(tf, f"{prefijo}:{tf}")] for tf in tfs]
    filas.append([("‹ menú", "menu")])
    return "¿Qué franja (TF)?", filas


def _menu_indicadores():
    """PARAMS_TELEGRAM, en su orden curado (ciclo de una operacion, no
    alfabetico) - los UNICOS parametros que se ofrecen desde Telegram
    (2026-08-10, antes 31 con un boton "Ampliar" para el resto - ver
    comentario junto a PARAMS_TELEGRAM)."""
    filas, fila = [], []
    for nombre in PARAMS_TELEGRAM:
        fila.append((_etiqueta(nombre), f"adj_i:{nombre}"))
        if len(fila) == 2:
            filas.append(fila); fila = []
    if fila:
        filas.append(fila)
    filas.append([("‹ menú", "menu")])
    return "¿Qué parámetro ajusto?", filas


def _menu_valores(indicador):
    if indicador not in monitor.PARAMS_AJUSTABLES:
        return "Parámetro no reconocido.", [[("‹ menú", "menu")]]
    _tipo, desc_tecnica = monitor.PARAMS_AJUSTABLES[indicador]
    desc = DESCRIPCIONES_MENU.get(indicador, desc_tecnica)
    sugeridos = VALORES_SUGERIDOS.get(indicador, [])
    # Los 3 sugeridos completos (antes solo se mostraban 2) - con 3
    # curados como [bajo, recomendado, alto], el del medio es el consejo
    # real y se marca aparte; enseñar solo 2 se dejaba el consejo fuera la
    # mitad de las veces (indicadores con numero par de elementos en el
    # dict de origen).
    filas = []
    for idx, v in enumerate(sugeridos):
        etiqueta = f"★ {v} (recomendado)" if len(sugeridos) == 3 and idx == 1 else v
        filas.append([(etiqueta, f"adj_v:{indicador}:{v}")])
    filas.append([("✏ escribir valor", f"adj_vf:{indicador}")])
    filas.append([("‹ menú", "menu")])
    return f"{_etiqueta(indicador)}\n{desc}\n¿Qué valor?", filas


def _menu_tf_ajuste(indicador, valor):
    filas = [[(tf, f"adj_tf:{indicador}:{valor}:{tf}")] for tf in _tfs_en_ejecucion()]
    filas.append([("🌐 todas las franjas", f"adj_tf:{indicador}:{valor}:todas")])
    filas.append([("‹ menú", "menu")])
    return f"{_etiqueta(indicador)} = {valor} - ¿en qué franja?", filas


def _procesar_callback(cb):
    callback_id = cb["id"]
    datos = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    if chat_id is None or str(chat_id) != str(avisos.CHAT_ID):
        _responder_callback(callback_id)
        return

    # cualquier boton pulsado corta un "escribir valor"/"open" a medias -
    # evita que un texto suelto posterior se interprete con el paso viejo.
    PENDIENTE.pop(str(chat_id), None)

    partes = datos.split(":")
    accion = partes[0]
    texto, filas, resultado = None, None, None

    if accion == "menu":
        texto, filas = _menu_principal()
    elif accion == "cancelar":
        texto, filas = "Cancelado.", None
    elif accion == "res_todo":
        resultado = cmd_resumen()
    elif accion == "res_coin":
        texto, filas = _menu_coins("res_c")
    elif accion == "res_c":
        resultado = cmd_resumen(coin=partes[1])
    elif accion == "res_tf":
        texto, filas = _menu_tfs("res_tf_v")
    elif accion == "res_tf_v":
        resultado = cmd_resumen(tf=partes[1])
    elif accion == "car_todo":
        resultado = cmd_cartera()
    elif accion == "open":
        PENDIENTE[str(chat_id)] = {"tipo": "open_coin"}
        _responder_callback(callback_id)
        _editar_menu(chat_id, message_id, "¿Qué moneda? (envía el ticker, ej. BTC)",
                     [[("✖ cancelar", "cancelar")]])
        return
    elif accion == "adj":
        texto, filas = _menu_indicadores()
    elif accion == "adj_i":
        texto, filas = _menu_valores(partes[1])
    elif accion == "adj_v":
        texto, filas = _menu_tf_ajuste(partes[1], partes[2])
    elif accion == "adj_vf":
        PENDIENTE[str(chat_id)] = {"tipo": "adj_valor", "indicador": partes[1]}
        _responder_callback(callback_id)
        _editar_menu(chat_id, message_id,
                     f"Valor para {partes[1]}? (envía el número)",
                     [[("✖ cancelar", "cancelar")]])
        return
    elif accion == "adj_tf":
        indicador, valor, tf = partes[1], partes[2], partes[3]
        resultado = cmd_ajustar(RAMA, indicador, valor, tf=None if tf == "todas" else tf)
    elif accion == "reset":
        texto, filas = _menu_reset()
    elif accion == "reset_tf":
        tf = partes[1]
        resultado = cmd_reset(tf=None if tf == "todas" else tf)
    else:
        texto, filas = "Opción no reconocida.", None

    _responder_callback(callback_id)
    if resultado is not None:
        _editar_menu(chat_id, message_id, resultado, [[("‹ menú", "menu")]])
    else:
        _editar_menu(chat_id, message_id, texto, filas)


def _procesar_pendiente(chat_id, texto):
    """Continua un asistente de texto libre a medias (open: coin -> tf;
    ajustar: valor escrito a mano). Devuelve None siempre - ya manda la
    respuesta o el siguiente paso por su cuenta (a veces es otro texto
    libre, a veces vuelve a botones)."""
    est = PENDIENTE.pop(chat_id)
    texto = texto.strip()
    tipo = est["tipo"]
    if tipo == "open_coin":
        PENDIENTE[chat_id] = {"tipo": "open_tf", "coin": texto}
        _enviar_menu(chat_id, f"Moneda: {texto.upper()}. ¿Qué TF? (ej. 5m, 15m, 1h, 4h)",
                     [[("✖ cancelar", "cancelar")]])
    elif tipo == "open_tf":
        _enviar(cmd_open(est["coin"], texto.lower()))
    elif tipo == "adj_valor":
        titulo, filas = _menu_tf_ajuste(est["indicador"], texto.lower())
        _enviar_menu(chat_id, titulo, filas)
    return None


# ---------------------------------------------------------------- routing

def _procesar(texto, chat_id):
    # minusculas SIEMPRE: el movil autocapitaliza la primera letra de cada
    # mensaje (visto en vivo: "Resumen/ETH/5m" no coincidia con "resumen/" y
    # caia en indicador/valor por error). Todo lo que se compara aqui
    # (resumen/open/help, nombres de indicador, tf) es case-insensitive por
    # diseño en el resto del proyecto, asi que es seguro.
    texto = texto.strip().lower()
    if texto in ("/help", "help"):
        titulo, filas = _menu_principal()
        _enviar_menu(chat_id, titulo, filas)
        return None
    if texto.startswith("/help/") or texto.startswith("help/"):
        indicador = texto.split("/")[-1]
        return cmd_help(indicador)
    if texto == "resumen":
        return cmd_resumen()
    if texto.startswith("resumen/"):
        partes = texto.split("/")
        if len(partes) == 2:
            arg = partes[1]
            # Un solo argumento: se detecta solo si es moneda o TF segun lo
            # que este corriendo AHORA MISMO (2026-08-10, antes exigia
            # coin Y tf juntos) - si no coincide con ninguno de los dos
            # (o no hay nada corriendo), se interpreta como moneda de
            # todas formas, mensaje mas util que "no encontrado".
            if arg in _tfs_en_ejecucion() and arg.upper() not in _coins_en_ejecucion():
                return cmd_resumen(tf=arg)
            return cmd_resumen(coin=arg)
        if len(partes) == 3:
            return cmd_resumen(coin=partes[1], tf=partes[2])
        return "Uso: resumen, resumen/coin (ej. resumen/ETH), resumen/tf (ej. resumen/15m) o resumen/coin/tf"
    if texto == "reset":
        return cmd_reset()
    if texto.startswith("reset/"):
        partes = texto.split("/")
        if len(partes) != 2:
            return "Uso: reset o reset/tf (ej. reset/15m)"
        return cmd_reset(tf=partes[1])
    if texto == "cartera":
        return cmd_cartera()
    if texto.startswith("cartera/"):
        partes = texto.split("/")
        if len(partes) == 2:
            return cmd_cartera(coin=partes[1])
        if len(partes) == 3:
            return cmd_cartera(coin=partes[1], tf=partes[2])
        return "Uso: cartera, cartera/coin (ej. cartera/BTC) o cartera/coin/tf (ej. cartera/BTC/15m)"
    if texto.startswith("open/"):
        partes = texto.split("/")
        if len(partes) != 3:
            return "Uso: open/coin/tf (ej. open/SOL/1h) - las dos partes son obligatorias."
        return cmd_open(partes[1], partes[2])
    partes = texto.split("/")
    # indicador/valor(/tf) - forma normal desde la rama unica (2026-08-07):
    # ya no hace falta escribir "libre/" delante. Se sigue aceptando
    # rama/indicador/valor(/tf) (con "libre" o cualquier otro texto en la
    # primera parte se ignora igual, cmd_ajustar solo obedece RAMA) para no
    # romper scripts/automatizaciones viejas que ya lo mandaban asi.
    if len(partes) == 2 and partes[0] in PARAMS_TELEGRAM:
        return cmd_ajustar(RAMA, partes[0], partes[1])
    if len(partes) == 3 and partes[0] in PARAMS_TELEGRAM:
        return cmd_ajustar(RAMA, partes[0], partes[1], tf=partes[2])
    if len(partes) == 3:
        return cmd_ajustar(RAMA, partes[1], partes[2])
    if len(partes) == 4:
        return cmd_ajustar(RAMA, partes[1], partes[2], tf=partes[3])
    return ("No entendido. Manda /help para ver los comandos "
            "(resumen, resumen/coin, resumen/tf, resumen/coin/tf, cartera, "
            "cartera/coin, cartera/coin/tf, open/coin/tf, indicador/valor, "
            "indicador/valor/tf, reset, reset/tf).")


def main():
    if not avisos.configurado():
        print("Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env - no puedo arrancar.")
        return
    print("telegram_control.py escuchando... (Ctrl+C para salir)")
    offset = _leer_offset()
    while True:
        updates = _get_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            _guardar_offset(offset)

            cb = u.get("callback_query")
            if cb:
                print(f"[boton] {cb.get('data')}")
                try:
                    _procesar_callback(cb)
                except Exception as e:
                    print(f"(error procesando boton: {e})")
                continue

            msg = u.get("message") or u.get("edited_message")
            if not msg or "text" not in msg:
                continue
            chat_id = str(msg["chat"]["id"])
            if chat_id != str(avisos.CHAT_ID):
                print(f"(mensaje ignorado, chat_id distinto: {chat_id})")
                continue
            texto = msg["text"]
            print(f"[recibido] {texto}")
            try:
                if chat_id in PENDIENTE:
                    _procesar_pendiente(chat_id, texto)
                    continue
                respuesta = _procesar(texto, chat_id)
            except Exception as e:
                respuesta = f"Error procesando el comando: {e}"
            if respuesta is None:
                continue
            _enviar(respuesta)
            print(f"[enviado] {respuesta[:200]}")


if __name__ == "__main__":
    main()
