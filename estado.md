# Estado — sesión 2026-08-10

Prueba en curso: analizar cuánto puede escalar una cantidad ficticia pequeña
con `monitor.py` en papel (sin dinero real), corriendo en paralelo a
`monitor_niveles.py`.

## Implementado

### Filtros de validación externa (`estrategia/filtros.py`)
- **Eliminados**: `FiltroBTC`, `FiltroSoporte`, `FiltroBollinger` (y
  `FiltroConfluencia`, ya inactivo antes) — su propio backtest
  (`backtest_filtros_combinados.py`, 9 años BTC+ETH) los encontró
  empeorando el resultado sin excepción al promoverlos a veto; como sombra
  ya no aportaban nada nuevo. Se quitó también `_velas_btc` (solo lo
  usaba `FiltroBTC`) de `mercado/lectura.py`, `posicion/posicion.py`,
  `estrategia/contexto.py` y `monitor.py`.
- **`FiltroOpenInterest` reactivado + `FiltroCVD` nuevo** (sombra, no
  vetan todavía): piden `open_interest`/`trades` con la MISMA API que ya
  usa `monitor.py` (`datos.open_interest`, `datos.trades`, igual patrón
  que `_funding_pct`), acumulando la serie en memoria de ESTE proceso —
  **no dependen de `grabador_libro.py`** (se probó leyendo su CSV y se
  revirtió a propósito: ese proceso corre aparte y su continuidad en
  producción no está decidida, ver `PENDIENTES.md`). Un reinicio de
  `monitor.py` vacía la serie — mismo trade-off que ya acepta el resto de
  `leer()`.
- Filtros que quedan como VETO real (sin cambios): `volatilidad`
  (ATR% ≥ 0.12) y `volumen`/RVOL (solo en `aceleracion_*`).

### Estancamiento (`posicion/posicion.py` / `monitor.py`)
- Pasa de solo-registro a **actuar**: si una posición lleva ≥30 min sin
  superar su máximo favorable, con la tendencia en contra, y el precio
  sigue del lado bueno de la entrada, mueve el stop a breakeven. Es el
  único mecanismo de protección intermedia que existe con la salida RR
  fija (antes no había ninguno).

### Varias posiciones concurrentes (`monitor.py` / `posicion/posicion.py`)
- `posiciones[coin]` pasa de `PosicionSim|None` a **lista**: hasta
  `cfg["posiciones_max"]` (default 3) posiciones abiertas a la vez por
  moneda/rama, cada una gestionada de forma independiente
  (`_gestionar_posicion()`, extraída para no duplicar lógica).
- **Guarda anti-hedge**: una señal en dirección opuesta a una posición ya
  abierta no apila un hedge sobre el mismo activo — no abre hasta que
  esa(s) se cierren por su propio stop/objetivo.
- CSV: una fila por posición gestionada esta vuelta + una por el intento
  de apertura/orden pendiente (antes: 1 fila fija por vuelta). Columnas
  `filtro_btc/soporte/confluencia/bollinger_*` quitadas del esquema,
  `filtro_cvd_veredicto/valor` añadidas.
- Verificado con datos simulados (sin red real): acumula hasta 3
  posiciones, respeta el tope, cierra cada una independiente por su
  propio stop, y bloquea/deja pasar la guarda anti-hedge correctamente
  según haya o no posiciones contrarias vivas.

### `telegram_control.py`
- **Menú de ajuste recortado** a los 13 parámetros que de verdad deciden
  entrada/riesgo/veto/salida/tamaño (`PARAMS_TELEGRAM`), cada uno con su
  explicación en lenguaje llano: `impulso_lookback`, `impulso_min_atr`,
  `aceleracion_ventana`, `aceleracion_min_atr`, `aceleracion_mult`,
  `stop_atr`, `exit_rr`, `regimen_sma`, `regimen_tf`, `fraccion_entrada`,
  `leverage`, `posiciones_max`, `ventana`. El resto de
  `monitor.PARAMS_AJUSTABLES` sigue existiendo (CLI, comandos manuales)
  pero ya no se ofrece ni se acepta desde Telegram.
- **`resumen` cambia de significado**: antes era un log de aperturas/
  cierres de HOY; ahora muestra las **operaciones ABIERTAS ahora mismo**
  (lado, entrada, stop, objetivo, pnl neto), reconstruidas de la fila más
  reciente de cada moneda. Filtrable de forma independiente por moneda
  (`resumen/ETH`) o por franja (`resumen/15m`), no solo ambas juntas.
- **Botón/comando Reset**: devuelve los 13 parámetros de `PARAMS_TELEGRAM`
  a su valor de arranque de una vez, derivado dinámicamente de
  `monitor._parse_args()` (no hardcodeado — no se desincroniza si cambian
  los defaults).
- Fix de colisión de nombres de archivo en `comandos/*.json` (resolución
  de milisegundo → nanosegundos + contador), encontrado al probar el Reset
  (13 comandos en ráfaga perdían 5 por pisarse entre sí).

### Pendiente sin cerrar de esta sesión
- **Visibilidad de órdenes PENDIENTES en `resumen`**: el nuevo `cmd_resumen`
  solo mira `posicion_lado`, nunca `orden_pendiente_lado` — una orden
  límite puesta pero aún sin llenar hoy es invisible en el resumen. Detectado
  pero no arreglado todavía (ver `PENDIENTES.md`).

## Qué vamos a monitorear con esta prueba

- **Evolución del capital** por moneda/franja (`capital_actual` en el CSV,
  o `cartera` por Telegram) — el objetivo final de la prueba.
- **Uso real del cupo de 3 posiciones**: ¿el sistema llega a abrir 2-3 a
  la vez con frecuencia, o casi siempre opera con 1? Si nunca usa el
  cupo, `posiciones_max` no está aportando nada en la práctica.
- **Cuántas veces actúa la guarda anti-hedge** (`apertura_bloqueada` con
  el motivo "no se apila un hedge") — si bloquea señales que habrían sido
  buenas, es una señal de que el cupo compite con la dirección contraria
  más de lo esperado.
- **Estancamiento**: cuántas veces dispara
  (`filtro_estancamiento_veredicto`) y si el breakeven que aplica mejora
  el resultado real o solo adelanta cierres que de todas formas iban a
  ganar (comparar `motivo_cierre` cuando `posicion_stop_origen` viene de
  "breakeven (estancamiento)" contra el resto).
- **Winrate real de la salida RR 1:3** contra el que dio el backtest de 1
  año — con posiciones concurrentes y anti-hedge el patrón de entradas
  cambia, vale la pena confirmar que el edge se sostiene.
- **Los filtros sombra** (`filtro_funding_*`, `filtro_open_interest_*`,
  `filtro_cvd_*`) — acumular aperturas suficientes para poder cruzarlos
  después contra el PnL real y decidir si alguno merece promoverse a veto
  (mismo criterio que ya se usó para RVOL/volatilidad).
