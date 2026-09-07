
import argparse
import csv
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DIR_BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATOS = os.path.join(DIR_BASE, "datos", "flujo")


def reparar_cvd(coin, mercado, dir_datos=DIR_DATOS):
    """Recalcula CVD desde trades deduplicados"""

    print(f"[{coin}] Reparando CVD desde trades deduplicados...")

    # Leer todos los trades
    trades = []
    trades_dir = dir_datos
    for f in sorted(os.listdir(trades_dir)):
        if not f.startswith(f"trades_{coin}_{mercado}"):
            continue

        ruta = os.path.join(trades_dir, f)
        print(f"  Leyendo {f}...")

        try:
            with open(ruta, newline='', encoding='utf-8') as fp:
                for row in csv.DictReader(fp):
                    try:
                        trades.append({
                            'ventana_fin_ms': int(row['ventana_fin_ms']),
                            'timestamp': int(row['timestamp_exchange_ms']),
                            'precio': float(row['precio']),
                            'volumen': float(row['volumen']),
                            'lado': row['lado'],
                            'id': row.get('id', ''),
                        })
                    except (KeyError, ValueError):
                        continue
        except FileNotFoundError:
            continue

    if not trades:
        print(f"  ❌ No se encontraron trades para {coin}/{mercado}")
        return False

    print(f"  ✓ {len(trades)} trades cargados")

    # Deduplicar por ID o (timestamp, precio, volumen, lado)
    vistos = {}
    trades_dedup = []
    for t in trades:
        if t['id']:
            clave = ('id', t['id'])
        else:
            clave = ('tpl', t['timestamp'], t['precio'], t['volumen'], t['lado'])

        if clave not in vistos:
            vistos[clave] = True
            trades_dedup.append(t)

    n_dup = len(trades) - len(trades_dedup)
    if n_dup:
        print(f"  ✓ {n_dup} trades duplicados removidos")

    # Agrupar por ventana y calcular vol_buy, vol_sell, delta
    por_ventana = defaultdict(lambda: {'buy': 0.0, 'sell': 0.0})
    for t in trades_dedup:
        por_ventana[t['ventana_fin_ms']][t['lado']] += t['volumen']

    # Leer flujo existente
    flujo_archivos = defaultdict(list)
    for f in sorted(os.listdir(trades_dir)):
        if not f.startswith(f"flujo_{coin}_{mercado}"):
            continue

        ruta = os.path.join(trades_dir, f)
        print(f"  Procesando {f}...")

        filas_existentes = []
        try:
            with open(ruta, newline='', encoding='utf-8') as fp:
                for row in csv.DictReader(fp):
                    try:
                        filas_existentes.append({
                            'ventana_fin_ms': int(row['ventana_fin_ms']),
                            'row': row,
                        })
                    except (KeyError, ValueError):
                        continue
        except FileNotFoundError:
            continue

        if not filas_existentes:
            continue

        # Recalcular CVD y VWAP diario para este día
        filas_ordenadas = sorted(filas_existentes, key=lambda x: x['ventana_fin_ms'])
        cvd_acum = 0.0
        vol_acum = 0.0
        vwap_num = 0.0

        for fila_data in filas_ordenadas:
            ventana = fila_data['ventana_fin_ms']
            row = fila_data['row']

            # Recalcular delta desde trades
            vb = por_ventana[ventana]['buy']
            vs = por_ventana[ventana]['sell']
            delta = vb - vs

            # Acumular CVD
            cvd_acum += delta

            # Acumular VWAP diario
            try:
                vwap = float(row.get('vwap', 0.0))
            except (ValueError, TypeError):
                vwap = 0.0

            vol = vb + vs
            vwap_num += vwap * vol
            vol_acum += vol
            vwap_diario = round(vwap_num / vol_acum, 4) if vol_acum > 0 else 0.0

            # Actualizar fila
            row['vol_buy'] = str(round(vb, 6))
            row['vol_sell'] = str(round(vs, 6))
            row['delta_vol'] = str(round(delta, 6))
            row['cvd'] = str(round(cvd_acum, 6))
            row['vwap_diario'] = str(vwap_diario)

            flujo_archivos[ruta].append(row)

    # Escribir archivos reparados
    campos = [
        "ventana_fin_ms", "fecha_utc", "coin",
        "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
        "ts_primer_trade", "ts_ultimo_trade", "cobertura_pct",
        "precio_apertura", "precio_cierre", "precio_max", "precio_min", "vwap", "vwap_diario",
    ]

    for ruta, filas in flujo_archivos.items():
        if filas:
            tmp = ruta + ".tmp"
            with open(tmp, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=campos, extrasaction='ignore')
                w.writeheader()
                w.writerows(filas)
            os.replace(tmp, ruta)
            print(f"    ✓ {os.path.basename(ruta)} reparado ({len(filas)} ventanas)")

    print(f"✅ CVD reparado para {coin}/{mercado}\n")
    return True


def main():
    p = argparse.ArgumentParser(description="Repara CVD en flujo_*.csv desde trades deduplicados")
    p.add_argument("coin", nargs="?", default="eth", help="moneda (default: eth)")
    p.add_argument("--mercado", default="futuros", help="mercado (default: futuros)")
    p.add_argument("--datos", default=DIR_DATOS, help="carpeta con flujo_*.csv y trades_*.csv")
    args = p.parse_args()

    coin = args.coin.upper()
    if not reparar_cvd(coin, args.mercado, args.datos):
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
