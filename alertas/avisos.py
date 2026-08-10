# ----------------------------------------------------------------------
#  avisos.py  -  Enviar avisos a tu Telegram (p.ej. cambios de regimen)
#
#  Lee TELEGRAM_TOKEN y TELEGRAM_CHAT_ID del .env. Si no estan puestos,
#  enviar() no hace nada (no rompe el programa). Asi el monitor funciona
#  igual con o sin Telegram configurado.
#
#  Como conseguir token y chat_id: ver los pasos que te paso por chat
#  (crear bot con @BotFather y sacar tu chat_id con getUpdates).
# ----------------------------------------------------------------------

import os

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def configurado():
    """True si hay token y chat_id en el .env."""
    return bool(TOKEN and CHAT_ID)


def enviar(texto):
    """Envia un mensaje a tu Telegram. Si no esta configurado o falla la
    red, NO rompe nada (devuelve False); el monitor sigue su marcha."""
    if not configurado():
        return False
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            params={"chat_id": CHAT_ID, "text": texto},
            timeout=10,
        )
        return r.ok
    except Exception as e:
        print(f"(aviso) no se pudo enviar a Telegram: {e}")
        return False


# Prueba rapida:  python avisos.py
if __name__ == "__main__":
    if not configurado():
        print("Telegram NO configurado: faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env")
    elif enviar("Prueba desde el bot de trading ✅"):
        print("Mensaje de prueba enviado. Mira tu Telegram.")
    else:
        print("No se pudo enviar (revisa token / chat_id).")
