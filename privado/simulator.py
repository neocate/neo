import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
import time
import sys
import argparse
import uuid
import os

sys.path.insert(0, str(Path("alertas")))
try:
    import avisos
except:
    avisos = None

DIR_NAS_VELAS = Path(__file__).parent.parent / "velas" / "ETH"
DIR_NAS_PRIVADO = Path(__file__).parent
ARCHIVO_ESTADO = DIR_NAS_PRIVADO / "posiciones.json"
ARCHIVO_LOG = DIR_NAS_PRIVADO / "simulator.log"
ARCHIVO_LOCK = DIR_NAS_PRIVADO / "simulator.lock"

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
    except (IOError, OSError) as e:
        _log(f"⚠ Archivo bloqueado: {archivo_velas.name} (reintentar próximo loop)")
        return None
    except Exception as e:
        _log(f"ERROR cargar velas: {e}")
        return None

def calcular_ema(series, periodo):
    return series.ewm(span=periodo, adjust=False).mean()

def actualizar_ema_incremental(ema_anterior, precio_nuevo, periodo):
    if ema_anterior is None:
        return precio_nuevo
    alfa = 2.0 / (periodo + 1)
    return alfa * precio_nuevo + (1 - alfa) * ema_anterior

def leer_estado(num_velas_15m, num_velas_1m, ema1, ema2):
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
        "ultima_vela_15m_idx": num_velas_15m - 1,
        "ultima_vela_1m_idx": num_velas_1m - 1,
        "ema1_ultima": None,
        "ema2_ultima": None,
        "senal_anterior": None,
        "ema1_periodo": ema1,
        "ema2_periodo": ema2
    }

def guardar_estado(estado):
    def convertir_valores(obj):
        if isinstance(obj, dict):
            return {k: convertir_valores(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convertir_valores(v) for v in obj]
        elif isinstance(obj, (bool, np.bool_)):
            return bool(obj)
        elif isinstance(obj, float):
            if np.isnan(obj) or np.isinf(obj):
                return None
            return obj
        return obj

    estado_limpio = convertir_valores(estado)
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado_limpio, f, indent=2)

def verificar_lock():
    if ARCHIVO_LOCK.exists():
        try:
            with open(ARCHIVO_LOCK, "r") as f:
                pid_antiguo = f.read().strip()
            print(f"ERROR: Ya hay un simulador corriendo (PID {pid_antiguo})")
            print(f"Mata el proceso anterior con: kill -9 {pid_antiguo}")
            sys.exit(1)
        except:
            pass
    
    with open(ARCHIVO_LOCK, "w") as f:
        f.write(str(os.getpid()))

def limpiar_lock():
    if ARCHIVO_LOCK.exists():
        ARCHIVO_LOCK.unlink()

def procesar_senal_15m(idx_15m, vela_15m, ema1, ema2, df_15m, estado, ema1_periodo, ema2_periodo):
    precio_15m = vela_15m['close']
    fecha_15m = vela_15m['fecha_utc']

    senal_actual = ema1 > ema2
    senal_anterior = estado.get('senal_anterior')

    if senal_anterior is None or senal_anterior == senal_actual:
        estado['senal_anterior'] = senal_actual
        return

    num_posiciones = len(estado['posiciones'])
    max_posiciones = int(CAPITAL_EN_JUEGO / MARGEN_POR_TRADE)

    if num_posiciones >= max_posiciones or estado['capital_disponible'] < MARGEN_POR_TRADE:
        return

    if senal_actual:
        entrada_precio = precio_15m
        sl_precio = entrada_precio - SL_PUNTOS
        tp_precio = entrada_precio + TP_PUNTOS
        posicion = {
            'id': str(uuid.uuid4())[:8],
            'entrada_idx': idx_15m,
            'entrada_fecha': str(fecha_15m),
            'entrada_precio': entrada_precio,
            'tipo': 'LONG',
            'sl_precio': sl_precio,
            'tp_precio': tp_precio,
            'sl_tocado': False,
            'tp_tocado': False
        }
        msg = f"ENTRADA LONG\nID: {posicion['id']}\nPrecio: {entrada_precio:.2f}\nSL: {sl_precio:.2f}\nTP: {tp_precio:.2f}\nCapital: {estado['capital_disponible']:.2f}"
    else:
        entrada_precio = precio_15m
        sl_precio = entrada_precio + SL_PUNTOS
        tp_precio = entrada_precio - TP_PUNTOS
        posicion = {
            'id': str(uuid.uuid4())[:8],
            'entrada_idx': idx_15m,
            'entrada_fecha': str(fecha_15m),
            'entrada_precio': entrada_precio,
            'tipo': 'SHORT',
            'sl_precio': sl_precio,
            'tp_precio': tp_precio,
            'sl_tocado': False,
            'tp_tocado': False
        }
        msg = f"ENTRADA SHORT\nID: {posicion['id']}\nPrecio: {entrada_precio:.2f}\nSL: {sl_precio:.2f}\nTP: {tp_precio:.2f}\nCapital: {estado['capital_disponible']:.2f}"
    
    estado['posiciones'].append(posicion)
    estado['capital_disponible'] -= MARGEN_POR_TRADE
    estado['capital_en_juego'] += MARGEN_POR_TRADE
    estado['senal_anterior'] = senal_actual
    if avisos:
        avisos.enviar(msg)

def procesar_ejecucion_1m(idx_1m, precio_1m, fecha_1m, estado):
    posiciones_a_cerrar = []

    for i, pos in enumerate(estado['posiciones']):
        if pos['sl_tocado'] or pos['tp_tocado']:
            continue

        is_long = pos['tipo'] == 'LONG'
        hit_sl = (precio_1m <= pos['sl_precio']) if is_long else (precio_1m >= pos['sl_precio'])
        hit_tp = (precio_1m >= pos['tp_precio']) if is_long else (precio_1m <= pos['tp_precio'])

        if hit_sl:
            pos['sl_tocado'] = True
            salida_precio = pos['sl_precio']
            posiciones_a_cerrar.append((i, salida_precio, 'SL'))
        elif hit_tp:
            pos['tp_tocado'] = True
            salida_precio = pos['tp_precio']
            posiciones_a_cerrar.append((i, salida_precio, 'TP'))
    
    for idx_pos, salida_precio, razon in reversed(posiciones_a_cerrar):
        pos = estado['posiciones'][idx_pos]
        entrada_precio = pos['entrada_precio']
        is_long = pos['tipo'] == 'LONG'

        ganancia_puntos = (salida_precio - entrada_precio) if is_long else (entrada_precio - salida_precio)
        cantidad_eth = (MARGEN_POR_TRADE * LEVERAGE) / entrada_precio
        ganancia_usd = ganancia_puntos * cantidad_eth
        comisiones_usd = (MARGEN_POR_TRADE * LEVERAGE) * COMISION
        ganancia_neta = ganancia_usd - comisiones_usd

        estado['capital_disponible'] += MARGEN_POR_TRADE + ganancia_neta
        estado['capital_en_juego'] -= MARGEN_POR_TRADE

        trade = {
            'entrada_fecha': pos['entrada_fecha'],
            'salida_fecha': str(fecha_1m),
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
        msg = f"SALIDA {razon}\nID: {pos['id']}\n{pos['tipo']}\nEntrada: {pos['entrada_precio']:.2f}\nSalida: {salida_precio:.2f}\nGanancia: {ganancia_neta:.2f} USDT\nCapital: {estado['capital_disponible']:.2f}"
        if avisos:
            avisos.enviar(msg)

        del estado['posiciones'][idx_pos]

def main(timeframe="15m", loop_secs=60, ema1_periodo=9, ema2_periodo=18):
    verificar_lock()

    archivo_velas_15m = DIR_NAS_VELAS / f"bitget_ETH_{timeframe}_futuros.csv"
    archivo_velas_1m = DIR_NAS_VELAS / "bitget_ETH_1m_futuros.csv"
    _log(f"Iniciando simulador: {timeframe} señales, EMA {ema1_periodo}/{ema2_periodo}, ejecución 1m (loop={loop_secs}s)")

    try:
        while True:
            try:
                df_15m = cargar_velas(archivo_velas_15m)
                df_1m = cargar_velas(archivo_velas_1m)
                if df_15m is None or len(df_15m) < 50 or df_1m is None or len(df_1m) < 50:
                    _log("Esperando velas...")
                    time.sleep(loop_secs)
                    continue

                estado = leer_estado(len(df_15m), len(df_1m), ema1_periodo, ema2_periodo)
                ultima_idx_15m = estado.get("ultima_vela_15m_idx", len(df_15m) - 1)
                ultima_idx_1m = estado.get("ultima_vela_1m_idx", len(df_1m) - 1)
                ema1_anterior = estado.get("ema1_ultima")
                ema2_anterior = estado.get("ema2_ultima")

                df_15m['ema1'] = calcular_ema(df_15m['close'], ema1_periodo)
                df_15m['ema2'] = calcular_ema(df_15m['close'], ema2_periodo)

                if ema1_anterior is None and ultima_idx_15m >= 0:
                    ema1_anterior = df_15m.iloc[ultima_idx_15m]['ema1']
                    ema2_anterior = df_15m.iloc[ultima_idx_15m]['ema2']

                for idx_15m in range(ultima_idx_15m + 1, len(df_15m)):
                    vela_15m = df_15m.iloc[idx_15m]
                    precio_15m = vela_15m['close']

                    ema1 = actualizar_ema_incremental(ema1_anterior, precio_15m, ema1_periodo)
                    ema2 = actualizar_ema_incremental(ema2_anterior, precio_15m, ema2_periodo)
                    ema1_anterior = ema1
                    ema2_anterior = ema2

                    df_15m.at[idx_15m, 'ema1'] = ema1
                    df_15m.at[idx_15m, 'ema2'] = ema2

                    procesar_senal_15m(idx_15m, vela_15m, ema1, ema2, df_15m, estado, ema1_periodo, ema2_periodo)
                    estado['ultima_vela_15m_idx'] = idx_15m
                    estado['ema1_ultima'] = ema1_anterior
                    estado['ema2_ultima'] = ema2_anterior

                for idx_1m in range(ultima_idx_1m + 1, len(df_1m)):
                    vela_1m = df_1m.iloc[idx_1m]
                    precio_1m = vela_1m['close']
                    fecha_1m = vela_1m['fecha_utc']
                    
                    procesar_ejecucion_1m(idx_1m, precio_1m, fecha_1m, estado)
                    estado['ultima_vela_1m_idx'] = idx_1m

                guardar_estado(estado)
                time.sleep(loop_secs)

            except KeyboardInterrupt:
                _log("Detenido por usuario")
                break
            except Exception as e:
                _log(f"Error: {e}")
                time.sleep(loop_secs)
    finally:
        limpiar_lock()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simulador de trading con EMA parametrizable",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Parámetros:
  -tf TIMEFRAME     Timeframe para generar señales (default: 15m)
  -ema1 PERIODO     Primer EMA (default: 9)
  -ema2 PERIODO     Segundo EMA (default: 18)
  -loop SEGUNDOS    Intervalo de verificación (default: 60s)

Ejemplos:
  python simulator_15m.py -tf 5m -ema1 9 -ema2 18 -loop 60
  python simulator_15m.py -tf 15m -ema1 12 -ema2 26 -loop 30
        """
    )
    parser.add_argument("-tf", default="15m", help="Timeframe (1m, 3m, 5m, 15m, etc)")
    parser.add_argument("-ema1", type=int, default=9, help="Primer período EMA")
    parser.add_argument("-ema2", type=int, default=18, help="Segundo período EMA")
    parser.add_argument("-loop", type=int, default=60, help="Segundos entre iteraciones")
    args = parser.parse_args()

    main(timeframe=args.tf, loop_secs=args.loop, ema1_periodo=args.ema1, ema2_periodo=args.ema2)
