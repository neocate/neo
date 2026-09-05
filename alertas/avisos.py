
import os

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def configurado():
    return bool(TOKEN and CHAT_ID)


def enviar(texto):
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


if __name__ == "__main__":
    if not configurado():
        print("Telegram NO configurado: faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env")
    elif enviar("Prueba desde el bot de trading ✅"):
        print("Mensaje de prueba enviado. Mira tu Telegram.")
    else:
        print("No se pudo enviar (revisa token / chat_id).")
