# niveles.py

Detecta soportes y resistencias sobre velas OHLCV ya cerradas, evalúa si cada
nivel sigue en pie o se ha roto, y escribe el resultado en JSON. Un proceso
por moneda vigila todos los timeframes (TF) a la vez.

## Cómo se inicia

```bash
python niveles.py eth --loop 60                  # daemon, todos los TF disponibles
python niveles.py eth --loop 60 --mercado spot   # idem, mercado spot
python niveles.py eth 1h --una-vez               # una sola pasada de 1h, para probar
python niveles.py eth --desde-dias 90 --loop 60  # fuerza la ventana de velas
python niveles.py eth --confirmacion-velas 3 --loop 60
python niveles.py                                # sin argumentos: imprime la ayuda
```

Argumentos:

| Argumento | Efecto |
|---|---|
| `<coin>` | obligatorio, ej. `eth` |
| `<tf>` | opcional, segundo posicional; vigila solo ese TF en vez de todos |
| `--loop <seg>` | modo daemon: recalcula cada `<seg>` segundos, para siempre |
| `--una-vez` | una sola pasada por TF y sale (no usa lock) |
| `--mercado spot\|futuros` | default `futuros` |
| `--desde-dias N` | ventana de velas a cargar; sobrescribe el params del fichero |
| `--confirmacion-velas N` | sobrescribe el params del fichero |

Un TF sin `params_<coin>_<tf>.json` o sin CSV en `velas/` se salta con un
aviso, no detiene al resto.

## Flujo de datos

```
velas/<COIN>/bitget_<COIN>_<tf>_<mercado>.csv   (entrada: solo velas CERRADAS)
        │
        ▼
  niveles.py  (un Vigilante por TF, dentro de un proceso por moneda)
        │
        ▼
niveles/json/nivel_<COIN>_<tf>_<mercado>_k<K>_toques<M>.json   (salida)
```

En `--loop`, cada iteración mira si el CSV de cada TF cambió en disco
(`CacheVelas`, por `mtime`+tamaño) o si su `params_<coin>_<tf>.json` cambió;
si ninguna de las dos cosas pasó, no recalcula — entre cierres el resultado
sería idéntico byte a byte. `params_<coin>_<tf>.json` se relee en cada
pasada, así que un cambio de parámetros se aplica sin reiniciar el proceso.

Todo el tiempo se maneja en UTC: el timeframe se toma siempre en UTC, nunca
en hora local (una vela 1d cierra a las 00:00 UTC).

## Parámetros (`params_<coin>_<tf>.json`)

| Campo | Rango | Significado |
|---|---|---|
| `k` | 1–20 | velas vecinas que debe dominar un pivote para contar como swing high/low |
| `tolerancia_atr` | 0–5 | ancho de la banda de toque, en múltiplos del ATR de cada vela |
| `toques_min` | 1–10 | toques mínimos para que un pivote se acepte como nivel |
| `confirmacion_velas` | 1–10 | cierres consecutivos al otro lado que confirman una rotura (default 2) |
| `desde_dias` | número o `null` | ventana de velas a cargar; `null` = calculado por TF (ver más abajo) |
| `max_dist_pct` | número o `null` | descarta niveles a más de X% del precio actual; `null` = sin filtro (default 10.0) |
| `max_antig_dias` | número o `null` | descarta niveles cuyo *último* toque sea más viejo que esto; `null` = sin filtro (default 180.0) |
| `separacion_min_atr` | número o `null` | separación mínima entre niveles contiguos, en ATR; `null` = 2× `tolerancia_atr` |
| `periodo_atr` | entero | periodo del ATR de Wilder (default 14); subirlo suaviza la banda |

`k`, `tolerancia_atr` y `toques_min` son obligatorios; el resto tiene default.

### `desde_dias` por defecto

Si no se fija, se calcula para que cada TF cargue ~10.000 velas
(`VELAS_OBJETIVO`), no un número fijo de días: 365 días son 8.760 velas en
1h (manejable) pero 525.600 en 1m (el coste de detección crece
cuadráticamente con el número de velas). Por eso el 1m real solo alcanza
hacia atrás lo que quepa en ~10.000 minutos, mientras que el 1d alcanza
~10.000 días — prácticamente todo el histórico del contrato.

## Esquema del JSON de salida

Nivel superior:

```
timestamp, coin, tf, mercado, fecha_ultima_vela, ts_ultima_vela,
params, config, atr_actual, tolerancia_actual, tolerancia_min,
tolerancia_max, separacion_min, velas_usadas, precio_actual,
num_niveles, niveles: [ ... ]
```

Cada elemento de `niveles`:

| Campo | Qué es |
|---|---|
| `tipo` | `"techo"` o `"suelo"` — **fijo desde que el pivote se formó, nunca se reclasifica** |
| `precio` | precio del nivel |
| `toques` | nº de veces que el precio entró en la banda (entradas distintas, no velas) |
| `primero` / `ultimo` | timestamp (ms) del primer y último toque |
| `velas_dentro` | nº de velas que el precio pasó dentro de la banda, en total |
| `fuerza` | `toques / velas_dentro` — tasa de rechazo (alta = entra y sale, baja = el precio flota ahí) |
| `estado` | `"vivo"` / `"roto"` / `"flip"` — ver más abajo |
| `ts_rotura` / `fecha_rotura` | cuándo se confirmó la rotura más reciente (`null` si vivo) |
| `ts_flip` | cuándo se retocó el nivel después de esa rotura (solo si `flip`) |
| `dias_desde_rotura` | antigüedad de la rotura, en días |
| `dist_pct` | distancia al precio actual, con signo (positivo = nivel por encima del precio) |
| `antig_dias` | días desde el *último* toque (esto es lo que filtra `max_antig_dias`, no la edad del pivote) |
| `vigente` | ver más abajo |

## Estado del nivel: `vivo` / `roto` / `flip`

Se recalcula entero cada pasada, mirando todo el histórico desde que el
nivel se formó:

- **`vivo`**: nunca se confirmó una rotura (`confirmacion_velas` cierres
  seguidos al otro lado).
- **`roto`**: se rompió y el precio no ha vuelto a tocarlo desde entonces.
- **`flip`**: se rompió y, después de esa rotura, el precio lo volvió a
  tocar — cambió de bando.

Se toma la rotura confirmada **más reciente**, no la primera: lo que
importa es la situación actual del nivel, no su historial completo de
roturas.

`estado` es pegajoso: para un `techo`, solo un cierre **por encima** cuenta
como "cruce" en la máquina de estados. Una vez en `flip`, ese nivel se
queda en `flip` para siempre a menos que el precio vuelva a romperlo por
arriba de nuevo (una nueva rotura confirmada), aunque el precio lleve meses
por debajo.

## `tipo` vs `dist_pct` vs `vigente`

`tipo` describe **cómo nació** el nivel (swing high → `techo`, swing low →
`suelo`) y no cambia jamás. Como consecuencia, un `techo` que se rompió por
arriba y luego se volvió a tocar (`flip`) puede terminar **por debajo** del
precio actual — y seguirá figurando como `"techo"` en el JSON. Mirar solo
`tipo` para decidir "¿esto es resistencia u soporte hoy?" es un error.

`vigente` resuelve esto: es `true` si el nivel sigue al lado que su `tipo`
dice, **más allá de la banda de toque actual** (`tolerancia_actual`) — no
solo por el signo de `dist_pct`, que un valor pequeño y positivo puede
seguir estando dentro de la banda de toque. Un `techo` con `vigente=true`
es una resistencia real hoy; con `vigente=false`, es una etiqueta histórica
que ya no describe dónde está el nivel respecto al precio.

`vigente` se recalcula en cada pasada igual que `dist_pct`: no es un flag
que se pone una vez, sube y baja libremente con el precio.

## Fusión y separación mínima

Antes de escribir el JSON, dos pasadas de limpieza:

- **`_fusionar`**: colapsa pivotes del *mismo* tipo separados por menos de
  la tolerancia actual, quedándose con el de mayor `fuerza`/`toques` de
  cada grupo. Techos y suelos se fusionan por separado — mezclar un techo
  con un suelo vecino produciría un `tipo` arbitrario.
- **`_imponer_separacion`**: garantiza una separación mínima
  (`separacion_min_atr` × ATR) entre niveles contiguos, **sin importar su
  tipo**. Con bandas de toque que se solapan, "el precio tocó un nivel" es
  casi siempre cierto y no distingue nada por sí solo; en el choque gana el
  más sólido (`vivo` > `flip` > `roto`, luego `fuerza`, luego `toques`).

## Mecánica operativa

- **`LockFile`**: un lock por moneda con latido (`os.utime` en cada
  iteración), no solo por PID — el lock vive en una carpeta compartida
  (NAS), así que un PID escrito desde otro equipo no significa nada al
  comprobarlo localmente, y `os.kill(pid, 0)` no distingue "no existe" en
  Windows. Un lock sin latido reciente (>3 iteraciones, nunca menos de 5
  minutos) se considera huérfano y se retira solo.
- **`_guardar_atomico`**: escribe a un temporal y hace `os.replace` — un
  lector nunca ve un JSON a medias, ni siquiera si el proceso muere en
  medio de la escritura.
- **`CacheVelas`**: relee el CSV solo si `mtime`+tamaño cambiaron, para no
  pagar un `os.stat()` completo del fichero en cada iteración.
- **Lectura por cola** (`_leer_cola`): un CSV de 1m puede tener millones de
  filas; en vez de leerlo entero, se hace `seek` desde el final y solo se
  lee lo necesario para cubrir la ventana pedida.
- **Señales**: `SIGTERM`/`SIGINT` marcan una bandera que el loop consulta
  entre TF y durante el sueño (`_dormir`, a trozos de 1s) — una parada no
  espera al intervalo completo ni corta un JSON a medias.

## Referencia de funciones

| Función / clase | Qué hace |
|---|---|
| `_tf_a_ms`, `_desde_dias_default` | conversión de TF y ventana por defecto |
| `_ruta_csv` | ruta del CSV de velas de un TF |
| `_fila_vela`, `_parsear`, `_leer_cola`, `_cargar_velas` | lectura y parseo del CSV, por cola |
| `CacheVelas` | evita releer el CSV si no ha cambiado |
| `_contar_toques` | cuenta entradas del precio en la banda de un nivel |
| `_fuerza` | tasa de rechazo de un nivel |
| `_fusionar` | colapsa pivotes cercanos del mismo tipo |
| `_imponer_separacion` | separación mínima entre niveles, cruzando tipos |
| `_evaluar_estado` | calcula vivo/roto/flip de un nivel |
| `_serie_atr` | serie de ATR alineada con las velas, sin huecos |
| `detectar_niveles` | pivotes → candidatos → fusión (sin evaluar estado ni filtrar) |
| `_evaluar_niveles` | añade estado, `dist_pct`, `antig_dias`, `vigente` a cada nivel |
| `_filtrar_niveles` | aplica `max_dist_pct` / `max_antig_dias` |
| `calcular` | una pasada completa sobre un conjunto de velas, sin E/S |
| `LockFile` | lock por moneda con latido, multiplataforma |
| `_guardar_atomico`, `_cargar_json` | E/S de JSON |
| `Vigilante` | un TF: sus params, su cache de velas, su fichero de salida |
| `_tfs_disponibles` | qué TF tienen CSV en disco |
| `loop_principal` | el daemon: lock, bucle, log, parada limpia |
| `main` | parseo de CLI y despacho a `--una-vez` o `--loop` |

## Limitaciones conocidas

- `max_dist_pct` filtra por distancia al precio **actual**, no por
  antigüedad del pivote: un nivel puede venir de hace años (revisitas de un
  ciclo de mercado anterior) y seguir apareciendo si el precio ha vuelto a
  esa zona.
- El nombre del JSON incluye `k`/`toques_min` del momento en que se generó.
  Si cambias esos parámetros, el nombre de fichero cambia — el antiguo
  queda huérfano en disco, no se borra solo.
