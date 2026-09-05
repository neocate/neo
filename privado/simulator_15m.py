import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
import time
import sys
import argparse

sys.path.insert(0, str(Path("alertas")))
try:
    import avisos
except:
    avisos = None

DIR_NAS_VELAS = Path("velas/ETH")
DIR_NAS_LIBRO = Path("libro/datos")
DIR_NAS_PRIVADO = Path("privado")
ARCHIVO_ESTADO = DIR_NAS_PRIVADO / "posiciones.json"
ARCHIVO_LOG = DIR_NAS_PRIVADO / "simulator_15m.log"

DIR_NAS_PRIVADO.mkdir(exist_ok=True)

SL_PUNTOS = 0.50
TP_PUNTOS = 1.00
CAPITAL_TOTAL = 100.0
CAPITAL_EN_JUEGO = 50.0
MARGEN_POR_TRADE = 5.0
LEVERAGE = 10
COMISION = 0.0004

def _log(msg):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{timestamp}] {msg}"
    print(linea)
    with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")

def cargar_velas(archivo_velas):
    if not archivo_velas.exists():
        _log(f"ERROR: {archivo_velas} no existe")
        return None
    try:
        df = pd.read_csv(archivo_velas)
        df['fecha_utc'] = pd.to_datetime(df['fecha_utc'])
        df = df.sort_values('fecha_utc').reset_index(drop=True)
        return df
    except Exception as e:
        _log(f"ERROR cargar velas: {e}")
        return None

def calcular_ema(series, periodo):
    return series.ewm(span=periodo, adjust=False).mean()

def actualizar_ema_incremental(ema_anterior, precio_nuevo, periodo):
    """Actualiza EMA con una nueva vela usando fórmula incremental"""
    if ema_anterior is None:
        return precio_nuevo
    alfa = 2.0 / (periodo + 1)
    return alfa * precio_nuevo + (1 - alfa) * ema_anterior

def leer_estado(num_velas_total):
    if ARCHIVO_ESTADO.exists():
        try:
            with open(ARCHIVO_ESTADO, encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {
        "capital_disponible": CAPITAL_TOTAL,
        "capital_en_juego": 0.0,
        "posiciones": [],
        "trades_cerrados": [],
        "ultima_vela_idx": num_velas_total - 1,
        "ema12_ultima": None,
        "ema26_ultima": None
    }

def guardar_estado(estado):
    def convertir_valores(obj):
        if isinstance(obj, dict):
            return {k: convertir_valores(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convertir_valores(v) for v in obj]
        elif isinstance(obj, float):
            if np.isnan(obj) or np.isinf(obj):
                return None
            return obj
        return obj

    estado_limpio = convertir_valores(estado)
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado_limpio, f, indent=2)

def obtener_libro_actual(fecha_vela):
    fecha_str = fecha_vela.strftime("%Y%m%d")
    archivo_libro = DIR_NAS_LIBRO / f"libro_ETH_futuros_{fecha_str}.csv"

    if not archivo_libro.exists():
        return None

    try:
        df_libro = pd.read_csv(archivo_libro)
        if df_libro.empty:
            return None
        ultima_fila = df_libro.iloc[-1]
        return {
            'last_price': ultima_fila['last_price'],
            'bids_json': ultima_fila['bids_json'],
            'asks_json': ultima_fila['asks_json']
        }
    except Exception as e:
        _log(f"ERROR leyendo libro: {e}")
        return None

def main(timeframe="15m", loop_secs=60):
    archivo_velas = DIR_NAS_VELAS / f"bitget_ETH_{timeframe}_futuros.csv"
    _log(f"Iniciando simulador {timeframe} desde NAS (loop={loop_secs}s)")
    ultima_mtime = 0

    while True:
        try:
            df = cargar_velas(archivo_velas)
            if df is None or len(df) < 50:
                _log("Esperando velas...")
                time.sleep(loop_secs)
                continue

            mtime = archivo_velas.stat().st_mtime
            if mtime == ultima_mtime:
                time.sleep(loop_secs)
                continue

            ultima_mtime = mtime

            estado = leer_estado(len(df))
            ultima_idx = estado.get("ultima_vela_idx", len(df) - 1)
            ema12_anterior = estado.get("ema12_ultima")
            ema26_anterior = estado.get("ema26_ultima")

            # Calcular EMA completo solo si es la primera ejecución
            if ema12_anterior is None:
                df['ema12'] = calcular_ema(df['close'], 12)
                df['ema26'] = calcular_ema(df['close'], 26)
                if ultima_idx >= 0:
                    ema12_anterior = df.iloc[ultima_idx]['ema12']
                    ema26_anterior = df.iloc[ultima_idx]['ema26']

            for idx in range(ultima_idx + 1, len(df)):
                vela = df.iloc[idx]
                precio_vela = vela['close']
                fecha_vela = vela['fecha_utc']

                # Actualizar EMA incrementalmente
                ema12 = actualizar_ema_incremental(ema12_anterior, precio_vela, 12)
                ema26 = actualizar_ema_incremental(ema26_anterior, precio_vela, 26)
                ema12_anterior = ema12
                ema26_anterior = ema26

                _log(f"Vela {idx}: {fecha_vela} | Precio: {precio_vela:.2f}")

                senal_anterior = (df.iloc[idx-1]['ema12'] > df.iloc[idx-1]['ema26']) if idx > 0 else None
                senal_actual = ema12 > ema26

                libro_actual = obtener_libro_actual(fecha_vela)

                if senal_anterior is not None and senal_anterior != senal_actual:
                    num_posiciones = len(estado['posiciones'])
                    max_posiciones = int(CAPITAL_EN_JUEGO / MARGEN_POR_TRADE)

                    if senal_actual and num_posiciones < max_posiciones:
                        if estado['capital_disponible'] >= MARGEN_POR_TRADE:
                            entrada_precio = precio_vela
                            sl_precio = entrada_precio - SL_PUNTOS
                            tp_precio = entrada_precio + TP_PUNTOS

                            posicion = {
                                'entrada_idx': idx,
                                'entrada_fecha': str(fecha_vela),
                                'entrada_precio': entrada_precio,
                                'tipo': 'LONG',
                                'sl_precio': sl_precio,
                                'tp_precio': tp_precio,
                                'sl_tocado': False,
                                'tp_tocado': False
                            }
                            estado['posiciones'].append(posicion)
                            estado['capital_disponible'] -= MARGEN_POR_TRADE
                            estado['capital_en_juego'] += MARGEN_POR_TRADE
                            msg = f"🟢 ENTRADA LONG\nPrecio: {entrada_precio:.2f}\nSL: {sl_precio:.2f}\nTP: {tp_precio:.2f}\nCapital: {estado['capital_disponible']:.2f}"
                            _log(f"  ENTRADA LONG: {entrada_precio:.2f} | SL: {sl_precio:.2f} | TP: {tp_precio:.2f}")
                            if avisos:
                                avisos.enviar(msg)

                    elif not senal_actual and num_posiciones < max_posiciones:
                        if estado['capital_disponible'] >= MARGEN_POR_TRADE:
                            entrada_precio = precio_vela
                            sl_precio = entrada_precio + SL_PUNTOS
                            tp_precio = entrada_precio - TP_PUNTOS

                            posicion = {
                                'entrada_idx': idx,
                                'entrada_fecha': str(fecha_vela),
                                'entrada_precio': entrada_precio,
                                'tipo': 'SHORT',
                                'sl_precio': sl_precio,
                                'tp_precio': tp_precio,
                                'sl_tocado': False,
                                'tp_tocado': False
                            }
                            estado['posiciones'].append(posicion)
                            estado['capital_disponible'] -= MARGEN_POR_TRADE
                            estado['capital_en_juego'] += MARGEN_POR_TRADE
                            msg = f"🔴 ENTRADA SHORT\nPrecio: {entrada_precio:.2f}\nSL: {sl_precio:.2f}\nTP: {tp_precio:.2f}\nCapital: {estado['capital_disponible']:.2f}"
                            _log(f"  ENTRADA SHORT: {entrada_precio:.2f} | SL: {sl_precio:.2f} | TP: {tp_precio:.2f}")
                            if avisos:
                                avisos.enviar(msg)

                posiciones_a_cerrar = []
                for i, pos in enumerate(estado['posiciones']):
                    if pos['tipo'] == 'LONG':
                        if precio_vela <= pos['sl_precio'] and not pos['sl_tocado']:
                            ganancia_puntos = -SL_PUNTOS
                            pos['sl_tocado'] = True
                            posiciones_a_cerrar.append((i, precio_vela, 'SL', ganancia_puntos))

                        if precio_vela >= pos['tp_precio'] and not pos['tp_tocado']:
                            ganancia_puntos = TP_PUNTOS
                            pos['tp_tocado'] = True
                            posiciones_a_cerrar.append((i, precio_vela, 'TP', ganancia_puntos))

                    else:
                        if precio_vela >= pos['sl_precio'] and not pos['sl_tocado']:
                            ganancia_puntos = -SL_PUNTOS
                            pos['sl_tocado'] = True
                            posiciones_a_cerrar.append((i, precio_vela, 'SL', ganancia_puntos))

                        if precio_vela <= pos['tp_precio'] and not pos['tp_tocado']:
                            ganancia_puntos = TP_PUNTOS
                            pos['tp_tocado'] = True
                            posiciones_a_cerrar.append((i, precio_vela, 'TP', ganancia_puntos))

                for idx_pos, salida_precio, razon, ganancia_puntos in reversed(posiciones_a_cerrar):
                    pos = estado['posiciones'][idx_pos]
                    ganancia_usd = ganancia_puntos * MARGEN_POR_TRADE * LEVERAGE
                    comisiones_usd = (MARGEN_POR_TRADE * LEVERAGE) * COMISION
                    ganancia_neta = ganancia_usd - comisiones_usd

                    estado['capital_disponible'] += MARGEN_POR_TRADE + ganancia_neta
                    estado['capital_en_juego'] -= MARGEN_POR_TRADE

                    trade = {
                        'entrada_fecha': pos['entrada_fecha'],
                        'salida_fecha': str(fecha_vela),
                        'tipo': pos['tipo'],
                        'entrada': pos['entrada_precio'],
                        'salida': salida_precio,
                        'ganancia_puntos': ganancia_puntos,
                        'ganancia_usd': ganancia_usd,
                        'comisiones': comisiones_usd,
                        'ganancia_neta': ganancia_neta,
                        'razon': razon
                    }
                    estado['trades_cerrados'].append(trade)
                    emoji = "✅" if ganancia_neta > 0 else "❌"
                    msg = f"{emoji} SALIDA {razon}\n{pos['tipo']}\nEntrada: {pos['entrada_precio']:.2f}\nSalida: {salida_precio:.2f}\nGanancia: {ganancia_neta:.2f} USDT\nCapital: {estado['capital_disponible']:.2f}"
                    _log(f"  SALIDA {razon}: {salida_precio:.2f} | Ganancia neta: {ganancia_neta:.2f} USDT | Capital: {estado['capital_disponible']:.2f}")
                    if avisos:
                        avisos.enviar(msg)

                    del estado['posiciones'][idx_pos]

                estado['ultima_vela_idx'] = idx
                estado['ema12_ultima'] = ema12_anterior
                estado['ema26_ultima'] = ema26_anterior
                guardar_estado(estado)

            time.sleep(loop_secs)

        except KeyboardInterrupt:
            _log("Detenido por usuario")
            break
        except Exception as e:
            _log(f"Error: {e}")
            time.sleep(loop_secs)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulador trading EMA 12/26")
    parser.add_argument("--tf", default="15m", help="Timeframe (1m, 3m, 5m, 15m, etc)")
    parser.add_argument("--loop", type=int, default=60, help="Sleep seconds between iterations")
    args = parser.parse_args()

    main(timeframe=args.tf, loop_secs=args.loop)
