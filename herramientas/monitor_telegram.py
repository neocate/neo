# ---------------------------------------------------------------
# monitor_telegram.py - Unico responsable de mandar Telegram cuando salta
# una señal confirmada. Antes esto vivia dentro de monitor_senales.py
# (avisos.enviar() inline, sacado de ahi el 2026-08-11) - se separa por el
# mismo motivo que ya separo señales de niveles: notificar es una
# responsabilidad distinta de detectar, y no tiene sentido reiniciar la
# deteccion de señales solo para ajustar como/cuando se notifica (o al
# reves).
#
# Proceso UNICO y GLOBAL, sin coin/tf: descubre TODOS los
# herramientas/libro/senales_<COIN>_<TF>.csv que haya (los escribe
# monitor_senales.py, uno por cada instancia que tengas corriendo) y los
# sigue en vivo, re-escaneando el directorio cada vuelta para engancharse
# tambien a los que aparezcan despues (si lanzas mas monitor_senales.py mas
# tarde, sin tener que reiniciar esto). Telegram es un unico canal de
# avisos del proyecto entero - no tiene sentido un proceso de notificacion
# por cada coin/tf.
#
# Al descubrir un fichero nuevo se posiciona al FINAL (no relee ni notifica
# nada del historico viejo - mismo criterio que monitor_niveles.py/
# monitor_senales.py con flujo_*.csv).
#
# De cada fila con evento="senal", solo manda Telegram si su 'tipo' esta en
# mercado.senales.REFINADAS_CONFIRMADAS - monitor_senales.py loguea TODO lo
# que detecta (las 4 CONFIRMADAS, las 3 EN_PRUEBAS -desde 2026-08-12, para
# que fjsl.py pueda seguir midiendolas- y lo que quede fuera del Grupo A),
# este filtro es el UNICO punto que decide que de eso se manda de verdad.
#
# Via alertas.avisos.enviar(). Sin TELEGRAM_TOKEN/TELEGRAM_CHAT_ID en .env,
# enviar() no hace nada - no hace falta configurarlo para que el resto del
# proyecto funcione.
#
# Uso:
#   python herramientas/monitor_telegram.py [--cada 15] [--latido-cada 1800]
# ---------------------------------------------------------------

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.monitor_comun import DIR_LIBRO, CAMPOS_AVISOS, _tail_csv
from mercado import senales
from alertas import avisos

_PATRON = re.compile(r"^senales_(?:\d+_)?([A-Z0-9]+)_(.+)\.csv$")


def _localizar_senales_csv():
    """(ruta, coin, tf) de todos los senales_*.csv en herramientas/libro/."""
    if not os.path.isdir(DIR_LIBRO):
        return []
    out = []
    for nombre in os.listdir(DIR_LIBRO):
        m = _PATRON.match(nombre)
        if m:
            out.append((os.path.join(DIR_LIBRO, nombre), m.group(1), m.group(2)))
    return out


def main():
    args = sys.argv[1:]
    cada = 15.0
    latido_cada = 1800.0
    i = 0
    while i < len(args):
        if args[i] == "--cada":
            i += 1; cada = float(args[i])
        elif args[i] == "--latido-cada":
            i += 1; latido_cada = float(args[i])
        i += 1

    print(f"Vigilando Telegram de todos los senales_*.csv en {DIR_LIBRO} (cada {cada:.0f}s).")
    if avisos.configurado():
        print("Telegram configurado (TOKEN/CHAT_ID presentes en .env) - las señales SI se mandan.")
    else:
        print("Telegram NO configurado (falta TOKEN/CHAT_ID en .env) - no se manda nada, ver alertas/avisos.py.")
    print(f"Latido cada {latido_cada:.0f}s (para saber que sigue vivo aunque no haya señales).")
    print("Ctrl+C para parar.\n")

    offsets = {}
    ultimo_latido = time.monotonic()
    try:
        while True:
            ficheros = _localizar_senales_csv()
            for ruta, coin, tf in ficheros:
                if ruta not in offsets:
                    offsets[ruta] = os.path.getsize(ruta)
                    print(f"Nuevo fichero detectado: {ruta} (leyendo solo desde ahora)")

                filas, offsets[ruta] = _tail_csv(ruta, offsets[ruta], fieldnames=CAMPOS_AVISOS)
                for fila in filas:
                    if fila.get("evento") != "senal":
                        continue
                    nombre = fila.get("tipo")
                    if nombre not in senales.REFINADAS_CONFIRMADAS:
                        continue
                    precio = fila.get("precio_actual", "")
                    fecha = fila.get("fecha_utc", "")
                    print(f"  >>> TELEGRAM {coin} {tf}  {nombre}  cierre {precio}  ({fecha} UTC)")
                    avisos.enviar(f"{coin} {tf}  {nombre}\ncierre {precio}  ({fecha} UTC)")

            # Latido periodico: sin esto, el log se queda mudo entre señales
            # (pueden pasar horas) y no hay forma de distinguir "vivo, sin
            # novedades" de "colgado" solo mirando el fichero de log.
            if time.monotonic() - ultimo_latido >= latido_cada:
                ultimo_latido = time.monotonic()
                objetivos = ", ".join(f"{coin} {tf}" for _, coin, tf in ficheros) or "ninguno todavia"
                print(f"[latido] vivo - vigilando: {objetivos}")

            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")


if __name__ == "__main__":
    main()
