# ---------------------------------------------------------------
# telegram_control.py - Escucha comandos de Telegram (getUpdates) y
# responde bajo demanda: que procesos siguen vivos, y el marcador de
# validador_niveles.py/marcador_tpsl.py - todo lo que hasta ahora habia
# que comprobar entrando por SSH y pegando 'ps -ef' (2026-08-12, Fran:
# "para poder comprobar sin tener que abrir el monitor").
#
# Adaptado del patron ya probado en D:\neocat\bit\telegram_control.py
# (mismo getUpdates/offset/teclados inline) - las "ramas"/PARAMS_AJUSTABLES/
# ajuste en caliente de ese proyecto no aplican aqui, se han dejado fuera.
#
# UNICO proceso que debe hacer getUpdates - Telegram reparte los mensajes
# entre quien pregunte primero, no los duplica (si dos procesos hacen
# polling a la vez, cada uno se queda con la mitad de las actualizaciones
# sin enterarse de la otra mitad). monitor_telegram.py sigue aparte, SIN
# TOCAR - ese solo manda avisos automaticos via sendMessage, que si puede
# hacerlo mas de un proceso a la vez sin problema (no es lo mismo que
# escuchar).
#
# Solo responde al chat_id de .env (TELEGRAM_CHAT_ID) - cualquier otro
# mensaje se ignora. El offset de getUpdates se guarda en
# herramientas/libro/telegram_offset.txt (gitignorado, es estado en vivo
# como el resto de ese directorio).
#
# Comandos (texto libre o /start para botones):
#   /start                     - menu de botones
#   estado                     - que procesos de herramientas/*.py siguen vivos (ps -ef)
#   confirmadas/coin/tf        - ULTIMA vez que disparo cada una de las 4
#                                 REFINADAS_CONFIRMADAS (mismo filtro que
#                                 monitor_telegram.py, pero bajo demanda -
#                                 para comprobar que no se ha perdido ningun
#                                 aviso sin esperar al push automatico)
#   confirmaciones/coin/tf     - tabla de validador_niveles.py --consultar
#   tpsl/coin/tf               - tabla de marcador_tpsl.py --consultar
#   resumen                    - confirmaciones + tpsl de TODAS las combinaciones activas
#
# Uso:
#   python herramientas/telegram_control.py
# ---------------------------------------------------------------

import csv
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alertas import avisos
from herramientas.monitor_comun import DIR_LIBRO, _listar_procesos
from mercado.senales import REFINADAS_CONFIRMADAS
from herramientas import validador_niveles, marcador_tpsl

OFFSET_FILE = os.path.join(DIR_LIBRO, "telegram_offset.txt")


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
    os.makedirs(DIR_LIBRO, exist_ok=True)
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


LIMITE_TELEGRAM = 4000  # Telegram corta sendMessage/editMessageText en ~4096
                         # caracteres - mismo margen de sobra para los dos.


def _enviar(texto):
    """Trocea en bloques de <4000 caracteres - Telegram corta mensajes largos."""
    for i in range(0, len(texto), LIMITE_TELEGRAM):
        avisos.enviar(texto[i:i + LIMITE_TELEGRAM])


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
    """Si 'texto' no cabe en un solo mensaje, se trocea (igual que _enviar()
    para el flujo de texto libre) - antes esto mandaba todo en una sola
    llamada a sendMessage sin trocear, y con --resumen de varios coin/tf
    activos Telegram podia rechazar el mensaje entero por pasarse del
    limite. Solo el ULTIMO trozo lleva teclado."""
    if len(texto) <= LIMITE_TELEGRAM:
        kwargs = {"chat_id": chat_id, "text": texto}
        if filas:
            kwargs["reply_markup"] = _teclado(filas)
        return _api("sendMessage", **kwargs)
    trozos = [texto[i:i + LIMITE_TELEGRAM] for i in range(0, len(texto), LIMITE_TELEGRAM)]
    for trozo in trozos[:-1]:
        _api("sendMessage", chat_id=chat_id, text=trozo)
    kwargs = {"chat_id": chat_id, "text": trozos[-1]}
    if filas:
        kwargs["reply_markup"] = _teclado(filas)
    return _api("sendMessage", **kwargs)


def _editar_menu(chat_id, message_id, texto, filas=None):
    """Reescribe el propio mensaje del boton pulsado. Si 'texto' no cabe en
    un solo mensaje, no hay forma de "editar" hacia varios - se manda
    troceado como mensaje(s) NUEVOS (_enviar_menu, mismo limite) y el
    mensaje original del boton se deja como puntero corto, sin teclado. Si
    ya no se puede editar (borrado, o mas de 48h), tambien cae a mandar uno
    nuevo, para no perder la respuesta en silencio."""
    if len(texto) > LIMITE_TELEGRAM:
        _enviar_menu(chat_id, texto, filas)
        _api("editMessageText", chat_id=chat_id, message_id=message_id,
             text="(respuesta larga, mandada como mensaje nuevo arriba ⬆️)",
             reply_markup={"inline_keyboard": []})
        return
    kwargs = {"chat_id": chat_id, "message_id": message_id, "text": texto}
    kwargs["reply_markup"] = _teclado(filas) if filas else {"inline_keyboard": []}
    if _api("editMessageText", **kwargs) is None:
        _enviar_menu(chat_id, texto, filas)


def _responder_callback(callback_id, texto=None):
    """Apaga el 'reloj de carga' del boton - sin esto Telegram lo deja
    girando hasta el timeout."""
    kwargs = {"callback_query_id": callback_id}
    if texto:
        kwargs["text"] = texto
    _api("answerCallbackQuery", **kwargs)


# ---------------------------------------------------------------- procesos vivos

def _combos_activos(nombre_script):
    """[(COIN, tf), ...] de los procesos vivos "python .../<nombre_script>
    <coin> <tf> ...", sacado de 'ps -ef' - igual que hace grabador_libro.py
    con su lock, esto refleja el estado REAL en vez de una lista fija a
    mano, para que los botones de coin/tf solo ofrezcan combinaciones que
    de verdad estan corriendo."""
    combos = set()
    for linea in _listar_procesos():
        if nombre_script not in linea:
            continue
        m = re.search(rf"{re.escape(nombre_script)}\s+(\S+)\s+(\S+)", linea)
        if m:
            combos.add((m.group(1).upper(), m.group(2)))
    return sorted(combos)


def cmd_estado():
    """Todas las lineas de 'ps -ef' que mencionan herramientas/ - mismo
    comando que Fran lleva pegando toda la sesion a mano
    (ps -ef | grep herramientas | grep -v grep), aqui bajo demanda."""
    out = []
    for linea in _listar_procesos():
        if "herramientas" not in linea:
            continue
        partes = linea.split(None, 7)
        if len(partes) < 8 or "herramientas" not in partes[7]:
            continue
        pid, cmd = partes[1], partes[7]
        cmd_corto = cmd.split("herramientas/", 1)[-1]
        out.append(f"{pid}: {cmd_corto}")
    if not out:
        return "Ningun proceso de herramientas/*.py corriendo ahora mismo."
    return "ESTADO:\n" + "\n".join(sorted(out))


def cmd_confirmadas(coin, tf):
    """Ultima vez que disparo cada una de las 4 REFINADAS_CONFIRMADAS en
    senales_<coin>_<tf>.csv (mismo filtro que usa monitor_telegram.py para
    decidir que manda) - "nunca disparada" si no aparece ninguna fila
    todavia. El fichero es append-only en orden cronologico, asi que basta
    con quedarse con la ULTIMA fila vista de cada tipo al recorrerlo."""
    ruta = os.path.join(DIR_LIBRO, f"senales_{coin.upper()}_{tf}.csv")
    if not os.path.exists(ruta):
        return f"No hay {ruta} todavia (¿esta corriendo monitor_senales.py {coin.lower()} {tf}?)."
    ultimas = {}
    with open(ruta, newline="") as f:
        for fila in csv.DictReader(f):
            tipo = fila.get("tipo")
            if tipo in REFINADAS_CONFIRMADAS:
                ultimas[tipo] = fila
    lineas = [f"CONFIRMADAS {coin.upper()} {tf} (ultima vez):"]
    for nombre in sorted(REFINADAS_CONFIRMADAS):
        fila = ultimas.get(nombre)
        if fila:
            lineas.append(f"{nombre}: {fila.get('fecha_utc', '?')}  cierre {fila.get('precio_actual', '?')}")
        else:
            lineas.append(f"{nombre}: nunca disparada")
    return "\n".join(lineas)


def cmd_confirmaciones(coin, tf):
    return validador_niveles._consultar(coin, tf)


def cmd_tpsl(coin, tf):
    return marcador_tpsl._consultar(coin, tf)


def cmd_resumen():
    combos_s = _combos_activos("monitor_senales.py")
    combos_v = _combos_activos("validador_niveles.py")
    combos_m = _combos_activos("marcador_tpsl.py")
    if not combos_s and not combos_v and not combos_m:
        return "No hay ningun monitor_senales.py/validador_niveles.py/marcador_tpsl.py corriendo ahora mismo."
    partes = [cmd_confirmadas(c, t) for c, t in combos_s]
    partes += [cmd_confirmaciones(c, t) for c, t in combos_v]
    partes += [cmd_tpsl(c, t) for c, t in combos_m]
    return "\n\n".join(partes)


# ---------------------------------------------------------------- menu de botones

def _menu_principal():
    return "¿Qué quieres consultar?", [
        [("🩺 Estado (procesos vivos)", "estado")],
        [("🔔 Confirmadas (última vez)", "cfmd")],
        [("✅ Confirmaciones (validador_niveles)", "conf")],
        [("🎯 TP/SL (marcador_tpsl)", "tpsl")],
        [("📋 Resumen (todo)", "resumen")],
    ]


def _menu_coins(prefijo, nombre_script):
    coins = sorted({c for c, _t in _combos_activos(nombre_script)})
    if not coins:
        return f"No hay ningun {nombre_script} corriendo ahora mismo.", [[("‹ menú", "menu")]]
    filas = [[(c, f"{prefijo}_c:{c}")] for c in coins]
    filas.append([("‹ menú", "menu")])
    return "¿Qué moneda? (en ejecución ahora mismo)", filas


def _menu_tfs(prefijo, nombre_script, coin):
    tfs = sorted({t for c, t in _combos_activos(nombre_script) if c == coin})
    if not tfs:
        return f"{coin} no tiene ningún proceso corriendo ahora mismo.", [[("‹ menú", "menu")]]
    filas = [[(tf, f"{prefijo}_t:{coin}:{tf}")] for tf in tfs]
    filas.append([("‹ menú", "menu")])
    return f"{coin} - ¿qué franja?", filas


def _procesar_callback(cb):
    callback_id = cb["id"]
    datos = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    if chat_id is None or str(chat_id) != str(avisos.CHAT_ID):
        _responder_callback(callback_id)
        return

    partes = datos.split(":")
    accion = partes[0]
    texto, filas, resultado = None, None, None

    if accion == "menu":
        texto, filas = _menu_principal()
    elif accion == "estado":
        resultado = cmd_estado()
    elif accion == "resumen":
        resultado = cmd_resumen()
    elif accion == "cfmd":
        texto, filas = _menu_coins("cfmd", "monitor_senales.py")
    elif accion == "cfmd_c":
        texto, filas = _menu_tfs("cfmd", "monitor_senales.py", partes[1])
    elif accion == "cfmd_t":
        resultado = cmd_confirmadas(partes[1], partes[2])
    elif accion == "conf":
        texto, filas = _menu_coins("conf", "validador_niveles.py")
    elif accion == "conf_c":
        texto, filas = _menu_tfs("conf", "validador_niveles.py", partes[1])
    elif accion == "conf_t":
        resultado = cmd_confirmaciones(partes[1], partes[2])
    elif accion == "tpsl":
        texto, filas = _menu_coins("tpsl", "marcador_tpsl.py")
    elif accion == "tpsl_c":
        texto, filas = _menu_tfs("tpsl", "marcador_tpsl.py", partes[1])
    elif accion == "tpsl_t":
        resultado = cmd_tpsl(partes[1], partes[2])
    else:
        texto, filas = "Opción no reconocida.", None

    _responder_callback(callback_id)
    if resultado is not None:
        _editar_menu(chat_id, message_id, resultado, [[("‹ menú", "menu")]])
    else:
        _editar_menu(chat_id, message_id, texto, filas)


# ---------------------------------------------------------------- routing texto

def _procesar(texto, chat_id):
    texto = texto.strip().lower()
    if texto in ("/start", "start", "/help", "help"):
        titulo, filas = _menu_principal()
        _enviar_menu(chat_id, titulo, filas)
        return None
    if texto in ("/estado", "estado"):
        return cmd_estado()
    if texto in ("/resumen", "resumen"):
        return cmd_resumen()
    if texto.startswith("confirmadas/"):
        partes = texto.split("/")
        if len(partes) != 3:
            return "Uso: confirmadas/coin/tf (ej. confirmadas/eth/1h)"
        return cmd_confirmadas(partes[1], partes[2])
    if texto.startswith("confirmaciones/"):
        partes = texto.split("/")
        if len(partes) != 3:
            return "Uso: confirmaciones/coin/tf (ej. confirmaciones/eth/1h)"
        return cmd_confirmaciones(partes[1], partes[2])
    if texto.startswith("tpsl/"):
        partes = texto.split("/")
        if len(partes) != 3:
            return "Uso: tpsl/coin/tf (ej. tpsl/eth/1h)"
        return cmd_tpsl(partes[1], partes[2])
    return ("No entendido. Manda /start para ver el menú, o usa "
            "confirmadas/coin/tf, confirmaciones/coin/tf, tpsl/coin/tf, estado, resumen.")


# ---------------------------------------------------------------- main

def main():
    if not avisos.configurado():
        print("Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env - no puedo arrancar.")
        return
    print("telegram_control.py escuchando... (Ctrl+C para salir)")
    offset = _leer_offset()
    try:
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
                    respuesta = _procesar(texto, chat_id)
                except Exception as e:
                    respuesta = f"Error procesando el comando: {e}"
                if respuesta is None:
                    continue
                _enviar(respuesta)
                print(f"[enviado] {respuesta[:200]}")
    except KeyboardInterrupt:
        print("\nParado por el usuario.")


if __name__ == "__main__":
    main()
