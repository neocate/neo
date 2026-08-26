# P&L Calculation - ETH Analyzer Backtest

## Cómo se calcula el P&L de forma realista

---

## **1. Flujo básico (sin comisiones)**

```
Entry:     Compro en 2463 USDT (LONG)
Exit:      Vendo en 2468 USDT (después de 1h)
Cambio:    +5 USDT por contrato
P&L Bruto: +5 USDT (no es realista)
```

---

## **2. Flujo REALISTA (con comisiones)**

Las comisiones y slippage son **costos reales** que reducen las ganancias:

```
Entry Price:      2463 USDT
Exit Price:       2468 USDT
Comisiones Taker: 0.06% (entrada) + 0.06% (salida) = 0.12%
Slippage:         0.03% (promedio de mercado)
Total Fees:       0.15%

Cálculo:
  1. Ganancia bruta:    (2468 - 2463) × 1 = 5 USDT
  2. Comisiones taker:  2463 × 0.06% = 1.48 USDT
  3. Comisiones salida: 2468 × 0.06% = 1.48 USDT
  4. Slippage:          2463 × 0.03% = 0.74 USDT
  5. Total Fees:        1.48 + 1.48 + 0.74 = 3.70 USDT
  
  P&L NETO:  5 - 3.70 = +1.30 USDT (+0.05%)
  
Sin comisiones:   +5 USDT (+0.20%)
Con comisiones:   +1.30 USDT (+0.05%)   ← REALISTA
```

---

## **3. LONG vs SHORT**

### **LONG** (compra en entry, vende en exit)
```python
P&L = (Exit - Entry) - Fees
Win = P&L > 0
```

**Ejemplo:**
- Entry: 2463, Exit: 2468
- P&L neto: +1.30 USDT ✓

---

### **SHORT** (vende en entry, compra en exit)
```python
P&L = (Entry - Exit) - Fees
Win = P&L > 0
```

**Ejemplo:**
- Entry: 2463, Exit: 2458
- Ganancia bruta: (2463 - 2458) = 5 USDT
- Comisiones: 3.70 USDT
- P&L neto: +1.30 USDT ✓

---

## **4. Fuente de comisiones (contrato.py)**

El analyzer integra automáticamente comisiones reales:

```python
from mercado.contrato import obtener_contrato

contrato = obtener_contrato('ETH/USDT:USDT')
# Returns:
# {
#   'comision_maker': 0.0002,  # 0.02%
#   'comision_taker': 0.0006,  # 0.06%
#   'leverage_maximo': 125,
#   'funding_rate': 0.0015,
#   ...
# }
```

**Si `contrato.py` no está disponible**, usa defaults Bitget:
- Taker: 0.06%
- Maker: 0.02%
- Slippage: 0.03%

---

## **5. Qué datos se guardan**

El backtest registra en CSV:

```
timestamp | signal | entry_price | exit_price | price_change_pct | pnl_gross | pnl_neto | pnl_pct | comisiones | result | win
2026-08-26T10:30|LONG|2463.00|2468.00|+0.20|+5.00|+1.30|+0.05|3.70|CORRECT|True
2026-08-26T10:31|SHORT|2462.00|2457.00|+0.20|+5.00|+1.30|+0.05|3.70|CORRECT|True
```

**Columnas:**
- `pnl_gross`: Ganancia sin comisiones (referencia)
- `pnl_neto`: Ganancia real después de comisiones ← **IMPORTANTE**
- `pnl_pct`: % neto (mejor métrica para comparar)
- `comisiones`: Costo total de comisiones y slippage
- `result`: CORRECT (P&L > 0) | WRONG (P&L < -5) | PARTIAL

---

## **6. Reportes de backtest**

### **Antes (sin comisiones):**
```
Win Rate: 75%
Avg P&L: +2.50%
```

### **Ahora (con comisiones reales):**
```
Win Rate: 75%
Avg P&L: +0.85% (realista)

Comisiones:
  Taker:    0.0600%
  Slippage: 0.0300%
  Total:    0.1500% por operación

Total Trades:          40
Win Rate:              75.0%

P&L Neto (después comisiones):
  Avg per trade:       +1.30 USDT (+0.05%)
  Total:               +52.00 USDT (+2.00%)
  Best Trade:          +8.50 USDT
  Worst Trade:         -5.20 USDT

Comisiones y Costos:
  Total Fees:          148.00 USDT
  Avg Fee per trade:    3.70 USDT
```

---

## **7. ¿Por qué es importante P&L realista?**

### **Problema sin comisiones:**
```
Analyzer dice: "Win Rate 80%, Avg P&L +3%"
Realidad:      Pierdes dinero después de comisiones
```

### **Solución con comisiones:**
```
Analyzer dice: "Win Rate 80%, Avg P&L +0.8%"
Realidad:      Ganas dinero (después de comisiones)
```

---

## **8. Cómo mejorar Win Rate**

1. **Reducir comisiones:**
   - Usar maker orders (0.02%) en lugar de taker (0.06%)
   - Negociar comisiones con Bitget si volumen es alto

2. **Reducir slippage:**
   - Usar órdenes de límite, no órdenes de mercado
   - Dividir órdenes grandes en múltiples partes

3. **Mejor timing:**
   - Buscar movimientos >10 pts, no 5 pts
   - Aumentar P&L mínimo para validar "CORRECT"

---

## **Fórmula completa de P&L**

```
Para LONG:
  PnL_gross = (Exit - Entry) × Size
  Fees_total = Entry × Size × (Taker_entry + Taker_exit + Slippage)
  PnL_neto = PnL_gross - Fees_total
  PnL_pct = (PnL_neto / (Entry × Size)) × 100

Para SHORT:
  PnL_gross = (Entry - Exit) × Size
  Fees_total = Entry × Size × (Taker_entry + Taker_exit + Slippage)
  PnL_neto = PnL_gross - Fees_total
  PnL_pct = (PnL_neto / (Entry × Size)) × 100
```

---

## **Integración con mercado/contrato.py**

El backtest **automáticamente:**

1. Lee comisiones reales de Bitget API (via `contrato.py`)
2. Calcula P&L neto con esas comisiones
3. Reporta estadísticas **realistas**, no teóricas

Si `contrato.py` falla (sin credenciales API), usa **defaults probados** de Bitget.

---

## **Validación de resultados**

```bash
# Ver último P&L calculado
tail -1 datos/eth_backtest_results.csv | cut -d',' -f1-9

# Ver promedio de P&L
python3 -c "
import pandas as pd
df = pd.read_csv('datos/eth_backtest_results.csv')
print(f'Avg P&L neto: {df[\"pnl_neto\"].mean():.2f} USDT')
print(f'Total comisiones: {df[\"comisiones\"].sum():.2f} USDT')
"
```
