# Anotaciones

- 2026-08-14: Auditoria de `herramientas/grabador_libro/flujo_ETH.csv` a
  peticion de Fran (analisis fresco, sin apoyarse en scripts guardados -
  el propio Fran confirmo que asi funciona mejor en este proyecto, la
  herramienta se queda obsoleta mas rapido que el dato). 6866 filas,
  2026-08-13 15:19 -> 2026-08-14 19:58 UTC (~28.6h desde que arranco la
  reescritura WS): sin timestamps duplicados, sin huecos >2min, sin filas
  malformadas, los 1715 snapshots de libro crudo (`bids_json`/`asks_json`)
  son JSON valido.

  - **Bug real encontrado y arreglado**: fila 767 (18:31:12, ~16min tras un
    reinicio de proceso) trae el libro cruzado (`bid=1876.42 > ask=1876.37`).
    `mercado/flujo.spread_bps()` ya se protegia explicitamente contra esto
    (`if m <= 0 or p_ask < p_bid: return None`), pero `mid()`/`microprecio()`
    no tenian el mismo guard y calculaban un valor enganoso sobre el libro
    cruzado esa fila. Arreglado (mismo guard `ask[0] < bid[0]` anadido a las
    dos), commit `896af84`.

  - **Salto brusco de CVD investigado y descartado como corte de red**:
    fila 323 (16:39:29), -2292 -> -8886 en un solo tick de 15s (6549 trades,
    delta_vol -6594, muy por encima del resto de ticks ~50-480). Fran
    sugirio que podria ser un corte de red deliberado de prueba. Descartado
    con evidencia cruzada: las velas de 1m reales descargadas por separado
    (`historico_ETH_1m_bitget.csv`) confirman un flash-drop real esa misma
    vela (16:39: open 1875.81, low 1866.00, volumen 21564 - 12-18x los
    minutos vecinos), y el patron alrededor (`n_trades`/`cvd` subiendo
    gradualmente 3 minutos antes, manteniendose elevados varios minutos
    despues) es el de una cascada de liquidaciones organica, no un salto
    aislado. Ademas no existe `huecos_ETH.csv` (el propio detector de
    cortes de `grabador_libro.py`, dispara si el libro lleva >15s sin
    actualizar y SIEMPRE registra, se recupere o no) - nunca se disparo en
    esta sesion. El corte de red deliberado que si hizo Fran fue otro,
    documentado mas abajo (entrada de la reescritura WS, "Fran paro 22800,
    relanzo como 31165, desconecto el NAS...", a las 18:27 UTC) - funciono
    como se esperaba, huecos registrados correctamente para las 3 monedas.

- 2026-08-14: Primer test empirico de `imbalance` (order flow) combinado con
  niveles de `niveles.py`, a peticion de Fran ("el imbalance nos puede
  beneficiar, vamos a comprobarlo"). ETH 15m, mismos parametros de
  produccion (`k=3 tolerancia-atr=0.25 toques-min=4`), niveles vigentes
  actuales aplicados como mapa ESTATICO sobre las ~28.6h completas de
  `flujo_ETH.csv` (~6860 ticks de 15s) - simplificacion aceptada dado que
  es solo una primera intuicion, no un backtest formal. `imbalance`
  bucketeado en fuerte_compra/fuerte_venta (>=0.5 / <=-0.5) vs neutro,
  retorno futuro del `mid` a 5/15/30min.

  **Resultado: sin edge separable de la tendencia con este dato.** El
  patron "cerca de techo = retorno negativo, cerca de suelo = retorno
  positivo" sale limpio pero aparece IGUAL en el bucket `neutro` (imbalance
  debil) - la ventana completa fue una sola tendencia bajista + rebote
  parcial (el mismo flash-drop de la entrada de arriba), asi que "cerca de
  nivel" solo correlaciona con EN QUE MOMENTO de esa tendencia caia cada
  tick, no aporta el imbalance. La comparacion que si importaria (imbalance
  fuerte vs neutro DENTRO del mismo contexto de nivel) no sale monotona ni
  consistente. Unico patron con pinta de real: `ctx=ambos` (techo Y suelo
  solapados en tolerancia) + `fuerte_compra` da retorno positivo creciente
  con el horizonte (+0.008%/+0.026%/+0.044% a 5/15/30min, ~60-62% aciertos,
  n~300) - pero ventanas de hasta 30min sobre ticks de 15s estan muy
  solapadas entre si (n efectivo real mucho menor) y es un solo regimen.
  Mismo caso ya vivido con `rsi_sobreventa` cambiando de signo entre
  regimenes (ver auditoria del Grupo A, 2026-08-13 mas abajo). **Pendiente**:
  seguir grabando en vivo (unica via posible, no se puede backtestear con
  historico profundo - ver auditoria de datos 2026-08-13) y repetir el
  mismo test con mas dias/regimen variado antes de fiarse de cualquier
  patron de aqui.

- 2026-08-14: Bug de biseccion de `descargar_bit._primera_vela_ms()` (ya
  documentado en el codigo para ETH 4h, ver su docstring) confirmado que
  NO es un caso aislado - reproducido tambien en BTC y ETH 15m al bajar
  `--velas` por primera vez para completar el registro de niveles local
  (Fran: "lo tengo ya descargado del filezilla" resulto ser el mismo
  problema, no una copia mas completa - las 3 monedas arrancan su
  `velas/<COIN>/15m_bitget.csv` el mismo 2026-07-16, pese a que
  `herramientas/libro/historico_ETH_15m_bitget.csv` (cache viejo de
  `--feed`) prueba que hay datos reales de Bitget desde al menos
  2026-05-13). La biseccion no converge del todo (tope de 50 pasos, aviso
  en consola) y se queda con un punto "confirmado pero conservador" en vez
  del inicio real. **Pendiente sin decidir**: si merece la pena una
  estrategia mas robusta (reintentos con consenso cerca del borde, o partir
  de una cota inferior conocida como la del cache de `--feed`) o se acepta
  el margen perdido tal cual.

- 2026-08-14: `niveles.py --actualizar` corrido localmente por primera vez
  (antes solo en el NAS) para las 9 combinaciones coin/TF en produccion
  (BTC/ETH/ICP x 15m/1h/4h) - confirma que el mecanismo incremental
  funciona: ETH (velas locales mas frescas, hasta 20:45 UTC) avanzo 9/3/1
  velas nuevas en 15m/1h/4h; BTC/ICP salieron "ya esta al dia" porque sus
  `velas/<COIN>/*.csv` locales no se habian refrescado desde antes (18:30/
  17:00/12:00) - el limite es el CSV de velas disponible, no un fallo del
  mecanismo de actualizacion.

- 2026-08-14: `ccxt.pro` (WS) no funciona en Windows sin arreglo -
  `aiodns.error.DNSError: (11, 'Could not contact DNS servers')` en la
  primera llamada HTTP real de cualquier `ccxtpro.bitget({...})` sin
  `session` propia (`load_markets()`, o cualquier `watch_*`) - `aiohttp`
  usa `aiodns`/`pycares` por defecto si esta instalado, y ese resolver no
  consigue leer la configuracion DNS en Windows. Importa AHORA aunque el
  proyecto solo corra en el NAS hoy - Fran: "si dentro de un año tengo que
  pasarlo a un windows no quiero reconstruirlo todo", portabilidad
  Linux/Windows es requisito de diseño del proyecto, no solo de un script
  suelto.

  Reproducido en DOS maquinas Windows independientes (el sandbox de esta
  sesion Y el Windows real de Fran, error identico en ambas) - no es un
  artefacto de sandbox. Nota de proceso: la primera vez que se reporto
  "falla en Windows" solo estaba verificado en el sandbox, no en una
  maquina de Fran - correccion explicita suya ("pero como sabemos que
  falla en windows?") antes de dar el hallazgo por bueno; se le paso el
  mismo test para que lo corriera el mismo en su Windows real, confirmando
  el fallo primero y el arreglo despues.

  Confirmado que `grabador_libro.py` (unico consumidor de `ccxt.pro` en el
  proyecto hasta ahora) tiene este bug latente: su `ccxtpro.bitget()` (sin
  `session` propia) fallaria igual si se lanzara desde Windows - probado
  su `load_markets()` exacto (mismos parametros, sin credenciales) contra
  el Windows real de Fran, mismo fallo.

  **Fix aplicado a `grabador_libro.py`** (linea ~934, compila limpio -
  pendiente de que Fran lo relance para confirmarlo en marcha; el fichero
  esta en produccion en el NAS ahora mismo, no se ha reiniciado el
  proceso): pasar una `aiohttp.ClientSession` propia con
  `TCPConnector(resolver=aiohttp.ThreadedResolver())` en el constructor de
  `ccxtpro.bitget()` (parametro `session=`), y cerrarla junto con
  `exchange.close()` en los dos puntos donde ya se cerraba. `ThreadedResolver`
  usa `socket.getaddrinfo()` estandar via threadpool en vez de
  `aiodns`/`pycares` - evita el problema por completo, y no depende de
  nada especifico de Windows, asi que no debería cambiar nada en el NAS/
  Linux donde ya corre en produccion (mismo mecanismo de resolucion DNS
  que usa el resto de llamadas REST sincronas del proyecto, que nunca
  tuvieron este problema).

  Contexto: surgio al intentar añadir un modo de refresco por WS a
  `descargar_bit.py` (`herramientas/velas/`, motivado por la necesidad de
  agilidad en TFs rapidos como 1m/3m para el historial de niveles de
  `niveles.py --actualizar`, ver entrada de mas abajo) - de
  momento se sigue con REST (pendiente `--cada` tipo `--feed` sobre
  `--velas`, sin implementar aun) mientras se decide si vale la pena el
  salto a WS tambien ahi, ahora que el fix de portabilidad ya existe y
  esta verificado.

- 2026-08-14: `descargar_bit.py` gana `--velas` (historico PERMANENTE de
  Bitget en `herramientas/velas/<COIN>/<TF>_bitget.csv`, sin el cap de
  `DIAS_OBJETIVO` que usa `--feed`/`herramientas/libro/`) y `niveles_soporte.py`
  se renombra a `niveles.py` y gana `--actualizar` (historial persistente
  de niveles). Motivado por Fran: quiere poder distinguir una resistencia
  historica (confirmada muchas veces a lo largo de años, nunca rota) de
  una resistencia de mera direccion de mercado (recien formada, pocos
  toques) - para eso hace falta historico mas profundo del que daba
  `herramientas/libro/` (capado a 90 dias) y un registro que sobreviva
  entre ejecuciones, cosas que `niveles_soporte.py` no tenia (recalculaba
  todo desde cero cada vez, sin persistencia).

  - **Bug real encontrado en `descargar_bit.py` (afecta tambien a
    `descargar()`/`actualizar()`/`--feed`, no solo a `--velas`, ver
    abajo):** el `since` de Bitget/ccxt es EXCLUSIVO (`>`, no `>=`) -
    comprobado en vivo con ETH 15m: pedir `since=X` (un timestamp de vela
    real) nunca devuelve la vela X misma, siempre la siguiente. Dos
    consecuencias silenciosas antes del fix: (1) `_desde_ms(None,...)`
    (modo "todo el historico") usaba un `since` fijo de 2019-01-01, pero
    Bitget devuelve LISTA VACIA (no clampa a la vela mas antigua real, a
    diferencia de Binance) para cualquier `since` anterior al inicio real
    del listado - `descargar(coin, tf, desde=None)` llevaba tiempo
    devolviendo "No se descargó nada" en silencio para cualquier coin/tf
    cuyo inicio real fuera posterior a 2019 (o sea, todas: BTC/ETH/ICP
    convergen a 2020-01-01/2020-01-01/~2022 respectivamente, verificado
    con llamadas reales). (2) `_descargar_rango()` avanzaba la pagina con
    `since = ultima_vela + tf_ms`, y `actualizar()`/`actualizar_velas()`
    hacian lo mismo al reanudar - sumado a la exclusividad, esto se
    comia SIEMPRE la vela justo en esa frontera: exactamente 1 vela
    perdida en cada frontera de pagina (cada 200 velas, el `limite_req`
    por defecto) Y en cada actualizacion incremental. Reproducido y
    confirmado con una auditoria real de `herramientas/velas/ETH/` (7
    ficheros, patron de hueco de "exactamente 1 vela cada 200 filas" en
    los 7 sin excepcion) - esto llevaba tiempo pasando tambien en
    `herramientas/libro/` via `--feed` (mismo codigo compartido,
    `_descargar_rango`/`actualizar()`), asi que el historico en vivo que
    usan `monitor_senales.py`/`backtest_senales.py`/`marcador_tpsl.py`
    probablemente tenga huecos de este tipo acumulados desde que arranco
    - NO se ha reparado ese historico viejo, solo se corta la sangria de
    aqui en adelante. Fix: `since = ultima_vela` (sin sumar `tf_ms`) en
    los tres sitios, y `_primera_vela_ms()` nueva (biseccion real,
    verificada en vivo: BTC 1d converge exacto a 2020-01-01) en vez de la
    fecha fija, con `-1ms` en el valor devuelto (la exclusividad se comeria
    tambien la primera vela real si no).
  - **Segundo bug, encontrado probando el fix en vivo con ETH 4h:** la
    biseccion de `_primera_vela_ms()` se quedo oscilando sin converger
    nunca (500+ pasos, siempre entre las mismas dos fechas) - la respuesta
    de Bitget para un `since` cerca del borde real NO es perfectamente
    determinista (la misma consulta a veces devuelve datos y a veces
    vacio), rompiendo la asuncion de biseccion pura. Sin tope, esto colgaba
    el proceso para siempre (parecia un lock, no lo era - confundio a
    Fran, ver el `AVISO` que ahora imprime). Fix: `MAX_PASOS_BISECCION=50`
    - si no converge del todo, se acepta el punto mas ajustado confirmado
    con datos en vez de seguir para siempre. Repetido en vivo con BTC:
    salto el aviso de "no convergio del todo" en 6 de sus 7 TF (1m-4h),
    siempre resolviendo a una fecha real igualmente - el tope funciona
    como red de seguridad, no como fallo.
  - `--velas <coin[,coin2,...]> [tf] [--cada segundos]`: sin `--cada`, de
    un tiro (Linux o Windows, sin diferencia - no usa nada especifico de
    plataforma, a diferencia del WS de la entrada de arriba). Con `--cada`
    (añadido mas tarde el mismo dia, `_feed_velas()`, mismo patron que
    `_feed()`): modo daemon. `actualizar_velas()` ya tenia su propio lock
    de fichero por dentro desde el principio, asi que una llamada puntual
    de un tiro (ej. desde `niveles.py --actualizar`) puede convivir con el
    daemon sin pisarse - probado en vivo lanzando el mismo `--velas btc`
    a la vez desde el NAS y desde Windows contra el mismo fichero: uno
    espera al otro por el lock, ninguno corrompe nada.
  - Progreso silencioso corregido de paso: ni la biseccion ni la descarga
    por paginas imprimian nada hasta terminar - con historicos largos (o
    un lock viejo colgado, ver mas abajo) parecia colgado sin estarlo.
    Ahora imprimen cada 5 pasos de biseccion / cada 10 paginas, y
    `_con_lock()` avisa si tiene que esperar en vez de quedarse callado
    hasta el timeout de 1800s.

  - **`niveles_soporte.py` renombrado a `niveles.py`** (a peticion de
    Fran) - actualizados los imports/comentarios en los 8 ficheros que lo
    referenciaban: `backtest_senales.py`, `marcador_tpsl.py`,
    `monitor_niveles.py`, `monitor_senales.py`, `validador_niveles.py`,
    `descargar_bit.py`, `mercado/senales.py` y el propio fichero. Las
    entradas de este log ANTERIORES a hoy (2026-08-07 a 2026-08-13) siguen
    diciendo `niveles_soporte.py` a proposito - asi se llamaba entonces,
    es historial correcto de esa fecha, no se reescriben con el nombre
    nuevo.
  - **`niveles.py --actualizar <coin> <tf> --k .. --tolerancia-atr ..
    --toques-min .. [--confirmacion-velas 2] [--cada segundos]`**: nuevo
    listado persistente en `herramientas/niveles/<COIN>/` -
    `listado_<TF>.json` (estado vivo: precio/tipo/toques/estado/
    seguimiento incremental de cada nivel, se sobreescribe atomico cada
    vez) + `historial_<TF>.csv` (log append-only, una fila por evento:
    `nivel_inicial`/`nivel_nuevo`/`toque`/`rotura`/`flip` - nunca se
    reescribe, solo crece). Mecanismo acordado con Fran tras descartar DOS
    diseños mas complejos que propuse yo primero (uno con modos separados
    `--backfill`/`--continuo` y ventana deslizante tipo
    `backtest_senales._niveles_por_tramos`, otro con cache en memoria +
    re-deteccion periodica) - Fran los simplifico el mismo dia a esto:
    "una vez calculado y anotado no necesita recalcular otra vez, solo lo
    haria si se cambiase algun parametro", y despues "niveles hace un
    primer barrido, y crea un listado con los soportes resistencias
    vigentes, solo tiene que con las nuevas velas, actualizar esos
    estados". Resultado, mucho mas simple que lo que yo habia diseñado:
    - Sin listado previo (o si cambian k/tolerancia-atr/toques-min/
      confirmacion-velas respecto al guardado): `_crear_listado()` - UNA
      pasada de `detectar_niveles()`+`_evaluar_estado()` sobre TODO
      `herramientas/velas/` (no hace falta ventana deslizante, esas
      funciones ya escanean el historico completo de una vez).
    - Con listado existente y MISMOS parametros: `_actualizar()` lee solo
      las velas nuevas desde `ultimo_ts_procesado` y actualiza los niveles
      YA conocidos con `_actualizar_listado_con_vela()` - reproduce las
      mismas dos reglas de `_contar_toques`/`_evaluar_estado` (agrupar
      toques por entrar/salir de tolerancia; `confirmacion_velas` cierres
      consecutivos para romper, primer retoque tras rotura = flip) pero
      vela a vela, O(niveles conocidos) por vela, sin reescanear nada.
      Niveles NUEVOS se detectan igual de barato: `_nuevo_candidato()`
      comprueba si una vela (la de hace `k` velas, que ya tiene sus `k`
      vecinos a cada lado) es un extremo local nuevo - O(k), mismo
      criterio que `indicadores.extremos_locales()` para un solo punto -
      y solo si pasa esa criba cuenta sus toques sobre el historico
      completo (caro, pero raro: hace falta dominar 2k+1 velas).
    - `--cada` (añadido mas tarde el mismo dia, `_feed_niveles()`, mismo
      patron que `--velas --cada`): como `_actualizar()` ya es barata
      cuando no hay nada nuevo ("ya esta al dia"), este bucle no tiene
      coste real la mayoria de las vueltas - el barrido caro solo pasa la
      primera vez o si cambian los parametros.
    - Explicitamente FUERA de alcance por ahora: la herramienta que
      consulte `historial_<TF>.csv` agrupando por zona de precio para
      puntuar "cuantas veces se ha confirmado esta zona a lo largo del
      tiempo" (la idea original de Fran de distinguir resistencia
      historica de resistencia direccional) - el historial ya se esta
      grabando, falta construir la consulta cuando haya datos acumulados
      de sobra.
  - **Caveat conocido, no urgente:** `niveles.py` lee
    `herramientas/velas/<COIN>/<TF>_bitget.csv` SIN lock, mientras que
    `descargar_bit.py --velas` si lo bloquea al escribir - con los dos en
    bucle continuo (`--cada`) existe una ventana pequeña donde `niveles.py`
    podria leer una fila a medio escribir y fallar el parseo. Autocorrectivo
    (el `except Exception` de `_feed_niveles()` lo captura, avisa, y la
    siguiente vuelta relee el fichero ya completo) - sin arreglar a
    proposito, revisar si `herramientas/niveles/_salida.log` empieza a
    acumular avisos repetidos.
  - **Hallazgos de datos en vivo, motivaron/confirmaron el diseño de
    arriba:**
    - Barrido de sensibilidad (presets laxo/medio/estricto variando
      k/tolerancia-atr/toques-min juntos) sobre las 7 TF de ETH: el
      recuento de niveles se reduce de forma consistente 2.2x-2.6x de laxo
      a estricto en las 7 TF por igual (nada se dispara ni se queda
      plano). Confirma en niveles la MISMA asimetria techo/suelo ya
      documentada para señales (entrada de auditoria de asimetria alza/
      baja, 2026-08-13: "rupturas a la baja mas bruscas/rapidas... 
      continuacion alcista mas sostenida") - en casi todas las TF los
      techos acumulan mas toques que los suelos (ej. 4h: techos hasta 40
      toques, suelos hasta 27), excepto 1h, que sale invertido (mas
      toques en suelos) sin explicacion clara todavia.
    - Analisis de antiguedad de niveles vigentes (campo `antig_dias`, ya
      calculado por `_analizar()` pero sin usar hasta ahora): en 1m-15m no
      hay problema real (mediana ~3 dias, el mas viejo se queda dentro del
      60-70% de la ventana disponible). En 4h y sobre todo 1d SI hay
      niveles genuinamente antiguos - 1d con mediana de 204 dias y el mas
      viejo con **1319 dias (3.6 años)**, sin romper desde que hay
      historico de ETH en Bitget (sept. 2022). Confirma la observacion de
      Fran ("el precio actual no es el de hace 3.6 años") de que
      "vigente" mezcla en el mismo listado, con el mismo peso, un nivel de
      la semana pasada y uno de hace años - motivo directo del historial
      persistente de arriba.

  Estado operativo al cerrar esta parte de la sesion: `herramientas/velas/`
  y `herramientas/niveles/` con datos reales para ETH (7 TF) y BTC (7 TF
  velas, 4h/1h/15m niveles) - ICP con velas pero niveles.py --actualizar
  solo lanzado para 4h/1h/15m tambien. En el NAS, corriendo en `--cada 60`:
  3x `descargar_bit.py --velas <coin>` (btc/eth/icp, cada uno cubre sus 7
  TF por dentro) + 9x `niveles.py --actualizar <coin> <tf>` (3 coins x
  4h/1h/15m) + `grabador_libro.py` de siempre - `ESTADO.md` NO se ha
  actualizado con este cambio todavia (sigue reflejando la foto del
  2026-08-13, ya desactualizada en varios frentes de esta sesion).

- 2026-08-13: Auditoria de datos del Grupo A (`mercado/senales.NIVEL_UTIL_GRUPO_A`/
  `REFINADAS_CONFIRMADAS`) a peticion de Fran, motivada por ver en vivo que
  `ruptura_alza_en_resistencia` salia floja en una muestra de ~36h
  (ver entrada de auditoria de resultados en vivo, mas abajo) y por la
  sospecha de que la seleccion original (backtest 2018-2022) "se hizo
  arbitraria". Con `backtest_senales.py` sobre los 8 años de `historicos/`
  (BTC+ETH, 2017-2026, todos los TF) ya disponibles en local:

  - **Barrido de `--dias-niveles-previos` (45/90/180/365d), BTC+ETH 15m,
    2022-2024**: no hay evidencia de que 90 dias sea insuficiente - de
    hecho, para `ruptura_alza_en_resistencia` (la unica que se sostiene,
    ver abajo) el edge se DILUYE segun crece la ventana (BTC: +0.072 con
    45d -> +0.010 con 365d). Mas historia no ayuda, empeora ligeramente -
    coherente con el hallazgo ya documentado de `rsi_sobreventa` cambiando
    de signo entre regimenes (niveles de un regimen viejo pesan menos, no
    mas, cuanto mas lejos quedan). Conclusion: 90d (el actual) sigue siendo
    razonable, no hace falta subirlo.

  - **De las 4 `REFINADAS_CONFIRMADAS`, solo `ruptura_alza_en_resistencia`
    sobrevive fuera de la ventana 2018-2022 que las valido.** Mirando
    `edge_cont@30` (el contexto "contrario" que es la base del filtro de
    Grupo A) en 6 combinaciones coin/TF (BTC+ETH, 15m/1h/4h, todas sobre
    2022-2024): `ruptura_alza_en_resistencia` sale POSITIVA en las 6 sin
    excepcion (+0.067 a +1.621 segun TF). Ninguna otra se acerca:
    `aceleracion_baja_en_soporte` negativa en 5 de 6; `rechazo_max_en_soporte`
    y `ruptura_baja_en_soporte` cambian de signo entre TF/moneda sin
    patron - ruido, no edge real.

  - **Barrido de horizontes (5/10/15/20/30/45/60 velas), BTC+ETH 15m**:
    ningun horizonte "rescata" a las 3 debiles (`aceleracion_baja_en_soporte`
    negativa en 13 de 14 puntos BTC+ETH combinados; `rechazo_max_en_soporte`
    negativa en 12 de 14). `ruptura_alza_en_resistencia` en cambio es
    POSITIVA en los 14 puntos sin excepcion, Y el edge CRECE con el
    horizonte (maximo en @60, no en @30 que es el que usa hoy
    `marcador_tpsl.py`) - candidata a revisar su horizonte de confirmacion
    ademas de mantenerla.

  - **Auditoria de asimetria alza/baja** (Fran: "ruptura_alza_en_resistencia
    y ruptura_baja_en_soporte deberian de salir parecidas, puedes comprobar
    el archivo"): revisadas las 4 piezas donde podria colarse un bug -
    `senales._ruptura()` (linea 131, `>max_previo`/`<min_previo` espejo
    exacto), `niveles_soporte._evaluar_estado()` (linea 154, mismo
    `confirmacion_velas` para techo/suelo), la clasificacion favorable/
    contrario de `backtest_senales._backtest()` (linea 299, simetrica por
    `direccion`), e `indicadores.extremos_locales()` (mismo `k` a cada
    lado). Sin bug encontrado - las 4 son espejo exacto en codigo. Tamaños
    de muestra tambien comparables entre alza/baja (mismo orden de
    magnitud). La divergencia de resultados es real (o al menos no es un
    artefacto de calculo) - hipotesis mas probable, sin poder probarla del
    todo: asimetria de mercado conocida (rupturas a la baja mas bruscas/
    rapidas por cascadas de stop-loss, continuacion alcista mas sostenida),
    pero podria seguir siendo ruido residual con n~500-600.

  - **Confirmado: `niveles_soporte.py` y `mercado/senales.py` son modulos
    independientes** (Fran preguntó antes de tocar codigo: "niveles toca
    señales?") - `niveles_soporte.py` solo importa `mercado.indicadores` y
    `descargar_bit._archivo`, cero referencia a `mercado.senales` (las 3
    menciones que salen en un grep son comentarios, no imports). La
    conexion entre ambos vive solo en los ficheros que importan los dos
    (`monitor_senales.py`, `backtest_senales.py`, `validador_niveles.py`).
    Consecuencia practica: reducir el Grupo A en `mercado/senales.py` no
    toca `niveles_soporte.py` para nada.

  - **Bug encontrado y arreglado de paso**: `backtest_senales._cargar_velas()`
    (linea 124) no se protegia contra filas vacias/incompletas -
    `historicos/05-08-26_ETH_4h_binance.csv` traia un `\r` suelto como
    ultima linea (artefacto de la descarga) y tumbaba el backtest con
    `IndexError`. Arreglado con guardia `if len(row) < 7: continue`. Fran
    resolvio el origen re-descargando el fichero por FileZilla; el guardia
    en el codigo queda igual por si vuelve a pasar con otro fichero.

  - **PENDIENTE, sin implementar todavia**: aplicar en `mercado/senales.py`
    la reduccion del Grupo A a solo `ruptura_alza_en_resistencia` (sacar
    `aceleracion_baja_en_soporte`/`rechazo_max_en_soporte`/
    `ruptura_baja_en_soporte` de `REFINADAS_CONFIRMADAS`), revisando de
    paso si el horizonte de confirmacion deberia subir de 30 a algo mas
    cercano a 60. Afecta a `REFINADAS_CONFIRMADAS`/`REFINADAS_EN_PRUEBAS`
    y a quien las consume: `monitor_senales.py`, `monitor_telegram.py`
    (que decide que se manda por Telegram), `validador_niveles.py`,
    `telegram_control.py`. Se decidio el alcance pero no se ha tocado
    codigo todavia.

- 2026-08-13: Auditoria de resultados en vivo del `grabador_libro.py` REST
  antiguo (PIDs 18047/18048/18049, dejados corriendo a proposito para
  comparar contra la reescritura WS antes de cortar a produccion, ver
  entrada de la reescritura WS) - datos accedidos directo desde
  `D:\neocat\neo\herramientas\libro` (carpeta compartida por red, sin SSH).

  - **Comparacion REST vs WS en la misma ventana solapada (~3.5h)**: WS
    captura sensiblemente MAS trades que REST en las 3 monedas (BTC +32%,
    ETH +14%, ICP +17%) - coherente con el motivo de la reescritura. El
    CVD neto del periodo salio con signo OPUESTO entre REST y WS en las 3
    monedas a la vez - se investigo por si era un bug de lado buy/sell
    invertido en el WS: NO lo es (se comprobo que el signo de `delta_vol`
    coincide con la direccion real del precio fila a fila, 66-88% en
    ambas versiones, practicamente igual) - es que el CVD es un residuo
    NETO pequeño sobre un volumen bruto mucho mayor, muy sensible a que
    trades exactos entran en la muestra, y REST se deja una parte real
    (limite de 500 trades por sondeo de 15s).
  - **Validacion de `avisos_*.csv`/`senales_*.csv` contra las velas reales
    descargadas** (Fran, tras notar que la frescura de los ficheros no
    prueba que sean "en vivo" de verdad, son FileZilla manual, no una
    conexion continua - correccion aceptada, no se debio decir "ahora
    mismo" sin verificarlo): comparado el `precio_actual` de cada aviso
    contra el rango [low,high] real de su vela (Bitget, mismo exchange)
    -> 52/53 coinciden (98%+), el unico desajuste es de 0.02% (ruido). Los
    "sin vela" resultaron ser todos de la vela EN CURSO (nunca grabada a
    proposito por `descargar_bit.py`), no huecos reales. BTC no se pudo
    validar reciente porque su feed de velas lleva parado desde el
    2026-08-12 18:06 (ya documentado, `descargar_bit.py --feed` no cubre
    BTC).
  - **`tpsl_*.csv` (marcador_tpsl.py) win-rate en vivo**: ETH 15m 38.5%
    (n=13), ICP 15m 58.3% (n=12) - muestra demasiado pequeña para
    significar nada (~36h de proceso), coherente con la propia cabecera
    del fichero ("cazar mosquitos con cañones, no tenemos datos
    suficientes"). Señales puras (sin TP/SL, retorno simple a N velas
    contra velas reales) igual de pequeñas (n=1 a n=12) - unico dato algo
    mas solido: `ruptura_alza_en_resistencia` salio 0/5 a 15 y 30 velas en
    esta ventana concreta, lo que motivo la auditoria de datos completa de
    la entrada de arriba (con datos de sobra, no n=5).
  - **Profundidad historica real en Binance para grabador_libro-like data**
    (comprobado con llamadas reales a ccxt, `binanceusdm`, no de memoria -
    Fran: "puedes comprobar en cctx"): funding rate SI tiene historia
    profunda de verdad (`fetchFundingRateHistory` probado hasta 730 dias
    atras, funciona) - candidato real a señal backtesteable en serio. Open
    interest y long/short ratio solo ~30 dias (`fetchOpenInterestHistory`/
    `fetchLongShortRatioHistory` fallan con error explicito de Binance,
    `"startTime is invalid"`, mas alla de eso). Trades (para reconstruir
    CVD historico) practicamente nada - `fetchTrades` con `since` de mas
    de ~2 dias da error explicito `"Search window is restricted to recent
    2 days only"`. Libro de ordenes: ninguna profundidad en ningun
    exchange, es dato de "ahora mismo" por definicion. Conclusion: una
    señal basada en CVD/imbalance de libro NO se puede backtestear como
    las 12 actuales (necesitan acumularse en vivo, meses); una basada en
    funding rate SI podria validarse con años de historia real, igual que
    las de vela.
  - **PENDIENTE sin decidir**: cerrar los 3 procesos REST viejos
    (`kill -INT 18047 18048 18049`) y que hacer con sus ficheros propios en
    `herramientas/libro/` (`flujo_BTC/ETH/ICP.csv`, `flujo_BTC-ETH.csv`,
    los 3 `.lock`) - Fran pidio "eliminarlos" pero el borrado de ficheros
    no se ha ejecutado (fuera del alcance de lo que se hace sin
    confirmacion explicita cada vez) ni se ha decidido entre borrar de
    verdad o archivar a un lado (mismo patron que `.git.corrupto-20260807/`).
    El resto de `herramientas/libro/` (`avisos_*`/`confirmaciones_*`/
    `historico_*`/`senales_*`/`tpsl_*`) NO se toca, pertenece a otros
    procesos que siguen en produccion.

- 2026-08-13: Sesion de diseño (sin codigo todavia) sobre como reorganizar
  Telegram y sobre un futuro supervisor de la cascada de procesos - los tres
  puntos siguientes quedan PENDIENTES, documentados aqui porque
  `PENDIENTES.md` ya no existe (ver entrada de auditoria mas abajo, mismo
  dia).

  - **Telegram, reparto de responsabilidades acordado** (Fran: "usaremos el
    formato de una responsabilidad por accion, reutilizaremos la carpeta
    alertas"): hoy `telegram_control.py` (406 lineas) mezcla mecanica cruda
    de la API de Telegram, los comandos de consulta (`cmd_estado`/
    `cmd_confirmadas`/`cmd_confirmaciones`/`cmd_tpsl`/`cmd_resumen`), los
    menus/teclados y el bucle de `getUpdates`+routing, todo en un fichero.
    Reparto acordado (sin implementar todavia):
    - `alertas/telegram_api.py` (nuevo): mecanica cruda compartida -
      `getUpdates`, `sendMessage`/`editMessageText`/`answerCallbackQuery`,
      troceo por limite de caracteres, teclados inline. `alertas/avisos.py`
      se queda como esta (enviar() de un solo mensaje, lo siguen usando
      monitor_niveles.py/monitor_telegram.py).
    - `herramientas/telegram_grabador.py` (nuevo): unico modulo con
      ESCRITURA - comandos de `grabador_libro.py` (ver `DIR_COMANDOS`/
      `_procesar_comandos` en `grabador_libro.py`: anadir/quitar/reiniciar
      moneda en caliente, ajustar un parametro con los mismos limites de
      `LIMITES_PARAMS`, reset) via el mismo mecanismo de fichero .json que
      ya existe, sin hablar con el proceso directamente.
    - `herramientas/telegram_comandos.py` (nuevo): los `cmd_*` de SOLO
      LECTURA que ya existen (confirmadas/confirmaciones/tpsl/estado),
      movidos tal cual.
    - `herramientas/telegram_control.py` (recortado): solo bucle de
      `getUpdates` + persistencia de offset + menus/teclados + routing -
      cada submenu delega en su propio modulo sin que se conozcan entre si
      (Fran: "no todo en un solo menu, cada submenu un sistema
      independiente").
    - **Alcance decidido para AHORA**: solo `grabador_libro.py` tiene
      cambio en caliente real (es el unico que ya tiene `DIR_COMANDOS`).
      `monitor_niveles.py`/`monitor_senales.py`/`validador_niveles.py`/
      `marcador_tpsl.py` se quedan en solo lectura por Telegram, igual que
      hoy - Fran: "de momento solo grabador, vamos a reconstruir todo lo
      demas... si es mejor hacerlo luego lo hacemos luego, si se puede ir
      creando se crea". Dar a esos cuatro su propio `DIR_COMANDOS` (mismo
      patron que `grabador_libro.py`) queda pendiente para cuando se
      reconstruyan.
  - **`monitor_comun.py` mezcla dos responsabilidades** (detectado al
    repasarlo para el reparto de arriba, no arreglado todavia): "leer el
    flujo en vivo" (`_flt`/`_localizar_csv_libro`/`_tail_csv`/
    `_ultima_fila_coin`) y "comprobar cascada de dependencias vivas"
    (`_listar_procesos`/`_proceso_corriendo`/`_pid_vivo`/
    `_requerir_grabador_libro`/`_requerir_feed_velas`) - conviven en un
    fichero porque las usan los mismos consumidores
    (`monitor_niveles.py`/`monitor_senales.py`), pero la parte de
    "cascada" tambien la usan `validador_niveles.py`/`marcador_tpsl.py` y,
    por separado, `telegram_control.py` tiene su PROPIO parseo de
    `ps -ef` sin reusar `_listar_procesos()` (duplicacion ya detectada en
    `ESTADO.md`, sin arreglar). Candidato a partirse en dos modulos cuando
    se reconstruyan los monitores, mismo criterio "una responsabilidad por
    accion" que Telegram.
  - **Supervisor de la cascada de procesos, aparcado para mas adelante**
    (Fran: "vamos a dejarlo para cuando tengamos el proyecto mas
    adelantado"): la idea es sustituir/complementar la autocomprobacion de
    cada script al arrancar (cadena "avisa y para" del 2026-08-12) por una
    tarea del Programador de DSM que revise periodicamente toda la cascada
    y relance lo que falte, en el orden correcto - motivado por el propio
    incidente de esta sesion (nadie se entero de que faltaba relanzar
    `grabador_libro.py` hasta comprobarlo a mano). Puntos ya discutidos
    para cuando se aborde: (1) config declarativo del "estado deseado"
    (que combos coin/tf DEBERIAN estar vivos), no inferido de lo que ya
    esta corriendo; (2) reusar los mismos `.lock`/PID que ya existen para
    detectar "ya esta vivo", nunca reinventar la deteccion (motivo de dos
    incidentes reales de corrupcion de CVD en este proyecto); (3) verificar
    en vivo que los procesos que lance el supervisor sobreviven al fin de
    la propia tarea programada de DSM (equivalente real a `nohup ... &`,
    no dado por hecho); (4) frecuencia moderada (5-10 min); (5) sin decidir
    todavia si la autocomprobacion de cada script se mantiene como red de
    seguridad ademas del supervisor, o se retira.

- 2026-08-13: `grabador_libro.py` reescrito de polling REST sincrono a
  WebSocket (Bitget v2, via `ccxt.pro`) - motivado directamente por los dos
  bugs reales de CVD de esta sesion (dedup por timestamp del 2026-08-12, y
  el `KeyError('cursor_ids')` encontrado en la auditoria previa al
  reinicio ese mismo dia), ambos originados en la complejidad de mantener
  un cursor de paginacion sobre `fetch_trades`. Con WS cada trade llega
  empujado UNA vez, sin paginar - se elimina esa complejidad en el caso
  normal.

  Decisiones tomadas razonando en conversacion y verificando en vivo con
  un script de prueba aparte (`herramientas/_prueba_ws_bitget.py`, borrado
  tras la verificacion):
  - Canal de libro `books50` da error Bitget 30016 "Param error" en
    USDT-FUTURES (solo existe para spot, no se sabia hasta probarlo en
    vivo) - se usa el canal `books` incremental SIN limite, cuyo checksum
    CRC32 gestiona `ccxt.pro` por dentro (no hay que implementarlo a
    mano). Da MUCHOS mas niveles de los esperados: 500 en BTC/ETH, 200 en
    ICP, verificado en vivo - se guardan TODOS por defecto (Fran:
    "guardemos todo ya que lo tenemos").
  - Funding rate y open interest pasan de REST cacheado a canal `ticker`
    (empuja solo, sin necesidad de espaciar peticiones) - el parametro
    `--funding-cada` desaparece por completo, ya no tiene funcion.
  - Long/short ratio SIGUE por REST (Bitget no lo transmite en directo, es
    un calculo periodico) - unico dato que sigue siendo poll, ahora via
    `loop.run_in_executor` para no bloquear el event loop mientras espera
    la respuesta HTTP.
  - UN SOLO PROCESO/conexion para todas las monedas (antes: un proceso por
    moneda con lock propio, decision del 2026-08-12) - Bitget soporta
    nativamente suscribir varias monedas en un mismo mensaje y anadir/
    quitar en caliente sobre la misma conexion (confirmado en vivo). El
    aislamiento que antes daba gratis el SO (un fallo de una moneda no
    tumbaba las demas) se reconstruye a mano: cada moneda tiene sus
    propias tareas asyncio con su propio try/except.
  - Recuperacion acotada de huecos: al detectar (via el "latido" del
    libro, que en la prueba actualizaba decenas de veces por segundo) que
    ha pasado mas de 15s sin actualizacion, se asume un corte de WS y se
    intenta rellenar los trades perdidos por REST (misma funcion
    `mercado.datos.trades()` ya probada) - acotado a 5 minutos y a un
    numero maximo de llamadas/trades, porque Bitget "no tiene historico
    profundo garantizado" (ver `mercado/datos.py.trades()`). Se registra
    SIEMPRE en `huecos_<COIN>.csv`, se recupere o no, para poder consultar
    en operaciones "¿ha habido algun hueco sin recuperar recientemente?"
    antes de fiarse de una señal - mismo espiritu que la zona de
    indecision que ya usa el proyecto para niveles/señales (Fran: "si no
    creemos que los datos estan correctos, esperar para operar un poco
    mas").
  - Cada script pasa a escribir en su PROPIA carpeta dentro de
    `herramientas/` (Fran: "cada py que lanzamos escriba en una carpeta,
    grabador_libro.py en herramientas/grabador_libro") - ya no comparte
    `herramientas/libro/` con `descargar_bit.py`/los monitores.
    `monitor_comun.py._localizar_csv_libro`/`_requerir_grabador_libro` se
    actualizaron para buscar ahi; `marcador_tpsl.py`/`verificar_flujo.py`
    no necesitaron tocarse (usan esas funciones/`_archivo` importada, no
    rutas a mano). Efecto colateral util: los `flujo_*.csv` viejos en
    `herramientas/libro/` quedan intactos sin tocarlos - ya sirven de
    "copia de antes del cambio" sin necesidad de archivarlos a mano.
  - Verificado en vivo antes de escribir el codigo final: reconexion
    automatica y silenciosa de `ccxt.pro` tras un corte de red real de
    56s (sin ningun error, retoma exactamente donde lo dejo); anadir/
    quitar moneda en caliente sobre la misma conexion sin reconectar (ICP
    anadida, ETH quitada, ambas limpias). Contraste con la version REST:
    durante el mismo corte, `flujo_*.csv` REST siguio escribiendo fila
    cada `--cada` segundos con los campos en blanco (sin crash, CVD
    congelado) - ese mismo criterio honesto ("blanco si el dato no es
    fresco, no repetir el ultimo valor conocido") se traslado al diseño
    WS.

  Dos bugs propios encontrados y arreglados durante la implementacion
  (antes de tocar produccion):
  - Doble bloqueo: `_bloquear_instancia_unica` escribia los locks al
    arrancar Y `_iniciar_coin` volvia a intentar adquirirlos - el proceso
    se rechazaba a si mismo con "ya hay un grabador_libro.py corriendo"
    para las 3 monedas, en la primera prueba en vivo. Arreglado:
    `_bloquear_instancia_unica` pasa a ser solo la comprobacion previa (no
    escribe nada), `_iniciar_coin` es el unico sitio que adquiere el lock
    de verdad.
  - Los niveles del libro de `ccxt.pro` (canal incremental) llevan un
    TERCER elemento interno por nivel (el par crudo en string, para el
    checksum, ver `ccxt/pro/bitget.py.handle_delta`) - `mercado/flujo.py`
    no se ve afectado (accede por indice [0]/[1]), pero se limpia antes de
    guardar en `bids_json`/`asks_json` para no persistir ese elemento
    redundante ni casi doblar el tamaño del JSON sin necesidad.

  Estado al cerrar ESTA parte de la sesion: lanzado en paralelo con los 3
  procesos REST de siempre (btc/eth/icp, PIDs 18047/18048/18049 sin tocar)
  para comparar unos minutos antes de cortar a produccion - continua en la
  entrada de abajo, misma sesion.

- 2026-08-13 (continuacion): auditoria de `grabador_libro.py` a peticion
  explicita de Fran ("de momento, grabador_libro lo ves correcto para su
  funcion? o crees que el codigo no esta bien?"), fichero completo mas los
  dos modulos propios que importa (`mercado/datos.py`, `mercado/flujo.py`).
  Un bug real encontrado y arreglado, mas tres ajustes pedidos sobre la
  marcha - `PENDIENTES.md` se elimino en esta sesion (decision de Fran,
  fuera de esta entrada), asi que lo pendiente de comparar REST-vs-WS y
  cortar a produccion (parrafo de arriba) queda documentado solo aqui de
  ahora en adelante, sin ese fichero como referencia cruzada.

  - **Bug real:** `_recuperar_hueco()` (linea ~505) reintroducia el MISMO
    bug de CVD que ya se arreglo el 2026-08-12 en la version REST (dedup
    por timestamp perdiendo trades del mismo milisegundo) - pero en la
    recuperacion de huecos nueva de la reescritura WS de ayer, que nunca
    paso por esa auditoria porque no existia todavia. Filtraba con `tt <=
    ts_previo: continue`, descartando CUALQUIER trade del milisegundo
    frontera sin llegar a comprobar su `id` en `_procesar_trade` - un
    trade nuevo real que compartiera ese ms con el cursor se perdia en
    silencio (mismo patron que el bug original: hasta 37 trades reales en
    un solo ms, documentado el 2026-08-12). Arreglado a `tt < ts_previo`
    (estricto) - a igualdad de timestamp, ahora es `_procesar_trade` quien
    decide por `id` ya visto, igual que en el ingest normal. Verificado
    importando la funcion REAL del fichero (no una copia) con un cluster
    sintetico en el mismo ms: con `<=` contaba 1 de 3 trades nuevos
    esperados, con `<` los 3.
  - **Cross-platform:** `_lock_libre_o_huerfano()` usaba `os.kill(pid, 0)`
    crudo - en Windows, cualquier señal que no sea CTRL_C_EVENT/
    CTRL_BREAK_EVENT dispara `TerminateProcess()` de verdad (mismo riesgo
    ya corregido en `monitor_comun._pid_vivo()` el 2026-08-12, pero nunca
    aplicado aqui). Nueva `_pid_vivo()` DUPLICADA en `grabador_libro.py`
    (mismo patron POSIX/`os.kill`+Windows/`OpenProcess(
    PROCESS_QUERY_LIMITED_INFORMATION)` que la de `monitor_comun.py` - no
    se importa de ahi porque `monitor_comun.py` ya importa DE
    `grabador_libro.py`, importar en el otro sentido crearia un ciclo).
    Verificado en Windows: PID propio vivo, PID 999999 muerto.
  - **`long_short_ratio` de ICP falla siempre:** Bitget devuelve `40054`
    "The data fetched by ICPUSDT is empty" - confirmado llamando al
    endpoint publico directo (sin pasar por el proyecto): BTC/ETH
    funcionan normal, ICP falla siempre igual, en las dos variantes del
    endpoint (`account-long-short` y `position-long-short`). No es un
    corte transitorio, Bitget no calcula ese dato para ICPUSDT (probable
    volumen/interes abierto insuficiente) - `open_interest` de ICP si
    funciona, es solo el ratio L/S. Nueva `datos.SinDatoParaSimbolo`
    (subclase de `ValueError`) que `long_short_ratio()` levanta cuando
    detecta el codigo 40054 en el mensaje; `_actualizar_ls_ratio()` la
    captura, avisa UNA vez ("no se volvera a pedir en esta sesion") y
    termina la tarea en vez de reintentar cada `ls_ratio_cada` para
    siempre - la columna queda en blanco (mismo criterio de "blanco si no
    hay dato" de siempre), sin el ruido de log infinito. Se reintenta de
    cero en el siguiente arranque o si la moneda se quita/anade en
    caliente - por si Bitget empieza a publicarlo mas adelante. Cubre
    cualquier otra moneda que caiga en el mismo caso, no solo ICP.
    Verificado contra la API real: ICP levanta `SinDatoParaSimbolo`,
    BTC/ETH siguen devolviendo valor normal (sin regresion).
  - **`mercado/__init__.py` y `alertas/__init__.py` eliminados** (decision
    de Fran) - eran solo un comentario de una linea cada uno, sin
    `__all__` ni logica. No hacian falta: `herramientas/` ya funcionaba
    sin `__init__.py` desde antes (namespace package implicito de Python,
    PEP 420, sin necesidad de marcar el directorio como paquete regular) -
    verificado copiando `datos.py`/`flujo.py` a una carpeta sin
    `__init__.py` y confirmando que `from mercado import datos, flujo`
    (el mismo import de `grabador_libro.py`) sigue funcionando igual.
  - **Reconexion por lanzamiento** (peticion de Fran, tras confirmar que un
    `kill -INT`+relanzar NO recuperaba el hueco: "hay que valorar tambien
    la reconexion por lanzamiento"): antes, `_recuperar_hueco()` SOLO se
    disparaba desde `_watch_book()` con el proceso ya corriendo - un
    reinicio (planificado con `kill -INT`, o un crash, o un reinicio del
    NAS) perdia el tramo entre la ultima fila escrita y el arranque
    siguiente en silencio, sin pasar por `huecos_<COIN>.csv` ni intentar
    nada por REST. Nuevo `cursor_<COIN>.json` por moneda (en
    `herramientas/grabador_libro/`, gitignorado igual que el resto de la
    carpeta) con `ultimo_trade_ts` + los `id` de los trades EN ese
    milisegundo (no solo el timestamp - necesario para no perder la
    proteccion de empate al sembrar `ids_recientes` en el arranque
    siguiente), persistido en CADA fila (misma cadencia que el CVD, para
    que ambos queden siempre consistentes entre si sin necesitar un hook
    de cierre limpio aparte). `_iniciar_coin()` siembra el estado desde
    ese cursor al arrancar; nueva tarea `_recuperar_al_arrancar()` llama a
    `_recuperar_hueco()` una vez, reusando tal cual el mismo tope de 5
    minutos y el mismo registro en `huecos_<COIN>.csv` que ya cubria los
    cortes de WS en caliente - un reinicio pasa a tratarse exactamente
    igual. Sin cursor previo (primera vez, o un reinicio tan viejo que
    nunca llego a escribir una fila con el codigo nuevo) no intenta nada,
    igual que antes. Verificado simulando "morir" (procesar trades,
    guardar cursor) y "arrancar" (sembrar estado, recibir un lote de REST
    con un trade duplicado del ultimo ms + uno nuevo del MISMO ms + uno
    posterior) con las funciones reales: el duplicado se descarta por id,
    los dos nuevos se cuentan, CVD final exacto.

  Prueba en vivo real, ya con todo lo de arriba aplicado (Fran paro
  22800, relanzo como 31165, desconecto el NAS de la red unos segundos y
  reconecto): las tres monedas dispararon `_recuperar_hueco()` y quedaron
  registradas en `huecos_<COIN>.csv` sin errores. BTC (0.4s) y ETH (1.4s)
  salieron `sin_datos_nuevos` - coherente con que `_watch_trades()` (tarea
  WS independiente de `_watch_book()`) ya hubiera contado esos trades en
  vivo al reconectar, antes de que la recuperacion REST llegara a mirarlos
  (el dedup por `id` los descarta correctamente como "ya vistos", no es un
  fallo). ICP salio 156.3s, mismo estado - pero NO porque el corte fuera
  mas largo para ICP (comparten la misma conexion WS que BTC/ETH): su
  `ts_ultimo_trade_previo` era ~155s mas viejo que el de BTC/ETH, es decir,
  ICP ya llevaba ~155s sin operar ANTES del corte real (Fran: "icp tiene
  muy poco movimiento"). `duracion_seg` mide tiempo desde el ULTIMO TRADE,
  no duracion real del corte de red - en una moneda poco liquida queda
  inflado por la calma del mercado, no por el corte. Efecto secundario
  identificado y NO arreglado todavia: `TOPE_HUECO_SEG=300s` usa esta
  misma medida para decidir si intenta recuperar - si ICP lleva >5min sin
  operar, la proxima vez que se dispare CUALQUIER hueco de libro (aunque
  el corte real sea de 1 segundo) el codigo lo clasificaria como "hueco
  grande" y ni lo intentaria, no por el corte sino por la calma del
  mercado. Pendiente de decidir si merece la pena separar el criterio del
  tope del que se usa para la ventana de recuperacion.

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
    porque el diseño ya acordado de la Cartera Simulada (cabecera de
    `marcador_tpsl.py`) dice "que lea contrato para comisiones
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
  - sigue sin configurarse (ver `ESTADO.md`).

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
