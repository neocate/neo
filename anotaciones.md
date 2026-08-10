# Anotaciones

- 2026-08-10: Rama `senales-vela` creada, bifurcada de `4524945` (el commit
  "solo herramientas/mercado", antes de que existiera `monitor.py`
  multi-posicion). Decision del usuario tras confirmar que en 2 semanas
  `monitor.py` en vivo no dio resultados ("no hemos conseguido pasarlo a
  verde") - lo va a parar. `master` en GitHub queda intacto (con el fix de
  locking `5062332` y el modulo `monitor.py`/`telegram_control.py`/
  `alertas`/`estrategia`/`posicion`/`registro` de `5bbf05e`) por si se
  retoma mas adelante - esta rama es un desarrollo aparte, no lo toca.

- 2026-08-10: `mercado/senales.py` creado - 12 señales de VELA (impulso/
  aceleracion/ruptura/rechazo/divergencia RSI/RSI extremo), sobre velas YA
  CERRADAS (mismo criterio que `descargar_bit.py`). A diferencia de
  `flujo.py` (libro de ordenes, irreconstruible - ver entradas de
  `grabador_libro.py` mas abajo), esto trabaja sobre velas: se puede
  recalcular en frio en cualquier momento. `detectar(velas, k)` acota
  internamente a `VENTANA_MAXIMA=500` velas antes de tocar
  `indicadores.atr()/rsi()` - si no, un caller que le pase el historico
  completo (90 dias de 1m son ~130k filas) y la llame en CADA vela nueva
  durante una sesion larga hace que el costo por vela crezca sin limite
  (Wilder decae exponencial - a partir de unas pocas centenas de velas la
  diferencia frente a usar todo el historico es insignificante).

- 2026-08-10: `herramientas/backtest_senales.py` creado - backtest OFFLINE
  de `senales.py` sobre historico profundo (`historicos/` de Binance o
  `herramientas/libro/` de Bitget), sin depender de sesion en vivo. Mide
  retorno medio a N velas por señal, ajustado por la tendencia neta del
  periodo (`edge@N = ret@N - direccion*baseline@N` - comparar contra el
  baseline crudo sin ajustar de signo favorece a ciegas a las señales que
  apuestan a favor de la tendencia del periodo probado). `--desde-1m`
  agrega velas desde 1m en vez de usar el historico nativo del TF (para
  comparar fuentes offline, sin tocar `monitor_niveles.py` con esto).

- 2026-08-10: `backtest_senales.py --tolerancia-atr/--toques-min` -
  contraste de señales por contexto de nivel vigente (favorable/contrario/
  lejos, via `niveles_soporte.detectar_niveles`+`_evaluar_estado`). Los
  niveles se RECALCULAN cada `--refresco-niveles` dias (ventana movil de
  `DIAS_NIVELES_PREVIOS=90` dias) en vez de una foto fija tomada en
  `--desde` - con periodos de años, una foto fija queda obsoleta enseguida
  (niveles de 2018 no significan nada en 2021). Ademas, si el precio hizo
  tendencia limpia en la ventana previa, solo confirma niveles de UN lado
  (BTC ago-nov 2018: 105 niveles, TODOS techo, cero suelo - con
  `--toques-min 4` casi nunca confirma un suelo en caida libre, cada
  minimo se deja atras sin retest).

- 2026-08-10: Hallazgo del "Grupo A" (backtest BTC+ETH 2018-2022, 15m/1h/
  4h, agregado desde 1m, niveles con refresco cada 30 dias): 7 de las 12
  señales dan mejor edge cuando hay un nivel vigente del tipo CONTRARIO a
  su propia direccion cerca (ej. `ruptura_alza` funciona mejor rompiendo
  una resistencia real que en espacio abierto) - justo lo opuesto de la
  intuicion de "nivel a mi favor". De esas 7, solo 4 confirman edge@30
  positivo Y consistente en las 6 combinaciones moneda/TF:
  `ruptura_alza_en_resistencia`, `aceleracion_baja_en_soporte`,
  `rechazo_max_en_soporte`, `ruptura_baja_en_soporte`
  (`senales.REFINADAS_CONFIRMADAS`). Las otras 3
  (`rsi_sobrecompra_en_soporte`, `rsi_sobreventa_en_resistencia`,
  `div_bajista_en_soporte`) mejoraron frente a su version sin filtrar pero
  siguen sin edge fiable (`REFINADAS_EN_PRUEBAS`) - probablemente les
  falte un filtro de regimen (ver siguiente entrada). `senales.detectar()`
  acepta `niveles_vigentes`/`tolerancia_nivel` opcionales (compatibles con
  las llamadas existentes que no pasan nada) para aplicar este
  filtro/renombrado.

- 2026-08-10: `rsi_sobreventa` cambia de SIGNO segun el regimen probado -
  edge@30 positivo (+0.5 a +0.8) en la ventana 2022-2026 (mayormente
  alcista), negativo (-0.5 a -0.9) en 2018-2022 (mezcla alcista/bajista) y
  en los 8 años completos. Confirma que las señales de REVERSION dependen
  del regimen del periodo probado, a diferencia de las de CONTINUACION
  (`ruptura_alza`/`impulso_alza`), que salen positivas en todas las
  ventanas probadas. Pendiente: filtro de regimen (tendencia de fondo, ej.
  pendiente de EMA larga o ADX) para las 3 de `REFINADAS_EN_PRUEBAS` - no
  implementado todavia.

- 2026-08-10: `monitor_niveles.py` conectado en vivo con las 4 señales
  confirmadas del Grupo A - evalua `senales.detectar()` con
  `niveles_vigentes` construido de `r["techos"]/r["suelos"]` (agrupados
  por ROL EFECTIVO, ya con el flip aplicado - OJO, el campo `tipo` de
  `watch` NO es fiable para esto, guarda el tipo ORIGINAL aunque el nivel
  haya hecho flip; hay que usar el grupo `r["techos"]`/`r["suelos"]`, no
  `n["tipo"]`) y la MISMA tolerancia que ya usa la watchlist
  (`r["tolerancia"]`). Solo un TF para esto (sin `--tf-macro`), igual que
  el backtest que lo valido.

- 2026-08-10: `monitor_niveles.py` - arranque con estado real:
  `_ultima_fila_coin()` lee la ULTIMA fila ya grabada del flujo (cola de
  256KB, no el fichero entero) para fijar `imbalance`/`cvd` desde el
  primer segundo y marcar bien que niveles ya estan "tocando" en ese
  momento - antes arrancaba siempre en blanco (`None`/`False` en todos)
  hasta el primer tick nuevo, aunque el precio YA estuviera dentro de
  tolerancia de algun nivel. Decision explicita del usuario: NO reproducir
  todo el historico de flujo ya grabado (se penso, se descarto) - los
  timestamps de los avisos quedarian mal (todo con la hora de "ahora"
  aunque el evento real fuera de hace horas) y seria mucho ruido de golpe.

- 2026-08-10: Telegram - `telegram_control.py` (panel de comandos completo
  del `monitor.py` multi-posicion: abrir procesos, ajustar parametros en
  caliente, cartera, menus de botones inline) ELIMINADO en esta rama -
  dependia enteramente de `import monitor`, que no existe aqui (ver
  primera entrada de hoy). `alertas/avisos.py` (ya existente, `enviar
  (texto)` simple via `.env`) se conecta directo en `monitor_niveles.py`:
  manda Telegram SOLO cuando dispara una de las 4 confirmadas
  (`REFINADAS_CONFIRMADAS`) - los toques de nivel y las señales sin
  confirmar se quedan en el CSV, no se mandan (demasiado ruido). Bug
  detectado y arreglado el mismo dia: el envio estaba en el bucle que
  procesa TODAS las señales activas (incluidas las 5 fuera del Grupo A,
  tipo `rechazo_min`), no solo las 4 confirmadas - `rechazo_min` llego a
  mandar un Telegram real antes del fix.

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
