# Resultados de backtesting

## Sesión 2026-09-08

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

## Próximos pasos sugeridos (sesión 2026-09-08)

1. ~~Validar SOL 1h EMA9/34 SL2x/TP2x y BTC/ETH BB(20,2.5σ) en 1h/4h con `--start 2022-01-01`~~ — hecho, ver sesión 2026-09-15.
2. Revisar la distribución de trades del outlier SOL 4h BB(14,2) antes de descartarlo o confiarlo — parcialmente resuelto sin mirar trade por trade, ver sesión 2026-09-15.
3. Seguir acumulando `flujo_ETH_futuros` semana a semana; retomar CVD cuando haya más historial o probar divergencia precio/CVD en vez de cruce de EMA. Sigue pendiente.
4. ~~Explorar `libro_ETH_futuros` (imbalance, OI, long/short ratio)~~ — hecho, ver sesión 2026-09-15.

## Sesión 2026-09-15

### Validación out-of-sample (`--start 2022-01-01`)

Re-corrida de los candidatos de la sesión anterior descartando el tramo 2020-2021 (`backtest_oos_2022.ps1`, log en `backtest_oos_2022.log`):

- **SOL 1h EMA9/34 — descartada.** Pierde en las 4 combinaciones SL/TP (PF 0.95–0.99). El +135% de la sesión anterior dependía de la corrida inicial de SOL 2020-2021, tal como se sospechaba.
- **BTC 4h BB(20,2.5σ) — se sostiene.** PF 1.12–1.23 en las 4 combinaciones (vs. 1.09–1.24 en el período completo). Es la única configuración de toda la screening que se banca el out-of-sample sin perder solidez.
- **BTC 1h BB(20,2.5σ) — positivo pero débil.** PF 1.01–1.05.
- **ETH 1h BB(20,2.5σ) — la edge casi desaparece.** PF 1.01–1.03 fuera de muestra (vs. 1.07–1.10, hasta +198%, en el período completo).
- **ETH 4h BB(20,2.5σ) — se rompe.** 3 de 4 combos pierden plata (PF 0.94–0.99), solo SL1.5x/TP3x zafa con PF 1.06.

### Filtro EMA diaria: qué período y qué lado por moneda

Sweep completo (`backtest_bollinger_ema_dia_periodos.ps1` → `periodos_parsed.csv`, `backtest_bollinger_ema_dia_asimetrico.ps1` → `asimetrico_parsed.csv`), período completo 2020-2026, BB20 y BB14, 3 monedas:

- **BTC**: el filtro `short` (bloquea SHORT si precio > EMA diaria) es sistemáticamente mejor que `long`, y mejora con periodos más largos — PF promedio 1.18 con EMA100 short, vs. 1.11 con EMA20 long. En la comparación simétrico-vs-asimétrico, el **simétrico 50/50 ganó** (PF prom. 1.275) por encima de las variantes asimétricas 20/50 (1.226) y 20/100 (1.243) — la hipótesis de que un período corto para LONG y largo para SHORT mejora el resultado **no se confirmó** en BTC.
- **ETH**: el filtro `long` no aporta nada — pierde en promedio en los 4 periodos probados (PF 0.96–0.99). `short` con EMA20 es el único que promedia positivo (PF 1.04); con periodos más largos también se apaga. Simétrico 50/50 (PF 1.093) y asimétrico 20/50 (PF 1.105) quedan prácticamente empatados.
- **SOL**: los promedios por período/variante son altos (PF 1.13–1.39) pero no son confiables — ver el punto siguiente.

### SOL + Bollinger(14,2): el outlier no era un caso aislado

El pendiente de revisar la distribución de trades del outlier SOL 4h BB(14,2) SL2x/TP3x (+408.8% en la sesión anterior) se puede cerrar sin mirar trade por trade: **en los dos sweeps de esta sesión, cualquier combinación de SOL + BB(14,2) da retornos desproporcionados** frente a su profit factor — ejemplos de `asimetrico_parsed.csv`:

| combo | trades | equity final | PF |
|---|---|---|---|
| SOL BB14 SL2/TP3 EMA20/50 | 386 | $1071.77 (+972%) | 1.39 |
| SOL BB14 SL2/TP3 EMA20/100 | 380 | $1077.70 (+978%) | 1.39 |
| SOL BB14 SL2/TP3 EMA50/50 | 345 | $464.45 (+364%) | 1.28 |

Un PF de 1.3–1.4 no explica un retorno de +900% en ~380 trades con riesgo del 5% del capital por operación — es la firma clásica de 1 o 2 trades gigantes dominando el resultado. **Conclusión: descartar SOL+BB(14,2) como candidato en cualquier configuración**, no solo el combo SL2x/TP3x puntual. BB(20,*) en SOL no muestra este patrón (equity y PF quedan en rango normal).

### Filtro de niveles de soporte/resistencia — mejora consistente en OOS

`backtest_niveles_3coins.ps1` (log en `backtest_niveles_3coins.log`) prueba BB(20,2.5σ) + EMA-diaria(20/50, filtro ambos) + niveles(ambos), 4h, OOS 2022-2026. Comparado contra el Bollinger puro OOS de la sección anterior:

| | BTC 4h sin filtros | BTC 4h + EMA-diaria + niveles | ETH 4h sin filtros | ETH 4h + EMA-diaria + niveles |
|---|---|---|---|---|
| SL1.5x/TP2x | PF 1.12 | **PF 1.14** | PF 0.99 (pierde) | **PF 1.05** |
| SL1.5x/TP3x | PF 1.21 | **PF 1.27** | PF 1.06 | **PF 1.19** |
| SL2x/TP2x | PF 1.18 | **PF 1.21** | PF 0.94 (pierde) | **PF 1.00** (break-even) |
| SL2x/TP3x | PF 1.23 | **PF 1.28** | PF 0.99 (pierde) | **PF 1.11** |

Los filtros combinados mejoran el PF en las 4 combinaciones para ambas monedas, y en ETH **rescatan 3 de 4 configuraciones que perdían plata sin filtro** — con ~30% menos trades (mejor selectividad).

SOL se probó como moneda "de control" (altcoin más volátil, para ver si el filtro generaliza más allá de BTC/ETH). Con un baseline limpio (BB20/2.5 sin ningún filtro, mismo rango OOS, en `sol_baseline_oos.log`), el patrón se repite:

| SOL 4h | sin filtros | + EMA-diaria + niveles |
|---|---|---|
| SL1.5x/TP2x | PF 1.02 | **PF 1.10** |
| SL1.5x/TP3x | PF 1.23 | **PF 1.35** |
| SL2x/TP2x | PF 1.10 | **PF 1.18** |
| SL2x/TP3x | PF 1.27 | **PF 1.39** |

Mejora en las 4 combinaciones, igual que en BTC y ETH. **El filtro de niveles + EMA-diaria generaliza a las 3 monedas probadas, no es un efecto puntual de BTC/ETH.**

*Reconfirmado el mismo día con el histórico de Binance actualizado al 14-sep (antes llegaba al 7-sep): variación ≤0.01 en PF para BTC/ETH; SOL quedó bit a bit idéntico porque la semana extra no generó ningún trade nuevo bajo este filtro. La validación OOS 2022 de la sección anterior también se reconfirmó igual de estable. No cambia ninguna conclusión.*

### Exploración de `libro_ETH_futuros` (order book, OI, funding, long/short ratio)

Con ~21 días de historial (25 ago – 15 sep, 96 snapshots/día) y usando velas reales de `velas/ETH/bitget_ETH_*.csv` para los retornos futuros:

- **Imbalance (fino y amplio)**: correlación con retorno futuro a 15m/1h prácticamente nula (|r| < 0.03, sin patrón monótono). No usable como señal aislada con este historial.
- **Divergencia precio vs. Open Interest**: la más prometedora — precio↑+OI↑ (trend con dinero nuevo) da retorno futuro positivo, precio↑+OI↓ (rally por cierre de shorts) da retorno futuro negativo, consistente en 15m y 1h. Pero el t-test de la diferencia da t≈1.2–1.7, **por debajo de significancia convencional (1.96)** — direccionalmente sensato, no confirmado.
- **Funding rate**: relación inversa con retorno futuro, pero 40% de las muestras está pegado al cap de 0.0100% de Bitget — poca resolución real.
- **Long/short ratio**: relación positiva con retorno futuro, contraria al uso "contrarian" típico — probablemente es un indicador rezagado que sigue a la tendencia, no una señal de posicionamiento genuina.
- El histórico de OI **no se puede backfillear** (ni en Bitget ni en ningún exchange vía API, retienen ~30 días) — solo se puede seguir acumulando hacia adelante con `libro.py`, que ya corre.

**No se justifica todavía construir un filtro con esto.** Revisar de nuevo con 60-90 días de historial.

## Sesión 2026-09-16

### Niveles + EMA-diaria en 1h — generaliza, y con más fuerza que en 4h

Mismo combo (BB20/2.5, EMA-diaria 20/50 ambos, niveles ambos, OOS 2022-2026) corrido en 1h para las 3 monedas (`backtest_niveles_3coins_1h.ps1`, log en `backtest_niveles_3coins_1h.log`), contra un baseline limpio sin filtros (BTC/ETH en `backtest_oos_2022.log`, SOL en `sol_baseline_1h_oos.log`):

| | BTC 1h | ETH 1h | SOL 1h |
|---|---|---|---|
| PF sin filtro (rango 4 combos SL/TP) | 1.01–1.05 | 1.02–1.03 | 1.01–1.02 |
| PF + EMA-diaria + niveles | **1.12–1.19** | **1.11–1.15** | **1.07–1.11** |

Mejora en las 12 combinaciones (3 monedas × 4 SL/TP), y con un salto mayor que en 4h: en 1h el Bollinger sin filtro apenas está sobre breakeven (varios combos en 1.01, a un paso de dar negativo con otro supuesto de costos), y el filtro lo lleva a un rango consistentemente rentable. **El filtro de niveles + EMA-diaria no es específico de 4h — en 1h aporta todavía más.**

### Auditoría de flags y corrección: 20/50 vs 50/50 en EMA-diaria

Al revisar qué flags se usaron en cada script (ver también el punto siguiente sobre `--start`), se encontró que `backtest_niveles_3coins.ps1`/`_1h.ps1` usaban `--ema-dia 20 --ema-dia-short 50` (asimétrico) pese a que el sweep de períodos de la sesión anterior había concluido que el **simétrico 50/50 le ganaba al asimétrico para BTC**. Se cableó `--ema-dia-filtro`/`--niveles-filtro` como default `"ambos"` en `backtest_bollinger.py` (antes `"none"`) y se probó recorrer niveles+EMA-diaria con 50/50 en vez de 20/50, en 4h y 1h, para las 3 monedas.

Resultado — **no generaliza, fue un error corregir el default a 50/50**:

| | 4h: 50/50 vs 20/50 | 1h: 50/50 vs 20/50 |
|---|---|---|
| BTC | 50/50 gana (+0.07 a +0.15 PF) | 50/50 **pierde** (−0.05 a −0.07 PF) |
| ETH | ≈ empate (±0.02) | 50/50 **pierde** (−0.04 a −0.05 PF) |
| SOL | 50/50 pierde (−0.02 a −0.04) | 50/50 pierde (−0.005 a −0.02, más leve) |

50/50 solo gana en BTC 4h — en las otras 20 combinaciones (de 24 totales) el 20/50 asimétrico es igual o mejor, y en 1h pierde limpio en las 12 combinaciones sin excepción. El hallazgo original (50/50 > 20/50) era válido pero **específico de BTC en 4h**, no generalizable; haberlo cableado como default global fue apresurado. Se revirtió: `backtest_niveles_3coins.ps1`/`_1h.ps1` vuelven a pedir `--ema-dia 20 --ema-dia-short 50` explícito. El default neutro del código (símetrico, sin asumir un lado) queda en `backtest_bollinger.py` sin tocar; los scripts de niveles declaran su propia elección empírica en vez de heredar un default no probado para su caso. Logs de la corrida descartada (50/50) guardados como `backtest_niveles_3coins_ema50-50.log` / `_1h_ema50-50.log` para no perder el resultado negativo.

### Limpieza y otros ajustes de flags

- **`--start` restaurado a `2020-01-01`** en `backtest_ema.ps1` y `backtest_bollinger.ps1`: se había corrido a `2022-01-01` en el commit del 9-sep (antes de esta sesión), lo que significa que desde esa fecha esos dos scripts ya no reproducían el screening full-period 2020-2026 documentado en la sesión 2026-09-08 — corrían un OOS 2022-2026 sin decirlo. Corregido, con comentario en el script para que no vuelva a driftear en silencio.
- **`BB_STD` default `2.0` → `2.5`** en `backtest_bollinger.py`: refleja lo que ya se venía validando en todos los sweeps (BB(20,2.5σ) le gana a BB(20,2.0σ) casi siempre).
- No se encontró basura para limpiar (un `.pkl` huérfano que se había detectado ya no existía).
- Se borraron los 7 scripts de screening ya resueltos y sin resultado pendiente (`backtest_ema.ps1`, `backtest_bollinger.ps1`, `backtest_flujo.ps1`, `backtest_bollinger_ema_dia*.ps1`, `backtest_oos_2022.ps1`) — quedan solo `backtest_niveles_3coins.ps1`/`_1h.ps1`, con `$ema_dia_long`/`$ema_dia_short` como variables al tope en vez de hardcodeados, para poder ajustar el período sin tocar la línea de comando. Commit `f924027`. Los 7 borrados siguen en el historial de git si hace falta reproducirlos.

### Paper trading en vivo: `vivo_bollinger.py`

Primer script que aplica la configuración validada (BB20/2.5 + EMA-diaria 20/50 + niveles ambos, 4h, SL2x/TP3x) sobre los feeds reales de Bitget en vez de histórico — sin órdenes reales, solo simula posiciones y avisa por Telegram (igual patrón que `simulator.py`, pero con esta estrategia en vez de EMA9/18).

- Reutiliza `calcular_bollinger`/`_nivel_bloquea` de `backtest_bollinger.py` y `calcular_atr` de `backtest_ema.py` — la misma lógica que ya está validada, no una reimplementación.
- Lee `velas/<COIN>/bitget_<COIN>_4h_futuros.csv` (señal) y `_1d_futuros.csv` (EMA-diaria) + `niveles/json/nivel_<COIN>_4h_futuros_k5_toques3.json` (ya calculado por el Vigilante de `niveles.py`, no lo recalcula).
- BTC y SOL no tenían este feed corriendo (solo ETH) — se bootstrapeó historial 1h/4h/1d (`descargar_hist_bit_futuros.py`) y el primer snapshot de niveles (`params_btc_*.json`/`params_sol_*.json`, mismo k=5/tolerancia=0.15/toques=3 que ETH).
- Probado con `--una-vez` dos veces: sin señal en la última vela de 4h (esperable, un breakout no es frecuente), la segunda corrida no reprocesó la misma vela (idempotencia OK vía `ultima_vela_procesada` en `posiciones_vivo.json`). Telegram confirmado configurado.
- **Pendiente de decidir, no de código: dónde corre 24/7.** `libro`/`flujo`/`niveles` corren como daemons en el NAS; este script todavía no — por ahora se usa manual (`python vivo_bollinger.py --una-vez` cuando se quiera chequear), sin comprometerse a un despliegue permanente todavía.

### Próximos pasos

1. Decidir despliegue de `vivo_bollinger.py` (NAS en loop, tarea programada en OFICINA, o seguir manual) — hoy corre a demanda con `--una-vez`.
2. Descartar definitivamente SOL+BB(14,2) de cualquier sweep futuro — no vuelve a dar señales confiables. BB20 en SOL sí es válido.
3. Seguir acumulando `flujo_ETH_futuros` y `libro_ETH_futuros`; repetir el análisis de OI+precio con 60-90 días antes de decidir si se cablea como filtro.
4. Si se quiere seguir optimizando el período de EMA-diaria por separado por moneda/timeframe (en vez de un único 20/50 para las 3), haría falta un sweep dedicado — no se hizo todavía, hoy 20/50 es "el que sostiene mejor en general", no necesariamente el óptimo por moneda.
