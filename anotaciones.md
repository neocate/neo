# Anotaciones

- 2026-08-08: `monitor_niveles.py` - `_asegurar_historico()` disparaba una
  descarga completa (`descargar()`, que REESCRIBE el CSV entero) por cada TF
  que no llegara a `--desde-dias` de profundidad. Si se lanzaban DOS
  `monitor_niveles.py` a la vez sobre la misma moneda (ej. uno en 15m y otro
  en 1h, ambos asegurando el mismo set `TIMEFRAMES_NIVELES`), los dos podian
  ver el fichero como "corto" al mismo tiempo y disparar la misma descarga
  completa en paralelo - en el mejor caso, trabajo duplicado; en el peor,
  dos procesos escribiendo el mismo `historico_<COIN>_<TF>_bitget.csv` a la
  vez (dos `open(..., 'w')` solapados). Arreglado con `_con_lock_historico()`:
  lock de fichero por creacion atomica (`O_CREAT|O_EXCL`) junto al historico
  (`<ruta>.lock`) - el proceso que lo consigue baja de verdad; el que lo
  pierde espera a que se libere y solo hace `actualizar()` (rapido, ya no
  hace falta repetir la descarga completa). Probado con dos hilos
  arrancando casi a la vez: solo uno descarga, el otro actualiza. Limitacion
  conocida y aceptada: si un proceso muere a mitad con el lock puesto, queda
  huerfano - no se limpia solo (habria que borrar el `.lock` a mano); no se
  añadio deteccion de lock viejo por no complicar mas de lo que pide el caso
  real.

- 2026-08-07: Repo git reiniciado. El `.git` original tenia varios objetos
  sueltos corruptos a nivel de bytes (`inflate: data stream error` en el
  commit HEAD y al menos otros 4 objetos; `git status`/`git fsck --full`
  llegaban a crashear) - sin remoto configurado, no habia de donde
  recuperar una copia buena. Solo eran 2 commits y el working tree seguia
  intacto (nada se perdio en disco, solo el historial de git), asi que se
  movio el `.git` corrupto a `.git.corrupto-20260807/` (sin borrar, por si
  hiciera falta mirar algo despues) y se hizo `git init` limpio con el
  estado actual como primer commit.

- 2026-08-07: `niveles_soporte.py` - `_fusionar()` ahora fusiona por CLUSTER
  de precio (ancla = precio del PRIMER nivel del cluster, no el ultimo
  agregado) y SIN importar tipo techo/suelo - una zona de rango actua como
  resistencia y soporte alternada. La version anterior solo fusionaba pares
  CONSECUTIVOS del MISMO tipo, lo que dejaba decenas de niveles
  casi-duplicados sin fusionar en zonas densas (ETH 4h/90d con
  tolerancia-atr 0.45 paso de 65 a 39 niveles al arreglarlo).

- 2026-08-07: `niveles_soporte.py` - estado vivo/roto/flip: "roto" exige
  `--confirmacion-velas` (sugerido: 2, no calibrado como si lo estan
  k/tolerancia-atr/toques-min) CIERRES DE VELA consecutivos del otro lado
  del nivel +/- tolerancia - un solo cierre puede ser ruido. "flip" = roto +
  retest posterior desde el otro lado (cambio de rol: techo roto actuando
  de suelo, o viceversa).

- 2026-08-07: `niveles_soporte.py` - filosofia de "zona de indecision"
  (decision del usuario, no mia, tras corregirme): un techo/suelo NO roto
  sigue vigente sin importar de que lado quedo el precio. NO hay que
  filtrar/esconder el que quedo del lado "equivocado" (precio ya lo supero
  o lo perdio sin ruptura confirmada) - eso es informacion real, no ruido a
  limpiar. La correccion correcta es avisar explicitamente
  ("zona de indecision, no de rango operable, esperar a que el precio
  confirme direccion") en vez de ocultar el nivel. Entrar ahi es la
  "zona de facil perdida" - motivo dado por el usuario. Ver
  `_avisos_zona_indecision()`.

- 2026-08-07: `niveles_soporte.py` `--tf-macro`: acota los niveles del TF
  principal al rango [suelo_macro, techo_macro] (techo/suelo VIVOS mas
  cercanos al precio, del TF macro, con los MISMOS k/tolerancia-atr/
  toques-min). Confluencia multi-timeframe: el TF alto marca los topes
  reales del rango, el TF fino da granularidad para entradas/salidas
  dentro de ese rango.

- 2026-08-07: `monitor_niveles.py` creado - vigila en vivo los niveles de
  `niveles_soporte.py` (foto tomada al arrancar via `_analizar()`, NO se
  recalculan vela a vela) y avisa por consola + log CSV
  (`herramientas/libro/avisos_<fecha>_<coin>_<tf>.csv`) cuando el precio
  entra/sale de la zona de tolerancia de un nivel. Version final: LEE
  (tail) el CSV que escribe `grabador_libro.py` (bid/ask/mid/imbalance/cvd)
  en vez de pedir nada a la API por su cuenta - decision del usuario,
  porque `grabador_libro.py` va a correr SIEMPRE en el NAS como proceso
  separado y estable, asi que `monitor_niveles.py` no necesita duplicar
  llamadas (confirmado que igual PUEDE correr desde Windows/PowerShell sin
  problema, porque nunca llama a la API el mismo - solo necesita
  ccxt/dotenv importables para poder importar `grabador_libro.CAMPOS_CSV`,
  no credenciales reales). OJO: un "toque" en vivo (precio entra en
  tolerancia) NO es lo mismo que "roto" en `niveles_soporte.py` (que exige
  cierres de vela consecutivos) - es aviso en tiempo real, no confirmacion
  definitiva de ruptura.

- 2026-08-07: `grabador_libro.py` se lanza en el NAS (DS423) por SSH real,
  NO desde PowerShell/Windows contra el share UNC - el `venv/` del proyecto
  es de Linux (tiene `lib`/`lib64`, sin `Scripts/`), no se puede activar
  desde Windows. Lanzado con nohup + background para que sobreviva al
  cierre de la sesion SSH:
  `nohup python herramientas/grabador_libro.py btc,eth > libro.log 2>&1 &`.
  El shell del DSM es busybox/ash - no tiene `pgrep` (usar `ps | grep
  grabador_libro` o `kill -0 <pid>` para chequear si sigue vivo). Sigue
  pendiente formalizarlo como tarea programada de DSM con guardia `pgrep`
  (ver PENDIENTES.md).

- 2026-08-07: `grabador_libro.py` baja/actualiza velas de Bitget en
  `TIMEFRAMES_VELAS = ["1m", "5m", "15m", "30m", "1h", "4h"]` para cada
  moneda, cada `--velas-cada` segundos (default 3600s), ademas de grabar
  libro/OI/funding/trades/long-short en vivo.

- 2026-08-07: Plan pendiente (NO ejecutado - el usuario freno la revision
  de `git status` con "dejalo como esta"): mover `herramientas/` y
  `mercado/` un nivel arriba, de `neo/` a `Fran/`, para compartirlos entre
  `neo` y otro proyecto (mismo patron que se piensa usar para un modulo de
  telegram). `neo/` quedaria solo con lo especifico de ese proyecto.
  IMPORTANTE antes de tocar nada: `neo/` es un repo git (tiene `.git/`,
  aunque no se detecta como tal desde fuera) - revisar `git status` primero,
  hay cambios de esta sesion sin commitear (`niveles_soporte.py` editado,
  `monitor_niveles.py` nuevo). `descargar_bin.py` y `descargar_bit.py` son
  autocontenidos (no dependen de `mercado/`) - moverlos solos no rompe
  nada. Mover `niveles_soporte.py`/`grabador_libro.py`/`monitor_niveles.py`
  exige mover `mercado/` tambien (dependen de `mercado.indicadores/datos/
  flujo`), porque el `sys.path.insert` de cada uno asume que `mercado/` es
  hermano de `herramientas/` en la carpeta raiz - si ambas carpetas se
  mueven juntas a `Fran/`, ese path relativo sigue funcionando sin tocar
  codigo.

- 2026-08-07: `niveles_soporte.py` - `_cargar_velas` pasa de leer
  `herramientas/historicos/*_binance.csv` (via `descargar_bin.py`) a
  `herramientas/libro/historico_<COIN>_<TF>_bitget.csv` (via
  `descargar_bit.py`), corrigiendo a peticion de Fran: los niveles se
  vigilan en vivo contra el precio de Bitget (`mercado/datos.py`, mismo
  exchange que opera `monitor.py`) - calcularlos sobre velas de Binance
  podia marcar "toque"/"roto" con precios que Bitget nunca vio. `bin` queda
  disponible para backtests largos que si quieran el historico profundo de
  Binance, pero ya no lo usa `niveles_soporte.py`/`monitor_niveles.py`.

- 2026-08-07: `monitor_niveles.py` - nueva `_asegurar_historico()`, se
  corre ANTES de `_armar_watchlist()`/`_analizar()`. Motivo: `grabador_libro.py`
  ya baja los mismos TF de Bitget (`TIMEFRAMES_VELAS`) pero con `desde=0`
  (arranca desde AHORA, sin historia atras - le basta para su "foto en
  vivo") - si `niveles_soporte.py` leyera ese mismo fichero tal cual,
  calcularia niveles solo con lo acumulado desde que arranco el proceso,
  sin detectar nada real. `_asegurar_historico()` comprueba el timestamp de
  la PRIMERA fila de cada TF en `TIMEFRAMES_NIVELES` (1m a 1d) + el TF
  vigilado + `--tf-macro`; si no llega a `--desde-dias` (90 por defecto)
  hacia atras, fuerza una descarga COMPLETA (`descargar_bit.descargar`,
  reescribe el fichero) en vez de `actualizar()` (que ignora `desde` si el
  fichero ya existe y solo añade hacia adelante, no rellena historia
  vieja). Pendiente de vigilar: si `grabador_libro.py` esta corriendo a la
  vez y su refresco periodico (`--velas-cada`) cae justo cuando este script
  esta escribiendo el mismo fichero al arrancar, podria haber una fila
  entrelazada - ventana corta (solo al arranque de `monitor_niveles.py`,
  una vez) y de bajo impacto (la siguiente `actualizar()` la vuelve a
  alinear), no se puso guardia extra por ahora.

- 2026-08-10: `estrategia/filtros.py` - se eliminan `FiltroBTC`,
  `FiltroSoporte` y `FiltroBollinger` (y se quita `_velas_btc`, solo lo
  usaba el primero). No eran vetos reales, solo sombra - pero
  `backtest_filtros_combinados.py` (9 años BTC+ETH) ya los habia medido
  como veto y los tres empeoraban el resultado SIN EXCEPCION si se
  promovian. Con esa pregunta ya contestada, seguir grabandolos como
  sombra no aportaba nada nuevo, solo gasto (`FiltroBTC` pedia velas de
  BTC aparte en cada intento de apertura). `FiltroCVD` se suma en su
  lugar (sombra, sin backtest de escala todavia - un solo caso real medido
  en conversacion, no alcanza el estandar que ya se exigio a RVOL/
  volatilidad antes de promoverlos).

- 2026-08-10: `FiltroOpenInterest`/`FiltroCVD` - primero se probo
  leyendolos de `flujo_<fecha>_<monedas>.csv` (el CSV que graba
  `herramientas/grabador_libro.py`, proceso APARTE de `monitor.py`), con
  tail-read incremental y cache en memoria para no releer el fichero
  entero cada vuelta (probado contra un CSV real de 22MB: primera lectura
  900 filas, segunda 0.001s). Se REVIRTIO a peticion de Fran: la
  continuidad de `grabador_libro.py` en produccion todavia no esta
  decidida (ver `PENDIENTES.md`, la tarea programada de DSM sigue sin
  armar) y `monitor.py` no debe depender de datos que hoy son solo de
  prueba. Version final: se piden con la MISMA API que ya usa el resto de
  `leer()` (`datos.open_interest`, `datos.trades` - igual patron que
  `_funding_pct`), acumulando la serie en memoria de ESTE proceso. Mismo
  trade-off que ya acepta el resto de `leer()` (un reinicio de
  `monitor.py` vacia la serie) - sin depender de nada externo.

- 2026-08-10: "estancamiento" (`PosicionSim.mfe_pct`/`mfe_dt`) deja de ser
  solo registro y pasa a actuar: si una posicion lleva >=30 min sin
  superar su propio maximo favorable, con la tendencia en contra, y el
  precio sigue del lado bueno de la entrada, mueve el stop a breakeven
  (mismo mecanismo y misma guarda que la reversion contraria - nunca
  EMPEORA el stop). Motivo: con la salida RR fija (`exit_rr>0`, unica rama
  activa desde el 2026-08-07) no hay NINGUN otro mecanismo que proteja una
  posicion que dejo de avanzar - antes se quedaba expuesta al 100% del
  riesgo inicial hasta objetivo o stop, sin importar cuanto tiempo llevara
  sin progresar.

- 2026-08-10: `monitor.py` pasa de 1 posicion de papel por moneda/rama a
  hasta `cfg["posiciones_max"]` (default 3) CONCURRENTES -
  `posiciones[coin]` de `PosicionSim|None` a lista. Se extrajo
  `_gestionar_posicion()` (`posicion/posicion.py`) para gestionar cada una
  sin duplicar la logica de stop/RR/escalera/trailing/estancamiento/
  continuacion; el CSV pasa de una fila fija por vuelta a una fila por
  posicion gestionada + una por el intento de apertura/orden pendiente.
  Guarda anti-hedge añadida (no discutida en backtest, decision de
  diseño): una señal en direccion OPUESTA a una posicion ya abierta no
  apila un hedge sobre el mismo activo (las dos patas se anularian entre
  si y solo sumarian comision) - no abre hasta que esa(s) se cierren por
  su propio stop/objetivo/señal contraria. Verificado con datos simulados
  (sin red real): acumula hasta 3, respeta el tope, cada una cierra
  independiente por su propio stop, y la guarda bloquea/deja pasar segun
  haya o no posiciones contrarias vivas (si el cierre por stop y la señal
  opuesta caen en la MISMA vuelta, la guarda ya ve el libro vacio y deja
  pasar - comportamiento correcto, no un bug).

- 2026-08-10: `telegram_control.py` - el menu/comandos de ajuste se
  recortan de los 31 de `monitor.PARAMS_AJUSTABLES` a los 13 de
  `PARAMS_TELEGRAM` (los que de verdad deciden entrada/riesgo/veto/salida/
  tamaño - a peticion de Fran: "de todos los valores, cuales son los mas
  necesarios de ir tocando"). El resto sigue existiendo en `monitor.py`
  (CLI, `comandos/*.json` a mano) pero ya no se ofrece ni se acepta desde
  Telegram. Se agrega boton/comando **Reset**: devuelve los 13 a su valor
  de ARRANQUE de una vez, derivados llamando a `monitor._parse_args()` (no
  hardcodeados - no se desincronizan si cambia un default en `monitor.py`;
  `exit_rr` es la excepcion, su default crudo es 0.0 pero `main()` lo
  fuerza a 3.0 siempre, se replica esa correccion). Probarlo en rafaga (13
  comandos seguidos) expuso una colision real: el nombre de
  `comandos/*.json` usaba resolucion de milisegundo
  (`time.time()*1000`) y perdia comandos que caian en el mismo
  milisegundo (13 llamadas, 8 archivos) - arreglado con
  `time.time_ns()` + contador de proceso.

  El comando `resumen` cambia de semantica: antes era un log de
  aperturas/cierres de HOY, ahora son las operaciones ABIERTAS y
  PENDIENTES ahora mismo (lado, entrada, stop/objetivo o precio limite,
  pnl), reconstruidas de la fila mas reciente de cada moneda (agrupando
  las que comparten el mismo `fecha_utc` visto, ya que desde el cambio de
  arriba una moneda puede tener varias filas en la misma vuelta).
  Filtrable por moneda O por TF por separado (antes exigia las dos juntas).
  Primera version solo miraba `posicion_lado` y dejaba las ORDENES
  pendientes (`orden_pendiente_lado`, aun sin llenar) invisibles del todo -
  corregido para mostrar ambos estados por separado.
