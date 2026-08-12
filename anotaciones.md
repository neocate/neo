# Anotaciones

- 2026-08-12: Auditoria de "algebra" (formulas/calculos) sobre todo el
  proyecto a peticion de Fran ("el algebra completo de los py, calculos,
  pruebas... etc"), tras el repaso de inconsistencias estructurales de la
  entrada de abajo. Revisadas formula por formula: `mercado/indicadores.py`
  (SMA/EMA/RSI/ATR/ADX Wilder/RVOL/extremos_locales - alineacion de indices
  correcta en todas, sin off-by-one), `mercado/flujo.py` (mid/spread_bps/
  imbalance/microprecio), las 12 señales de `mercado/senales.py` (ventanas
  "previas" excluyen bien la vela actual, sin lookahead bias),
  `niveles_soporte.py` (clustering/toques/vivo-roto-flip) y
  `backtest_senales.py` (formula de `edge@N`, agregacion 1m->TF,
  ventanas de niveles sin fuga de informacion del futuro) - todo correcto,
  sin bugs. Confirmado tambien: el proyecto no tiene ningun test
  automatizado (`pytest`/`unittest`/carpeta `tests/`), solo los backtests
  manuales.
  - Un hallazgo real, en `grabador_libro.py._trade_flow()`: el CVD
    descartaba trades ya contados comparando SOLO por timestamp (`tt <=
    ultimo: continue`). Si Bitget ejecuta mas de un trade en el MISMO
    milisegundo (no es raro en BTC/ETH en momentos de volumen) y uno ya se
    conto en la vuelta anterior, el otro - real, nunca contado - se
    descartaba tambien por tener el mismo timestamp: su volumen
    desaparecia del CVD en silencio. Invisible para `verificar_flujo.py`:
    el trade perdido nunca llegaba a sumarse a ningun lado, asi que
    `cvd[i] == cvd[i-1]+delta_vol[i]` seguia cuadrando perfectamente
    (consistente consigo mismo, pero con menos volumen real del que hubo).
    Arreglado con dedup por `id` de trade (campo que ccxt normaliza en
    todos los exchanges) en el timestamp FRONTERA: solo se descarta por
    timestamp estrictamente MENOR que el cursor; a igualdad de timestamp,
    se descarta por `id` ya visto, no por el valor del timestamp.
    Verificado en DOS niveles (a peticion explicita de Fran: "haz llamadas
    reales para probar, es importante para dejarlo en datos ficticios" -
    no bastaba con datos simulados): (1) trades simulados a mano, y (2)
    llamadas REALES a la API publica de Bitget (`fetch_trades` no necesita
    credenciales, no hay `.env` en esta maquina y aun asi funciono) contra
    ETH/USDT:USDT en vivo. Los datos reales confirmaron que el problema NO
    era un caso raro de laboratorio: en un solo lote de 500 trades reales
    habia colisiones de timestamp por todas partes, la mayor con **37
    trades en el MISMO milisegundo** - replicando el corte de cursor justo
    despues del primero de ese cluster, la logica VIEJA (solo timestamp)
    habria perdido 36 de esos 37 trades reales, **51.1 ETH de volumen real
    desaparecido del CVD sin ningun aviso**. `_trade_flow()` tambien se
    probo end-to-end con 3 vueltas reales seguidas (arranque + 2 rondas mas
    tras esperar trades nuevos de verdad) - CVD acumulando de forma
    coherente, sin duplicar ni perder el cursor. De paso, la semilla del
    cursor en la primera vuelta pasa de `trades[-1].get("timestamp")`
    (asumia que la API devuelve los trades ya ordenados) a `max(...)` sobre
    todos los timestamps del lote, mas robusto.
  - De la entrada de abajo (repaso de inconsistencias), un detalle que se
    quedo sin documentar: `_pid_vivo()` en `monitor_comun.py` ademas fija
    `restype`/`argtypes` explicitos en la llamada a `OpenProcess` por
    `ctypes` - sin eso, ctypes asume que devuelve un `int` de 32 bits, pero
    un `HANDLE` de Windows es de 64 bits en sistemas x64 (en la practica
    los handles del kernel caben en 32 bits y no se ha visto truncarse,
    pero mejor no depender de eso). Probado en esta misma maquina Windows
    contra el PID del propio proceso Python (vivo) y un PID casi con
    certeza libre.

- 2026-08-12: Repaso de inconsistencias entre los `.py` del proyecto
  (pedido explicito de Fran: "repasa los py para ver inconsistencias"),
  pasada con `ast` para localizar llamadas a funciones no definidas ni
  importadas en cada modulo + lectura cruzada de cabeceras/contratos entre
  ficheros hermanos. 6 arreglos:
  - `validador_niveles.py`/`marcador_tpsl.py`: el handler de
    `except KeyboardInterrupt` de cada uno llamaba a
    `_imprimir_confirmaciones`/`_imprimir_marcador`, resto del refactor
    "imprimir -> devolver texto" del 2026-08-12 (ver entrada de Telegram
    mas abajo) que nunca se actualizo aqui - `NameError` real al parar
    cualquiera de los dos con Ctrl+C en vez de un cierre limpio. Arreglado
    a `print(_texto_confirmaciones(...))`/`print(_texto_marcador(...))`.
  - `monitor_comun._requerir_grabador_libro()` comprobaba el PID del
    `.lock` con `os.kill(pid, 0)` (patron POSIX: señal 0 = solo
    comprobar). En Windows, `os.kill()` con cualquier señal que no sea
    CTRL_C_EVENT/CTRL_BREAK_EVENT llama de verdad a `TerminateProcess()` -
    si el PID (de un proceso Linux del NAS) coincidiera por casualidad con
    un proceso vivo en una maquina Windows corriendo esto (soportado, ver
    entrada de monitor_niveles.py 2026-08-07), lo mataria en vez de solo
    comprobarlo. Nueva `_pid_vivo()`: en POSIX sigue usando `os.kill(pid,
    0)`, en Windows usa `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` +
    `CloseHandle` (solo consulta, nunca termina).
  - `telegram_control.py`: `_enviar()` (flujo de texto libre) troceaba en
    bloques de 4000 caracteres antes de mandar, pero `_enviar_menu()`/
    `_editar_menu()` (flujo de botones, incluido "Resumen") mandaban el
    texto entero en una sola llamada - con varios coin/tf activos
    `cmd_resumen()` puede superar el limite de Telegram (~4096) y fallar en
    silencio solo por boton. Ahora las dos trocean igual
    (`LIMITE_TELEGRAM`); `_editar_menu` con texto largo manda el contenido
    troceado como mensaje nuevo y deja el mensaje del boton como puntero
    corto (no se puede "editar" un mensaje hacia varios).
  - `mercado/contrato.py._leer_funding_rate()` era un stub que devolvia
    `None` siempre ("CCXT puede no tener esto, por ahora") - relevante
    porque el diseño ya acordado de la Cartera Simulada (PENDIENTES.md,
    cabecera de `marcador_tpsl.py`) dice "que lea contrato para comisiones
    y funding". Ahora reusa `mercado.datos.funding_rate()` (implementacion
    real via `fetch_funding_rate` de ccxt, ya en uso por
    `grabador_libro.py`) en vez de duplicar/dejar sin hacer.
    `_leer_interest_rate()` se deja como stub pero con comentario explicito
    de que es a proposito (el proyecto solo opera futuros, nunca margen).
  - `datos.normalizar_simbolo()` no tenia el passthrough de "ya trae '/'"
    que si tenian `descargar_bit.py`/`descargar_bin.py._simbolo` -
    llamarla con un simbolo ya normalizado la habria roto
    (`'ETH/USDT:USDT/USDT:USDT'`). Añadido el passthrough, y
    `descargar_bit.py._simbolo` ahora delega en ella en vez de reimplementar
    la misma conversion Bitget-futuros a mano (mismo exchange, mismo
    formato). `descargar_bin.py._simbolo` se deja SEPARADA a proposito
    (Binance spot, formato de simbolo distinto - no es duplicacion real) con
    comentario aclarandolo para que nadie la "unifique" por error mas
    adelante.
  Verificado con `python -m py_compile` en los 8 ficheros tocados, la
  pasada de `ast` repetida (0 llamadas a nombres no definidos en todo el
  proyecto), y pruebas puntuales de `normalizar_simbolo`/`_simbolo`/
  `_pid_vivo` (esta ultima contra el PID del propio proceso Python, vivo de
  verdad, y un PID casi con certeza libre - nunca contra un proceso ajeno,
  para no arriesgarse con la propia funcion que se estaba corrigiendo).

- 2026-08-12: `mirar.md` borrado - sus 3 hallazgos (todos del mismo dia,
  "post `dc66cd0`") ya estaban resueltos por commits posteriores del propio
  2026-08-11/12, el fichero nunca se actualizo para reflejarlo: (1)
  `VELAS_OBJETIVO=500` (cap por velas) se revirtio a `DIAS_OBJETIVO=90` ese
  mismo dia - `descargar_bit.py` linea 91 ya lo documenta con referencia
  cruzada a `mirar.md`; (2) el auto-arranque fijo a BTC/ETH que denunciaba
  se retiro por completo el 2026-08-12 (ver entrada de la cascada, mas
  abajo); (3) el comentario huerfano `DIAS_HISTORICO_DEFAULT` en
  `backtest_senales.py` ya no existe en el fichero. Corregido de paso,
  detectado al revisar esto: `.gitignore` ignoraba `herramientas/historicos/`
  pero el `historicos/` real (el que lee `backtest_senales.py`,
  `DIR_HISTORICOS`) vive en la RAIZ del repo y se rellena por FTP manual
  via FileZilla (dato de Fran, no hay script/cron que automatice esto) -
  `descargar_bin.py` tambien apuntaba mal (su propio
  `DIR_HISTORICOS` escribia en `herramientas/historicos/`, un sitio que
  `backtest_senales.py` nunca mira). Regla de `.gitignore` corregida a
  `historicos/` y `DIR_HISTORICOS` de `descargar_bin.py` corregido para
  apuntar a la raiz, igual que `backtest_senales.py` - entrada
  correspondiente en `PENDIENTES.md` retirada por resuelta. De paso, dato
  de Fran: `herramientas/libro/` (CSVs en vivo de `grabador_libro.py`/
  `monitor_niveles.py`/etc., ya ignorado en `.gitignore`) TAMBIEN viaja por
  FTP manual via FileZilla, no solo `historicos/` - mismo mecanismo de
  transporte (manual, sin automatizar) para los dos.

- 2026-08-12: Telegram pasa a modo SOLO PULL. `herramientas/telegram_control.py`
  creado (adaptado del patron ya probado en `D:\neocat\bit\telegram_control.py`:
  mismo `getUpdates`/offset persistido/teclados inline, sin las "ramas"/
  `PARAMS_AJUSTABLES`/ajuste en caliente de ese otro proyecto, que no aplican
  aqui) - UNICO proceso que debe hacer `getUpdates` (Telegram reparte los
  mensajes entre quien pregunte primero, no los duplica si dos procesos
  hacen polling a la vez). Comandos: `/start` (menu botones), `estado`
  (que procesos de `herramientas/*.py` siguen vivos, via `ps -ef` - lo que
  hasta ahora habia que comprobar entrando por SSH a mano),
  `confirmadas/coin/tf` (ultima vez que disparo cada una de las 4
  `REFINADAS_CONFIRMADAS`, mismo filtro que usaba `monitor_telegram.py`
  pero bajo demanda), `confirmaciones/coin/tf` y `tpsl/coin/tf` (tablas de
  `validador_niveles.py`/`marcador_tpsl.py --consultar`), `resumen` (todo
  junto). Decision explicita del usuario: `monitor_telegram.py` (aviso
  automatico push de las 4 confirmadas) se para del todo, no convive con
  `telegram_control.py` - "SOLO PULL", sin notificacion automatica, solo
  consulta cuando se pregunta. `validador_niveles._consultar`/
  `marcador_tpsl._consultar` se refactorizaron de imprimir a DEVOLVER texto
  para que `telegram_control.py` los reuse sin duplicar el formato de tabla
  - los procesos en vivo que ya estuvieran corriendo con el codigo viejo
  (solo imprimian) siguen funcionando igual, `telegram_control.py` importa
  su propia copia fresca del modulo desde disco y solo comparte con ellos
  el CSV, no memoria ni proceso.

- 2026-08-12: `herramientas/fjsl.py` construido dos veces y retirado como
  nombre de fichero. Primera version: sobre-elaborada (ciclo completo de
  agentes Explore+Plan en modo plan) para acabar siendo, en esencia,
  `backtest_senales.py` metido en un `while True` con sleep - correccion
  directa del usuario. Segunda version: tail-based (simulaba TP/SL contra
  ticks de `flujo_*.csv` en vivo) pero seguia mezclando dos
  responsabilidades. Version final, separada en dos ficheros ("un .py, una
  obligacion" otra vez):
  - `validador_niveles.py`: por cada una de las 7 señales del Grupo A,
    comprueba si `precio_actual` esta dentro de la tolerancia ATR de un
    nivel vigente del tipo que esa señal necesita (mismo criterio de
    "tocando" que `monitor_niveles.py`) - comprobacion de ESTADO (niveles
    vigentes actuales), no de coincidencia de eventos contra `avisos_*.csv`
    con ventana de tiempo (simplificacion del propio usuario a mitad de
    diseño). Independiente de lo que ya implique el nombre renombrado de la
    señal - puede revelar si esa etiqueta se quedo desactualizada. Escribe
    `confirmaciones_<COIN>_<TF>.csv`.
  - `marcador_tpsl.py`: el marcador TP/SL via ATR (WIN/LOSS/TIMEOUT contra
    flujo en vivo) - deliberadamente NO prioritario ("TP/SL ahora mismo es
    cazar de noche sin luna a mosquitos con cañones, no tenemos datos
    suficientes"), preparatorio para una cartera simulada futura (spec
    completa en la cabecera del fichero: notional 20 USDT, SL 3%/TP 10%,
    comisiones+funding leidos del contrato real, saldo compuesto entre
    operaciones - NADA de eso implementado todavia, solo se guardan pares
    entrada/salida en precio). Escribe `tpsl_<COIN>_<TF>.csv`.
  El nombre `fjsl.py` queda libre para un futuro orquestador que sume
  cartera/funding/comisiones de verdad sobre estos dos.

- 2026-08-12: Auto-arranque de dependencias sustituido por una cascada que
  avisa y para. Antes, `monitor_niveles.py`/`monitor_senales.py` arrancaban
  ellos mismos `grabador_libro.py`/`descargar_bit.py --feed` si no los
  encontraban corriendo (`_asegurar_grabador_libro`/`_asegurar_feed_velas`,
  retirados). Tras liarse relanzando manualmente una cadena larga de
  procesos y dejarse alguno sin relanzar por error, decision del usuario:
  "cada py que lanzamos depende de otro... de esta forma evito dejar sin
  relanzar por error un py". Cadena nueva (comprobada SOLO al arrancar, no
  de forma continua): `grabador_libro.py` + `descargar_bit.py --feed`
  (raiz) -> `monitor_niveles.py`/`monitor_senales.py` (avisan y paran si
  la raiz no esta) -> `validador_niveles.py`/`marcador_tpsl.py` (avisan y
  paran si esos dos monitores no estan vivos - confianza en cascada, no
  re-comprueban la raiz). `grabador_libro.py` se detecta por su lock por
  moneda (fiable aunque se lance combinado); el resto por `ps -ef` con
  coin+tf como token suelto (fiable porque esos procesos son siempre un
  coin+tf por proceso, nunca combinados). Incidente real durante el
  rollout: los `.lock` de `grabador_libro.py` (5 bytes, solo el PID) se
  borraron a mano en una limpieza sin darse cuenta de que ya eran estado
  vivo para esta cascada - los procesos seguian corriendo bien, pero la
  comprobacion decia que no. Arreglo sin reiniciar nada: reescribir el PID
  a mano en el `.lock`.

- 2026-08-12: Tercera moneda (ICP) añadida - solo como grabador_libro.py al
  principio, feed/monitores/validador/marcador añadidos despues para probar
  el comportamiento con una moneda "pequeña". BTC se deja deliberadamente
  SOLO con `grabador_libro.py` (sin feed, sin monitores) - grabando por si
  acaso, sin vigilancia activa por ahora, decision explicita del usuario.

- 2026-08-12: `grabador_libro.py` - lock por moneda
  (`grabador_libro_<COIN>.lock`, antes uno global para todo el proceso) -
  permite `grabador_libro.py btc`/`eth`/`icp` como procesos independientes
  de verdad, en vez de forzar el proceso combinado `btc,eth`. Bug de
  cabecera encontrado al crear `flujo_ICP.csv` desde cero por primera vez:
  `_ultimo_cvd()`/`_ultima_fila_coin()` (mismo patron en las dos) leian la
  PROPIA CABECERA del CSV como si fuera una fila de datos cuando el
  fichero solo tenia esa linea (heuristica vieja de "descarta la primera
  linea SOLO si hay mas de una" fallaba con fichero recien creado) -
  `ValueError: could not convert string to float: 'cvd'`. Doble fix:
  comparar la primera linea contra la cabecera literal en vez de contar
  lineas, Y hacer `.rstrip("\r")` (csv.writer termina cada fila en `\r\n`
  pese a `newline=""` al abrir, lo que rompia esa comparacion la primera
  vez que se intento el fix).

- 2026-08-12: Unificacion de nombres de fichero (`avisos_[coin]_[tf].csv`,
  `senales_[coin]_[tf].csv`, `flujo_[coin].csv` sin tf, `[accion]_[coin]_
  [tf].csv`), todos SIN fecha en el nombre - necesario para que la
  continuidad de CVD entre reinicios (`_ultimo_cvd()`) funcione (un nombre
  con fecha abre un fichero nuevo en cada reinicio, sin nada que leer).
  Columna `pid` (`os.getpid()`) añadida a TODOS los CSV en vivo del
  proyecto, para deteccion directa de escritores duplicados sin tener que
  investigar a mano - motivada por los incidentes de corrupcion de CVD de
  abajo. `grabador_libro.py` pasa de un `flujo_<MONEDA1-MONEDA2>.csv`
  compartido a un `flujo_<COIN>.csv` por moneda. Nueva herramienta
  `verificar_flujo.py`: audita consistencia de CVD (`cvd[i] ==
  cvd[i-1]+delta_vol[i]`) y multiples PID escribiendo el mismo fichero,
  fusionando incidentes separados por <5min (escritores duplicados
  entrelazados a veces producen transiciones consistentes por casualidad
  que fragmentarian un incidente real en docenas de falsos positivos
  pequeños).

- 2026-08-11/12: Dos incidentes reales de corrupcion de CVD por escritores
  duplicados de `grabador_libro.py`, ambos con la misma causa raiz: `ps w`
  (sin `-e`/`-a`) solo lista procesos de la sesion/terminal ACTUAL, oculta
  procesos huerfanos/demonizados de OTRA sesion (`PPID=1`, sin TTY,
  visibles solo con `ps -ef`). Incidente 1: dos huerfanos desde el
  2026-08-07 nunca murieron del todo, invisibles a `ps w`, corrompiendo CVD
  durante horas. Incidente 2: el propio chequeo de auto-arranque de los
  monitores (`_proceso_corriendo`, entonces basado en `ps w`) daba un falso
  "no esta corriendo" y disparo el mismo bug otra vez. Fix: `ps -ef` en
  todos los sitios donde antes se usaba `ps w`, mas un lock de instancia
  unica en `grabador_libro.py` (con guardia de huerfano por PID) que hasta
  entonces no existia. Regla para cualquier sesion futura en este proyecto:
  nunca `ps w` a secas para comprobar procesos daemonizados en este NAS.

- 2026-08-11: Arquitectura separada en procesos de una sola obligacion
  ("un .py, una obligacion" - mismo motivo que ya justifico separar
  `grabador_libro.py` de `monitor.py` en su dia): `grabador_libro.py`
  (SOLO libro/OI/funding/trades/CVD, dato irreconstruible),
  `descargar_bit.py --feed` (SOLO velas, si tienen historico en el
  exchange), `monitor_niveles.py` (SOLO toques de nivel),
  `monitor_senales.py` (SOLO señales de vela, separado de niveles ese
  mismo dia - nunca hubo una version separada previa, se extrajo del
  commit `7a11670` que las tenia fusionadas), `monitor_telegram.py` (SOLO
  notificar, sacado de `monitor_senales.py`), `monitor_comun.py`
  (funciones compartidas). `DIAS_OBJETIVO` de `descargar_bit.py` fue
  primero `VELAS_OBJETIVO=500` (velas planas, intuicion de que un nivel de
  hace 90 dias en TF fino ya es irrelevante) pero se revirtio el mismo dia
  tras re-validar el backtest a esa profundidad real: el edge de
  `REFINADAS_CONFIRMADAS` SI se rompia de verdad con la ventana corta - la
  intuicion no se sostuvo con datos.

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
