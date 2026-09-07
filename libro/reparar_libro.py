
import csv
import json
import os
from datetime import datetime, timezone

DIR_BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATOS = os.path.join(DIR_BASE, "datos")


def reparar_libro(coin, mercado, dir_datos=DIR_DATOS):
    """Agrega columnas profundidad_real a libro_*.csv existentes"""

    print(f"[{coin}] Reparando libro_*.csv con nueva estructura...")

    campos_nuevos = [
        "timestamp_local_ms", "fecha_utc", "timestamp_exchange_ms", "estado", "coin",
        "imbalance", "imbalance_niveles", "imbalance_amplio",
        "last_price", "mark_price", "index_price",
        "open_interest", "funding_rate_pct", "long_short_ratio", "session_id",
        "profundidad_real", "profundidad_real_amplio",
        "bids_json", "asks_json",
        "bids_amplio_json", "asks_amplio_json",
    ]

    # Buscar todos los libro_*.csv
    libro_dir = dir_datos
    for filename in sorted(os.listdir(libro_dir)):
        if not filename.startswith(f"libro_{coin}_{mercado}"):
            continue
        if "_v" in filename:
            continue

        ruta = os.path.join(libro_dir, filename)
        print(f"  Procesando {filename}...")

        try:
            filas = []
            with open(ruta, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Extraer profundidad_real desde bids_json y asks_json
                    prof_real = 0
                    prof_real_amplio = 0

                    try:
                        bids = json.loads(row.get('bids_json', '[]') or '[]')
                        asks = json.loads(row.get('asks_json', '[]') or '[]')
                        prof_real = min(len(bids), len(asks))
                    except (json.JSONDecodeError, TypeError):
                        pass

                    try:
                        bids_amplio = json.loads(row.get('bids_amplio_json', '[]') or '[]')
                        asks_amplio = json.loads(row.get('asks_amplio_json', '[]') or '[]')
                        prof_real_amplio = min(len(bids_amplio), len(asks_amplio))
                    except (json.JSONDecodeError, TypeError):
                        pass

                    # Agregar nuevas columnas si no existen
                    if 'profundidad_real' not in row:
                        row['profundidad_real'] = str(prof_real)
                    if 'profundidad_real_amplio' not in row:
                        row['profundidad_real_amplio'] = str(prof_real_amplio)

                    # Marcar en estado las filas sin last_price: libro.py no
                    # capturaba el ticker antes del 2026-09-02, y quedaron con
                    # estado "ok" pese a no tener last/mark/index. No se puede
                    # reponer el dato, pero si se puede dejar de mentir sobre
                    # su ausencia para que cualquier lector sepa que debe
                    # esperar a un last_price no vacio.
                    if not (row.get('last_price') or '').strip():
                        estado_actual = row.get('estado') or 'ok'
                        errores_actuales = [] if estado_actual == 'ok' else estado_actual.split(',')
                        if 'ticker' not in errores_actuales:
                            errores_actuales.append('ticker')
                        row['estado'] = ','.join(errores_actuales)

                    filas.append(row)

            if not filas:
                print(f"    ⚠️  No hay datos")
                continue

            # Escribir con nueva estructura
            tmp = ruta + ".tmp"
            with open(tmp, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=campos_nuevos, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(filas)

            os.replace(tmp, ruta)
            print(f"    ✓ {os.path.basename(ruta)} reparado ({len(filas)} filas)")

        except Exception as e:
            print(f"    ❌ Error: {e}")
            continue

    print(f"✅ Estructura de libro.py unificada para {coin}/{mercado}\n")
    return True


def main():
    import argparse
    p = argparse.ArgumentParser(description="Repara estructura de libro_*.csv")
    p.add_argument("coin", nargs="?", default="eth", help="moneda (default: eth)")
    p.add_argument("--mercado", default="futuros", help="mercado (default: futuros)")
    p.add_argument("--datos", default=DIR_DATOS, help="carpeta con libro_*.csv")
    args = p.parse_args()

    coin = args.coin.upper()
    if not reparar_libro(coin, args.mercado, args.datos):
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
