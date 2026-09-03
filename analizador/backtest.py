#!/usr/bin/env python3
"""
ETH Backtest - Comparar predicciones vs mercado real
Analiza eth_setup_log.csv vs velas reales con P&L realista
Incluye comisiones, slippage y datos reales del contrato
"""

import argparse
import pandas as pd
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PATHS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
DATA_DIR = SCRIPT_DIR / "datos"
LOG_DIR = SCRIPT_DIR / "log"
MERCADO_DIR = BASE_DIR / "mercado"

BACKTEST_LOG = LOG_DIR / "backtest.log"

# Duracion en minutos de cada TF de ejecucion soportado, para acotar la
# ventana de busqueda de la vela de cierre a su propio ancho de vela.
TF_MINUTOS = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440}


def _velas_dir(coin: str) -> Path:
    return BASE_DIR / "velas" / coin.upper()

# Agregar mercado y niveles al path
sys.path.insert(0, str(MERCADO_DIR))
sys.path.insert(0, str(BASE_DIR / "niveles"))
from io_velas import validar_coin

try:
    from contrato import obtener_contrato
    CONTRATO_DISPONIBLE = True
except ImportError:
    CONTRATO_DISPONIBLE = False
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning("contrato.py no disponible, usando valores por defecto")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BACKTEST_LOG),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_price_data(coin='ETH', mercado='futuros', exec_tf='5m'):
    """Cargar velas del TF de ejecucion indicado (usadas como precio de
    entrada/salida del backtest). Antes 'coin', 'mercado' y la vela de
    ejecucion estaban fijos a ETH/futuros/5m sin importar --tf ni la moneda
    real; ahora son parametros explicitos, con 5m como default declarado
    (no un hardcode escondido) por precision de la busqueda de precio."""
    file = _velas_dir(coin) / f"bitget_{coin.upper()}_{exec_tf}_{mercado}.csv"

    data = {}
    if file.exists():
        try:
            df = pd.read_csv(file)
            # utc=True para que sea comparable con analysis['timestamp']
            # (ver backtest_tf_dynamic): ambos deben quedar tz-aware en UTC.
            df['fecha_utc'] = pd.to_datetime(df['fecha_utc'], utc=True)
            data[exec_tf] = df
        except Exception as e:
            logger.error(f"Error loading {exec_tf}: {e}")
    else:
        logger.warning(f"Missing {exec_tf} data: {file}")

    return data

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONTRATO Y COMISIONES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_fees(coin='ETH'):
    """Obtener comisiones reales del contrato o usar defaults.

    Incluye 'fuente' ('contrato' o 'estimado') para dejar constancia en los
    resultados de si taker/maker vinieron de datos reales de Bitget o del
    fallback fijo. 'slippage' siempre es una estimacion propia: contrato.py
    no expone slippage real, asi que 'fuente' no cubre ese campo.
    """
    defaults = {
        'taker': 0.0006,  # 0.06%
        'maker': 0.0002,  # 0.02%
        'slippage': 0.0003,  # 0.03% estimado, aplicado por lado (ver calculate_pnl)
        'fuente': 'estimado',
    }
    if not CONTRATO_DISPONIBLE:
        return defaults

    try:
        contrato = obtener_contrato(f'{coin.upper()}/USDT:USDT')
        # 'fuente' dice de donde salen de VERDAD: 'cuenta' (tramo VIP real,
        # con credenciales), 'mercado' (tarifa publica del par) o 'estimado'
        # (los defaults). Antes ponia siempre 'contrato' aunque la peticion
        # hubiera fallado y se estuvieran usando los defaults.
        return {
            'taker': contrato['comision_taker'],
            'maker': contrato['comision_maker'],
            'slippage': 0.0003,  # Estimado: no lo expone ningun endpoint
            'fuente': contrato.get('comision_fuente', 'estimado'),
        }
    except Exception as e:
        logger.warning(f"Error leyendo contrato: {e}, usando defaults")
        return defaults

def calculate_pnl(signal, entry_price, exit_price, size=1.0, fees=None):
    """
    Calcula P&L realista incluyendo comisiones y slippage
    
    Args:
        signal: 'LONG' o 'SHORT'
        entry_price: Precio de entrada
        exit_price: Precio de salida
        size: Cantidad de contratos (default 1)
        fees: Dict con comisiones {'taker': 0.0006, 'slippage': 0.0003}
    
    Returns:
        dict con {
            'entry_price': float,
            'exit_price': float,
            'price_change_pct': float (sin comisiones),
            'pnl_gross': float (ganancia bruta),
            'comisiones': float (total comisiones),
            'pnl_neto': float (ganancia neta),
            'pnl_pct': float (% neto),
            'fee_fuente': str ('contrato' o 'estimado')
        }
    """
    if fees is None:
        fees = get_fees()

    if signal == 'LONG':
        # LONG: compra en entry, vende en exit
        price_change = exit_price - entry_price
        price_change_pct = (price_change / entry_price) * 100 if entry_price > 0 else 0
        pnl_gross = price_change * size
    elif signal == 'SHORT':
        # SHORT: vende en entry, compra en exit
        price_change = entry_price - exit_price
        price_change_pct = (price_change / entry_price) * 100 if entry_price > 0 else 0
        pnl_gross = price_change * size
    else:
        # WAIT - sin posición
        return None

    # Taker + slippage se aplican por lado (entrada sobre entry_price, salida
    # sobre exit_price), no como un cargo unico sobre el notional de entrada:
    # antes ambas patas se cobraban sobre entry_price, subestimando el coste
    # real de la salida cuando el precio se habia movido bastante.
    fee_pct_por_lado = fees['taker'] + fees['slippage']
    comision_entrada = entry_price * size * fee_pct_por_lado
    comision_salida = exit_price * size * fee_pct_por_lado
    comisiones = comision_entrada + comision_salida

    pnl_neto = pnl_gross - comisiones
    pnl_pct = (pnl_neto / (entry_price * size)) * 100 if entry_price > 0 else 0

    return {
        'entry_price': entry_price,
        'exit_price': exit_price,
        'price_change_pct': price_change_pct,
        'pnl_gross': pnl_gross,
        'comisiones': comisiones,
        'pnl_neto': pnl_neto,
        'pnl_pct': pnl_pct,
        'fee_fuente': fees.get('fuente', 'estimado'),
    }

def _resolver_salida(signal, entry_price, ts, df_exec, ventana, hours_ahead, sl_pct, tp_pct,
                      ts_reversal=None, precio_reversal=None):
    """Escanea las velas de ejecucion entre 'ts' y el limite buscando la
    primera que toque el stop-loss (sl_pct adverso) o el take-profit
    (tp_pct favorable), usando high/low intravela, no solo el cierre.

    Si ninguna vela toca ninguno de los dos, sale al cierre de la ultima
    vela disponible, pero solo si esa vela ya alcanza el horizonte (si los
    datos aun no llegan tan lejos, se devuelve PENDING en vez de cerrar
    prematuramente con una vela intermedia).

    Si una misma vela toca stop y take-profit a la vez, se asume que el
    stop se disparo primero (peor caso: sin datos intravela no hay forma de
    saber el orden real, y asumir lo favorable séria optimismo de
    look-ahead).

    Si 'ts_reversal' esta dado (timestamp de la siguiente señal de sentido
    contrario en el log), acota la busqueda a ese punto: no tiene sentido
    seguir midiendo un LONG contra el precio si el propio sistema ya disparo
    un SHORT antes de que tocara stop/tp/horizonte. Si nada lo toca antes,
    cierra ahi mismo al precio de esa señal (motivo REVERSAL).

    'hours_ahead=None' desactiva el cierre forzado por horizonte: la
    busqueda solo queda acotada por 'ts_reversal' (si lo hay); si tampoco
    hay reversal, escanea hasta el final de los datos de precio
    disponibles. Si nada toca stop/tp/reversal, la señal queda PENDING sin
    limite de tiempo -- la posicion se considera abierta hasta que el
    precio o una reversal la resuelvan, no hasta un plazo arbitrario.

    Devuelve (exit_price, exit_time, motivo) o None si sigue pendiente.
    """
    limite = None if hours_ahead is None else ts + timedelta(hours=hours_ahead)
    if ts_reversal is not None and (limite is None or ts_reversal < limite):
        limite = ts_reversal

    if limite is not None:
        velas = df_exec[(df_exec['fecha_utc'] >= ts) & (df_exec['fecha_utc'] <= limite)]
    else:
        velas = df_exec[df_exec['fecha_utc'] >= ts]
    velas = velas.sort_values('fecha_utc')

    if signal == 'LONG':
        precio_stop = entry_price * (1 - sl_pct)
        precio_tp = entry_price * (1 + tp_pct)
    else:  # SHORT
        precio_stop = entry_price * (1 + sl_pct)
        precio_tp = entry_price * (1 - tp_pct)

    for _, vela in velas.iterrows():
        alto, bajo = vela['high'], vela['low']
        if signal == 'LONG':
            toco_stop = bajo <= precio_stop
            toco_tp = alto >= precio_tp
        else:
            toco_stop = alto >= precio_stop
            toco_tp = bajo <= precio_tp

        if toco_stop:
            return precio_stop, vela['fecha_utc'], 'STOP'
        if toco_tp:
            return precio_tp, vela['fecha_utc'], 'TP'

    # Ni stop ni TP se tocaron antes del limite. Si el limite era la
    # reversion (llego antes que el horizonte, o no hay horizonte), cerramos
    # ahi.
    if ts_reversal is not None and limite == ts_reversal:
        return precio_reversal, ts_reversal, 'REVERSAL'

    if hours_ahead is None:
        # Sin horizonte y sin reversal: la posicion sigue abierta hasta que
        # el precio la resuelva, por mucho que se acaben los datos.
        return None

    if velas.empty:
        return None

    ultima = velas.iloc[-1]
    if ultima['fecha_utc'] >= limite - ventana:
        return ultima['close'], ultima['fecha_utc'], 'HORIZONTE'
    return None


def evaluate_prediction(row, price_data, exec_tf='5m', hours_ahead=1, fees=None,
                         sl_pct=0.03, tp_pct=0.10, ts_reversal=None, precio_reversal=None):
    """
    Resuelve la salida de una señal LONG/SHORT con P&L realista (comisiones,
    slippage y fees reales del contrato incluidos). Sale por stop-loss,
    take-profit, una señal de sentido contrario (ts_reversal/precio_reversal)
    o el cierre al horizonte, lo que ocurra primero. Con hours_ahead=None no
    hay horizonte: solo sale por stop/tp/reversal (ver _resolver_salida).

    No clasifica CORRECT/WRONG/win aqui: con margen aislado el resultado
    depende de cuanto capital tenia esa entrada, asi que es
    backtest_tf_dynamic quien decide win/result sobre el pnl ya escalado
    a la qty real (ver ese comentario). Un pnl_neto negativo fijo (ej. "-5
    USDT") no significa lo mismo con tamanos de posicion distintos.
    """
    if fees is None:
        fees = get_fees()

    ts = row['timestamp']
    signal = row['signal']
    entry_price = row['price']

    df_exec = price_data.get(exec_tf)
    if df_exec is None:
        return None

    vela_min = TF_MINUTOS.get(exec_tf, 5)
    ventana = timedelta(minutes=vela_min)

    # Buscar vela de ejecucion actual (confirma que hay datos cubriendo ts)
    mask = (df_exec['fecha_utc'] >= ts) & (df_exec['fecha_utc'] < ts + ventana)
    if df_exec[mask].empty:
        return None

    salida = _resolver_salida(signal, entry_price, ts, df_exec, ventana, hours_ahead, sl_pct, tp_pct,
                               ts_reversal, precio_reversal)
    if salida is None:
        return 'PENDING'
    exit_price, exit_time, motivo = salida

    pnl_data = calculate_pnl(signal, entry_price, exit_price, size=1.0, fees=fees)

    return {
        'timestamp': ts,
        'signal': signal,
        'entry_price': entry_price,
        'exit_price': exit_price,
        'exit_time': exit_time,
        'motivo_salida': motivo,
        'price_change_pct': pnl_data['price_change_pct'],
        'pnl_gross': pnl_data['pnl_gross'],
        'pnl_neto': pnl_data['pnl_neto'],
        'pnl_pct': pnl_data['pnl_pct'],
        'comisiones': pnl_data['comisiones'],
        'fee_fuente': pnl_data['fee_fuente'],
    }

def _localizar_log(tf, fuente='auto'):
    """CSV de senales a backtestear.

    analyzer.py escribe en DOS sitios distintos: el modo vivo en
    eth_setup_log_<tf>.csv y el replay en eth_setup_hist_log_<tf>.csv. El
    backtest solo miraba el primero, asi que un replay recien corrido no se
    podia backtestear: leia el log de produccion, o daba "No data" si no
    existia. Con 'auto' se coge el mas reciente de los dos y se dice cual.
    """
    vivo = DATA_DIR / f"eth_setup_log_{tf}.csv"
    hist = DATA_DIR / f"eth_setup_hist_log_{tf}.csv"
    if fuente == 'vivo':
        return vivo
    if fuente == 'replay':
        return hist
    cands = [p for p in (hist, vivo) if p.exists()]
    if not cands:
        return vivo
    return max(cands, key=lambda p: p.stat().st_mtime)


def backtest_tf_dynamic(tf, hours_ahead=1, initial_capital=25, margin_pct=0.10,
                         coin='ETH', mercado='futuros', exec_tf='5m',
                         sl_pct=0.03, tp_pct=0.10, fuente='auto'):
    """Backtest con margen aislado por operación, una posición a la vez.

    Cada trade arriesga 'initial_capital' USDT de margen (apalancamiento
    1/margin_pct, ej. 10% -> 10x). El nominal y la cantidad de ETH se
    recalculan en cada entrada según el margen disponible y el precio
    vigente en ese momento. El margen de la siguiente entrada es el
    resultado acumulado: capital +- pnl neto (comisiones incluidas) de la
    operación anterior. Al ser aislado, la pérdida de una operación no
    puede superar el margen que se puso en ella.

    Sale por stop-loss (sl_pct), take-profit (tp_pct), reversal o el
    horizonte (hours_ahead=None desactiva el horizonte, ver
    _resolver_salida), lo que ocurra primero. Mientras una posición sigue
    abierta (no ha llegado su salida), se ignoran las señales siguientes:
    antes cada fila del log se trataba como un trade independiente que
    reinvertia el 100% del margen, aunque el horizonte de la anterior
    siguiera vigente -- con señales cada ~1 min y horizontes de horas, eso
    encadenaba decenas de posiciones que en la realidad habrian estado
    abiertas a la vez, disparando oscilaciones de capital sin sentido (ver
    conversacion).

    Una señal PENDING (sin exit_time: no toco stop/tp/reversal en los datos
    disponibles) tambien cuenta como posicion abierta y bloquea las
    siguientes hasta el final de los datos de precio: si se la tratara como
    "no paso nada", se podrian abrir posiciones nuevas encima de una que en
    la realidad seguiria activa. Con hours_ahead=None esto es mas frecuente
    (no hay plazo que la fuerce a cerrar).
    """
    csv_file = _localizar_log(tf, fuente)

    if not csv_file.exists():
        print(f"[AVISO] No data for {tf} (buscado: {csv_file.name})")
        return False
    print(f"[FUENTE] {csv_file.name}")
    
    # Cargar análisis
    try:
        analysis = pd.read_csv(csv_file)
        analysis['timestamp'] = pd.to_datetime(analysis['timestamp'], format='mixed', utc=True)
        analysis = analysis.sort_values('timestamp').reset_index(drop=True)
    except Exception as e:
        print(f"[ERROR] Error loading {tf}: {e}")
        return False
    
    price_data = load_price_data(coin, mercado, exec_tf)
    if not price_data:
        print(f"[ERROR] No price data for {tf}")
        return False

    df_exec_completo = price_data.get(exec_tf)
    fin_datos = (df_exec_completo['fecha_utc'].max()
                 if df_exec_completo is not None and not df_exec_completo.empty else None)

    fees = get_fees(coin)
    leverage = 1.0 / margin_pct
    capital = initial_capital
    capital_high = initial_capital
    max_drawdown_usdt = 0.0
    max_drawdown_pct = 0.0
    trades_executed = 0
    señales_ignoradas_solapadas = 0
    señales_pendientes = 0
    señales_sin_datos = 0
    posicion_abierta_hasta = None
    results = []
    
    for idx in range(len(analysis)):
        row = analysis.iloc[idx]

        if posicion_abierta_hasta is not None and row['timestamp'] < posicion_abierta_hasta:
            señales_ignoradas_solapadas += 1
            continue

        if row['signal'] not in ('LONG', 'SHORT'):
            continue

        # Primera señal de sentido contrario que aparezca despues de esta en
        # el log: si el propio sistema cambia de idea antes de que toque
        # stop/tp/horizonte, no tiene sentido seguir con la posicion abierta
        # en la direccion vieja.
        opuesto = 'SHORT' if row['signal'] == 'LONG' else 'LONG'
        futuras_opuestas = analysis.iloc[idx + 1:]
        futuras_opuestas = futuras_opuestas[futuras_opuestas['signal'] == opuesto]
        if not futuras_opuestas.empty:
            ts_reversal = futuras_opuestas['timestamp'].iloc[0]
            precio_reversal = futuras_opuestas['price'].iloc[0]
        else:
            ts_reversal = None
            precio_reversal = None

        result = evaluate_prediction(row, price_data, exec_tf=exec_tf, hours_ahead=hours_ahead,
                                      fees=fees, sl_pct=sl_pct, tp_pct=tp_pct,
                                      ts_reversal=ts_reversal, precio_reversal=precio_reversal)
        if result is None:
            señales_sin_datos += 1
            continue
        if result == 'PENDING':
            señales_pendientes += 1
            # Sigue abierta de verdad (no toco stop/tp/reversal todavia):
            # bloquea señales siguientes hasta donde llegan los datos de
            # precio, en vez de dejar la siguiente señal abrir encima.
            if fin_datos is not None:
                posicion_abierta_hasta = fin_datos
            continue

        posicion_abierta_hasta = result['exit_time']

        # Ejecutar trade con margen aislado
        entry_price = result['entry_price']
        pnl_neto = result['pnl_neto']

        # pnl_neto viene calculado para size=1.0 ETH (ver calculate_pnl). El
        # margen disponible (capital) apalancado 1/margin_pct compra
        # notional/entry_price ETH al precio vigente, asi que el P&L real
        # escala por esa cantidad, no por una notional fija: 2500 USDT solo
        # equivalia a 1 ETH cuando ETH cotizaba ahi, y deja de ser cierto en
        # cuanto el precio se mueve.
        margin = capital
        notional = margin * leverage
        qty = notional / entry_price
        pnl_escalado = pnl_neto * qty

        # Aislado: la perdida de esta posicion no puede superar el margen que
        # se puso en ella. Si el movimiento real habria perdido mas que el
        # margen, en la practica el exchange liquida la posicion antes de
        # llegar al motivo que calculo _resolver_salida (STOP/TP/HORIZONTE/
        # REVERSAL ya no aplica: el margen se agoto antes de eso).
        if pnl_escalado < -margin:
            pnl_escalado = -margin
            result['motivo_salida'] = 'LIQUIDATION'

        # Actualizar capital (margen para la siguiente entrada)
        capital += pnl_escalado
        trades_executed += 1
        
        # Track máximo capital y reduccion real (tras cada operacion, no solo
        # al final): la version anterior comparaba solo el capital inicial y
        # final, y no veia una caida profunda seguida de recuperacion.
        if capital > capital_high:
            capital_high = capital
        drawdown_usdt = capital_high - capital
        drawdown_pct = (drawdown_usdt / capital_high) if capital_high > 0 else 0.0
        if drawdown_usdt > max_drawdown_usdt:
            max_drawdown_usdt = drawdown_usdt
            max_drawdown_pct = drawdown_pct
        
        # Registrar trade. 'win'/'result' se calculan sobre pnl_escalado (lo
        # que de verdad mueve el saldo), no sobre el pnl_neto sin escalar que
        # devuelve evaluate_prediction (ese esta calculado para 1 ETH
        # nominal, no para el tamano real de esta entrada). Con margen
        # dinamico cada trade es ganador o perdedor, sin punto medio.
        result['margin'] = margin
        result['leverage'] = leverage
        result['notional'] = notional
        result['qty'] = qty
        result['win'] = pnl_escalado > 0
        result['result'] = 'CORRECT' if pnl_escalado > 0 else 'WRONG'
        result['capital_antes'] = capital - pnl_escalado
        result['capital_despues'] = capital
        result['pnl_escalado'] = pnl_escalado
        results.append(result)
        
        # Detener si ruina
        if capital <= 0:
            print(f"[RUINA] RUINA en trade {trades_executed}: capital = {capital:.2f} USDT")
            break
    
    if not results:
        print(f"[ERROR] No completed predictions for {tf}")
        return False
    
    # Guardar resultados
    df_results = pd.DataFrame(results)
    backtest_csv = DATA_DIR / f"eth_backtest_results_{tf}_dynamic.csv"
    df_results.to_csv(backtest_csv, index=False)
    
    # Estadísticas. Todo lo que llega aqui es un trade resuelto de verdad
    # (PENDING ya se descarto en el bucle) y ya viene con win/result sobre
    # pnl_escalado, asi que no hace falta filtrar nada mas: es todo el saldo.
    completed = df_results
    wins = completed['win'].sum() if len(completed) > 0 else 0
    
    horizonte_txt = 'sin horizonte' if hours_ahead is None else f"{hours_ahead}h max"
    print(f"\n{'='*70}")
    print(f"BACKTEST DINÁMICO (margen aislado, 1 posición a la vez): {tf.upper()} ({horizonte_txt})")
    print(f"Stop-loss:         {sl_pct:>9.1%}   Take-profit: {tp_pct:.1%}")
    print(f"Margen Inicial:    {initial_capital:>10.2f} USDT")
    print(f"Apalancamiento:    {leverage:>9.1f}x  (margen {margin_pct:.0%})")
    print(f"Margen Final:      {capital:>10.2f} USDT ({(capital-initial_capital):+.2f} USDT)")
    print(f"ESTADO:            {'RUINA' if capital <= 0 else 'OK'}")
    print(f"ROI:               {((capital-initial_capital)/initial_capital*100):>10.1f}%")
    print(f"Margen Máximo:     {capital_high:>10.2f} USDT")
    print(f"Máx Reducción:     {max_drawdown_usdt:>10.2f} USDT ({max_drawdown_pct:.1%})")
    print(f"\nOperaciones:")
    print(f"Total Trades:      {trades_executed:>10d}")
    print(f"Completadas:       {len(completed):>10d}")
    print(f"Señales ignoradas (posición ya abierta): {señales_ignoradas_solapadas:>6d}")
    print(f"Señales pendientes (sin stop/tp/reversal en los datos): {señales_pendientes:>6d}")
    print(f"Señales sin datos de ejecución: {señales_sin_datos:>6d}")
    if len(completed) > 0:
        win_rate = (wins / len(completed)) * 100
        print(f"Win Rate:          {win_rate:>10.1f}%")
        print(f"Ganadores:         {wins:>10d}")
        print(f"Perdedores:        {len(completed) - wins:>10d}")
        print(f"Avg P&L/Trade:     {completed['pnl_escalado'].mean():>+10.2f} USDT")
        print(f"Avg Notional:      {completed['notional'].mean():>10.2f} USDT")
        salidas = completed['motivo_salida'].value_counts()
        print(f"Salidas -> STOP: {salidas.get('STOP', 0)} | TP: {salidas.get('TP', 0)} | "
              f"REVERSAL: {salidas.get('REVERSAL', 0)} | HORIZONTE: {salidas.get('HORIZONTE', 0)} | "
              f"LIQUIDATION: {salidas.get('LIQUIDATION', 0)}")
    print(f"\nResultados guardados: {backtest_csv}")
    print(f"{'='*70}\n")
    
    return capital > 0

def _entero_positivo(valor):
    n = int(valor)
    if n <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return n


def _flotante_positivo(valor):
    n = float(valor)
    if n <= 0:
        raise argparse.ArgumentTypeError("debe ser un numero positivo")
    return n


def _fraccion_margen(valor):
    n = float(valor)
    if not (0 < n <= 1):
        raise argparse.ArgumentTypeError("debe estar en el rango (0, 1]")
    return n


def _coin_type(valor):
    try:
        return validar_coin(valor)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def main():
    """CLI entry point for backtest"""
    parser = argparse.ArgumentParser(
        description='ETH Analyzer Backtest (margen aislado, dinamico)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python3 backtest.py                    # Backtest all TFs
  python3 backtest.py --tf 5m            # Backtest 5m only
  python3 backtest.py --tf 15m --hours 3 # Backtest 15m, look 3 hours ahead
  python3 backtest.py --compare          # Compare all TFs side by side
  python3 backtest.py --sin-horizonte    # Sin cierre por tiempo: solo stop/tp/reversal
        '''
    )
    
    parser.add_argument('--tf', type=str, choices=['5m', '15m', '1h', '4h'],
                       help='Specific timeframe to backtest')
    parser.add_argument('--hours', type=_entero_positivo, default=1,
                       help='Look ahead hours, > 0 (default: 1). Ignorado si se pasa --sin-horizonte')
    parser.add_argument('--sin-horizonte', action='store_true',
                       help='Desactiva el cierre forzado por horizonte: solo sale por stop-loss, '
                            'take-profit o reversal; si nada de eso ocurre, la señal queda PENDING')
    parser.add_argument('--compare', action='store_true',
                       help='Compare all timeframes')
    parser.add_argument('--capital', type=_flotante_positivo, default=25,
                       help='Margen inicial por operacion, USDT, > 0 (default: 25)')
    parser.add_argument('--margin-pct', type=_fraccion_margen, default=0.10,
                       help='Porcentaje de margen aislado / inverso del apalancamiento, en (0,1] (default: 0.10 = 10x)')
    parser.add_argument('--coin', type=_coin_type, default='ETH',
                       help='Moneda (default: ETH). El log de señales sigue siendo eth_setup_log_<tf>.csv')
    parser.add_argument('--mercado', type=str, default='futuros', choices=['futuros', 'spot'],
                       help='Mercado (default: futuros)')
    parser.add_argument('--exec-tf', type=str, default='5m', choices=sorted(TF_MINUTOS.keys()),
                       help='Vela usada para el precio de entrada/salida (default: 5m, por precision)')
    parser.add_argument('--stop-pct', type=_fraccion_margen, default=0.03,
                       help='Stop-loss como fraccion del precio de entrada, en (0,1] (default: 0.03 = 3%%)')
    parser.add_argument('--fuente-log', type=str, default='auto', choices=['auto','vivo','replay'],
                        help='Que log de senales backtestear: vivo (eth_setup_log), '
                             'replay (eth_setup_hist_log) o auto = el mas reciente (default)')
    parser.add_argument('--take-profit-pct', type=_fraccion_margen, default=0.10,
                       help='Take-profit como fraccion del precio de entrada, en (0,1] (default: 0.10 = 10%%)')
    
    args = parser.parse_args()
    
    hours_ahead = None if args.sin_horizonte else args.hours

    ok_todos = True
    try:
        tfs = ['5m', '15m', '1h'] if not args.tf else [args.tf]
        if not args.tf:
            print("\n" + "=" * 70)
            print(f"BACKTEST DINÁMICO - {'COMPARAR TFs' if args.compare else 'TODOS LOS TFs'} "
                  f"(Margen: {args.capital} USDT, {1/args.margin_pct:.0f}x"
                  f"{', SIN HORIZONTE' if args.sin_horizonte else ''})")
            print("=" * 70 + "\n")

        for tf in tfs:
            ok_todos &= backtest_tf_dynamic(tf, hours_ahead, args.capital, args.margin_pct,
                                             args.coin, args.mercado, args.exec_tf,
                                             args.stop_pct, args.take_profit_pct,
                                             args.fuente_log)

        if not ok_todos:
            print("[AVISO] Al menos un TF terminó en RUINA o sin datos (ver ESTADO arriba)")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"[ERROR] Error: {e}")
        sys.exit(1)

    if not ok_todos:
        sys.exit(2)

if __name__ == "__main__":
    main()
