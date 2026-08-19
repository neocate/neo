import pandas as pd
import json
from pathlib import Path

def cargar_grabador(ruta_csv):
    df = pd.read_csv(ruta_csv)
    df['fecha_utc'] = pd.to_datetime(df['fecha_utc'])
    df['timestamp_local_ms'] = pd.to_numeric(df['timestamp_local_ms'])
    df['timestamp_exchange_ms'] = pd.to_numeric(df['timestamp_exchange_ms'], errors='coerce')
    return df

def filtrar_validos(df):
    df_ok = df[df['estado'] == 'ok'].copy()
    print(f"Total filas: {len(df)}, válidas: {len(df_ok)} ({100*len(df_ok)/len(df):.1f}%)")
    if len(df) > len(df_ok):
        gaps = df[df['estado'] != 'ok']
        print(f"  Gaps encontrados en: {gaps['fecha_utc'].min()} a {gaps['fecha_utc'].max()}")
    return df_ok

def parsear_libro(df):
    df['bids'] = df['bids_json'].apply(lambda x: json.loads(x) if x else [])
    df['asks'] = df['asks_json'].apply(lambda x: json.loads(x) if x else [])
    df['bid_price'] = df['bids'].apply(lambda x: x[0][0] if x else None)
    df['ask_price'] = df['asks'].apply(lambda x: x[0][0] if x else None)
    df['mid_price'] = (df['bid_price'] + df['ask_price']) / 2
    return df

def extraer_profundidad(df, moneda="BTC", nivel=5):
    def vol_acumulado(bids_list, asks_list, n_niveles):
        bid_vol = sum(b[1] for b in bids_list[:n_niveles])
        ask_vol = sum(a[1] for a in asks_list[:n_niveles])
        return bid_vol, ask_vol

    df[f'bid_vol_{nivel}'] = df['bids'].apply(
        lambda x: sum(b[1] for b in x[:nivel]) if x else 0
    )
    df[f'ask_vol_{nivel}'] = df['asks'].apply(
        lambda x: sum(a[1] for a in x[:nivel]) if x else 0
    )
    return df

def ejemplo_uso():
    ruta = Path("herramientas/libro/flujo_20260819_BTC-ETH.csv")
    if not ruta.exists():
        print(f"No encontrado: {ruta}")
        return

    print("=== Cargando datos ===")
    df = cargar_grabador(ruta)

    print("\n=== Filtrando datos válidos ===")
    df = filtrar_validos(df)

    print("\n=== Parseando libro ===")
    df = parsear_libro(df)

    print("\n=== Extrayendo profundidad ===")
    df = extraer_profundidad(df, nivel=5)
    df = extraer_profundidad(df, nivel=10)

    print("\n=== Resumen por moneda ===")
    for coin in df['coin'].unique():
        df_coin = df[df['coin'] == coin]
        print(f"\n{coin}:")
        print(f"  Período: {df_coin['fecha_utc'].min()} a {df_coin['fecha_utc'].max()}")
        print(f"  Filas: {len(df_coin)}")
        print(f"  Precio bid medio: {df_coin['bid_price'].mean():.2f}")
        print(f"  Precio ask medio: {df_coin['ask_price'].mean():.2f}")
        print(f"  Spread promedio: {(df_coin['ask_price'] - df_coin['bid_price']).mean():.6f}")
        print(f"  Volumen bid (5 niveles): {df_coin['bid_vol_5'].mean():.2f}")
        print(f"  Volumen ask (5 niveles): {df_coin['ask_vol_5'].mean():.2f}")
        print(f"  Imbalance: {df_coin['imbalance'].mean():.4f}")
        print(f"  Funding rate: {df_coin['funding_rate_pct'].mean():.6f}%")
        print(f"  CVD final: {df_coin['cvd'].iloc[-1]:.2f}")

    print("\n=== Guardando datos limpios ===")
    salida = ruta.parent / f"{ruta.stem}_clean.csv"
    df.to_csv(salida, index=False)
    print(f"Guardado: {salida}")

    return df

if __name__ == "__main__":
    df = ejemplo_uso()
