# Resultados de backtesting — sesión 2026-09-08

Resumen de lo probado con `backtest_ema.py`, `backtest_bollinger.py` y `backtest_flujo.py` (futuros, apalancamiento 10x, riesgo 5% del capital disponible por trade, SL/TP por ATR, stop-and-reverse).

## Bug corregido

`cerrar_posicion` cobraba la fee de entrada dos veces: una al abrir la posición (descontada de `capital_disponible`) y otra dentro de `neto` al cerrarla. Esto sesgaba todos los resultados hacia abajo, sobre todo en configs con muchos trades (el compounding de posiciones sub-dimensionadas se acumula). Ya está corregido en `backtest_ema.py:cerrar_posicion` — `backtest_bollinger.py` y `backtest_flujo.py` lo heredan al importar de ahí. Todos los resultados de este documento son **post-fix**.

## EMA crossover (`backtest_ema.py`, `--start 2020-01-01`)

- **BTC**: pierde en absolutamente todas las combinaciones probadas (15m, 1h, 4h; EMA 9/34, 9/42, 9/26).
- **ETH**: igual que BTC, pierde en todo.
- **SOL 1h**: gana en 7 de 8 combinaciones (EMA 9/34 y 9/42 × 4 SL/TP). Mejor caso: EMA9/34 SL2x/TP2x, +135% neto, PF 1.055, 1717 trades.
- **SOL 4h**: mixto, solo 1 de 8 combos positivo.
- **Pendiente**: validar SOL 1h EMA9/34 SL2x/TP2x con `--start 2022-01-01` para descartar que el resultado dependa de la corrida inicial de SOL en 2020-2021 (no se llegó a correr esta sesión).

## Bollinger breakout (`backtest_bollinger.py`, `--start 2020-01-01`)

- **15m**: pierde en las 36 combinaciones (3 monedas × 3 configs de banda × 4 SL/TP) sin excepción.
- **1h y 4h con BB(20, 2.5σ)**: positivo o casi neutro en 11 de 12 combinaciones BTC/SOL/ETH × SL-TP. El más consistente de toda la sesión entre las tres monedas.
  - BTC 4h: PF 1.09-1.24 en las 4 combinaciones de SL/TP.
  - ETH 1h: PF 1.07-1.10, hasta +198% en SL2x/TP2x.
  - SOL: acompaña la dirección positiva en la mayoría, aunque más débil que BTC/ETH.
- BB(20, 2σ) y BB(14, 2σ) (bandas más angostas, más señales) pierden casi siempre — mismo patrón que en EMA: menos señales pero más selectivas gana.
- **Ojo**: SOL 4h BB(14,2) SL2x/TP3x dio +408.8% (PF 1.20, n=565) — outlier sospechoso, probablemente dominado por 1-2 trades que agarraron un movimiento gigante. No tomar como señal real sin revisar la distribución de esos trades.

## Cruce EMA vs Bollinger

No hay ninguna combinación coin+timeframe donde ambos indicadores ganen a la vez — parecen capturar regímenes distintos (Bollinger: BTC/ETH en 1h-4h; EMA: SOL en 1h). Combinarlos como filtro mutuo no tiene sustento en los datos actuales: donde uno gana, el otro no aporta señal real, así que un filtro AND solo reduciría trades sin mejorar calidad.

## Flujo / CVD (`backtest_flujo.py`)

- Datos: `libro/datos/flujo/flujo_ETH_futuros_*.csv`, solo ETH, 14 días (25 ago - 7 sep 2026).
- Cruce de EMA sobre CVD: pierde en 1m (PF 0.14-0.23) y 1h (muestra insuficiente, 8-10 trades, igual pierde). En 15m prácticamente plano, un solo combo apenas sobre breakeven (+$0.05).
- Conclusión: sin edge detectable todavía. Puede ser falta de historial (14 días) o que el cruce de EMA no sea la forma correcta de usar CVD (se usa más típicamente para divergencia con precio que como serie a la que aplicarle un cruce de tendencia).
- Datos de `libro/datos/libro_ETH_futuros_*.csv` (imbalance, open interest, long/short ratio) sin explorar todavía.

## Próximos pasos sugeridos

1. Validar SOL 1h EMA9/34 SL2x/TP2x y BTC/ETH BB(20,2.5σ) en 1h/4h con `--start 2022-01-01` (out-of-sample respecto al período ya escaneado).
2. Revisar la distribución de trades del outlier SOL 4h BB(14,2) antes de descartarlo o confiarlo.
3. Seguir acumulando `flujo_ETH_futuros` semana a semana; retomar CVD cuando haya más historial o probar divergencia precio/CVD en vez de cruce de EMA.
4. Explorar `libro_ETH_futuros` (imbalance, OI, long/short ratio) — quedó pendiente sin decidir qué campo probar primero.
