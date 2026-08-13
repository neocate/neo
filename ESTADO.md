# Estado actual — neo

Foto tomada 2026-08-13 21:24 (hora NAS), actualizada tras el reinicio de `grabador_libro.py` de esta sesión. Basado en presencia/antigüedad de ficheros en disco (no hay acceso a `ps -ef` desde esta sesión).

## Git

- Rama: `senales-vela`, sincronizada con `origin/senales-vela`
- Working tree: limpio (sin ficheros sin trackear ni cambios pendientes)

## Grabador de libro (order book / OI / funding / CVD / long-short)

Dos ubicaciones escribiendo `flujo_BTC.csv` / `flujo_ETH.csv` / `flujo_ICP.csv` en paralelo, **ambas activas ahora mismo** (última escritura <1 min):

| Ubicación | BTC | ETH | ICP | `.lock` |
|---|---|---|---|---|
| `herramientas/grabador_libro/` | activo, PID **5513** | activo, PID 5513 | activo, PID 5513 | `grabador_libro_{BTC,ETH,ICP}.lock` presentes, PID 5513 (proceso relanzado con el código de esta sesión) |
| `herramientas/libro/` | activo | activo | activo | `grabador_libro_{BTC,ETH,ICP}.lock` presentes, sin tocar desde antes de esta sesión |

`herramientas/grabador_libro/cursor_{BTC,ETH,ICP}.json`: existen y se actualizan cada `--cada` (nuevo, ver sección de código) — confirma que el PID 5513 ya corre con el fix de reconexión por lanzamiento.

`herramientas/grabador_libro/_salida.log`: su última escritura es un `ERROR: ya hay un grabador_libro.py corriendo para BTC (PID 5513)` — de un intento posterior de relanzar por encima del que ya estaba vivo, correctamente rechazado por el lock. No es un fallo del proceso 5513 (sigue escribiendo `flujo_*.csv` con normalidad, confirmado); es que ese intento fallido sobreescribió el log con `>` en vez de `>>`, tapando la salida real de 5513.

## Feed de velas (`descargar_bit.py --feed`)

Escribe en `herramientas/libro/historico_<COIN>_<TF>_bitget.csv` y `herramientas/libro/log/velas_<coin>.log`.

| Moneda | Estado |
|---|---|
| ETH | activo (TFs 1m/3m/5m actualizados hace <5 min; 15m/30m hace ~13 min; 1h/4h hace ~43 min) |
| ICP | activo (mismo patrón que ETH) |
| BTC | sin actividad — `historico_BTC_*_bitget.csv` sin tocar desde hace 19 h a 31 h según TF |

## Monitores y confirmación en vivo

Estado por combinación moneda/TF, según último log escrito en `herramientas/libro/log/`:

| Combinación | `monitor_niveles.py` | `monitor_senales.py` | `validador_niveles.py` | `marcador_tpsl.py` |
|---|---|---|---|---|
| ETH 15m | activo (<1 min) | sin actividad ~5 h | activo (~4 min) | activo (~6 min) |
| ETH 1h | activo (<1 min) | sin actividad ~22,3 h | activo (~6 min) | activo (~6 min) |
| ICP 15m | activo (<1 min) | sin actividad ~2 h | activo (~16 min) | activo (~16 min) |
| ICP 1h | activo (<1 min) | sin actividad ~17,7 h | no existe log (no está corriendo) | no existe log (no está corriendo) |
| BTC (cualquier TF) | no existe log (no está corriendo) | no existe log | no existe log | no existe log |

`avisos_ETH_15m/1h.csv` y `avisos_ICP_15m/1h.csv`: todos actualizados en los últimos 5 min.

## Telegram

- `telegram_control.py`: última entrada en `telegram_control.log` hace ~5 h (registra solo cuando hay tráfico, no en bucle continuo — antigüedad no implica proceso parado)
- `telegram_offset.txt`: última escritura hace ~5 h


## Operación — parar/relanzar `grabador_libro.py`

```bash
kill -INT <pid>
nohup venv/bin/python -u herramientas/grabador_libro.py btc,eth,icp > herramientas/grabador_libro/_salida.log 2>&1 &
```

`kill -INT` (no `kill` a secas — SIGTERM no lo captura el código, se saltaría el `finally` que cierra CSV y libera locks). Invoca `venv/bin/python` directo, sin `source activate`. PID actual del proceso en vivo: **5513** (el PID cambia en cada reinicio, comprobar con `ps -ef` o el contenido de `grabador_libro_<COIN>.lock` antes de matar el proceso equivocado). Usar `>>` en vez de `>` si se quiere conservar el log de arranques anteriores en vez de sobreescribirlo en cada intento.

## Programador de reinicio del proyecto

No existe ninguna tarea programada (DSM Task Scheduler ni cron) para ningún proceso del stack — `grabador_libro.py`, `descargar_bit.py --feed`, `monitor_niveles.py`/`monitor_senales.py`, `validador_niveles.py`/`marcador_tpsl.py`, `telegram_control.py` se arrancan y reinician SIEMPRE a mano por SSH, con los comandos de la sección de arriba (o su equivalente por script). Confirmado que el DS423 (Synology DSM) tiene la opción de Programador de tareas disponible, pero no se ha configurado ninguna tarea todavía — ni con guardia de PID/lock para evitar duplicar un proceso si ya está corriendo.

## Código — `grabador_libro.py`

- Comprobación de PID de lock (`_lock_libre_o_huerfano` / `_pid_vivo`): funciona en POSIX (NAS) vía `os.kill(pid, 0)` y en Windows vía `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` — no usa `TerminateProcess` en ningún caso.
- Recuperación de huecos (`_recuperar_hueco`): descarta trades del REST de recuperación con `tt < ts_previo` (estricto) — a igualdad de timestamp con el cursor, `_procesar_trade` decide por `id` ya visto, no se descarta ciego por timestamp.
- `_actualizar_ls_ratio`: si `mercado.datos.long_short_ratio()` levanta `SinDatoParaSimbolo` (Bitget 40054, símbolo sin ese dato de forma estructural — hoy ICP, cualquier otra moneda futura que caiga en el mismo caso queda cubierta igual) la tarea avisa UNA vez y termina, no reintenta más en esa sesión de proceso. La columna `long_short_ratio` de esa moneda queda en blanco (mismo criterio que el resto de columnas sin dato fresco), sin repetir el aviso cada `ls_ratio_cada`. Se reintenta de cero en el siguiente arranque del proceso o si la moneda se quita/añade en caliente.
- `cursor_<COIN>.json` (nuevo, en `herramientas/grabador_libro/`, gitignorado igual que el resto de la carpeta): guarda `ultimo_trade_ts` + los `id` de los trades EN ese timestamp, escrito en cada fila (misma cadencia que el CVD, `--cada`). Al arrancar, `_iniciar_coin()` siembra `estado["ultimo_trade_ts"]`/`ids_recientes"` desde ahí, y una tarea nueva (`_recuperar_al_arrancar`) llama a `_recuperar_hueco()` una vez al inicio — un reinicio del proceso (planificado o no, `kill -INT`/crash/reinicio del NAS) pasa a tratarse igual que un corte de WS en caliente: mismo tope de 5 min, mismo registro en `huecos_<COIN>.csv`, misma protección de empate de milisegundo por `id`. Sin cursor previo (primera vez) no intenta nada, igual que antes.
- Verificado localmente (Windows, `python -m py_compile` + repro con las funciones reales): `_pid_vivo` reconoce PID propio como vivo y un PID inexistente como muerto; el filtro de huecos cuenta correctamente un trade nuevo que comparte milisegundo con el cursor (en vivo Y tras un reinicio simulado con `cursor_<COIN>.json`); `datos.long_short_ratio()` contra la API real de Bitget levanta `SinDatoParaSimbolo` para ICP y sigue devolviendo valor normal para BTC/ETH (sin regresión).

## Compatibilidad Windows/Linux pendiente en el resto del proyecto

`grabador_libro.py` ya soporta ambas plataformas para su comprobación de PID. Dos sitios más en el proyecto comprueban procesos vivos y **solo funcionan en POSIX** (usan `subprocess.run(["ps", "-ef"])`, sin rama Windows — en Windows fallan en silencio y el chequeo asume "no está corriendo"):

- `herramientas/monitor_comun.py` → `_listar_procesos()` / `_proceso_corriendo()` (usado por la cascada de dependencias de `validador_niveles.py`/`marcador_tpsl.py`)
- `herramientas/telegram_control.py` → su propio parseo de `ps -ef` para los comandos `estado`/`resumen` (implementación separada de la de `monitor_comun.py`, no reutilizada)

`mercado/__init__.py` y `alertas/__init__.py` se eliminaron (eran solo un comentario de una línea cada uno, no hacía falta — `mercado/`/`alertas/` siguen siendo importables como namespace packages implícitos de Python, verificado; `herramientas/` ya funcionaba así, sin `__init__.py`, desde antes). No queda ningún `__init__.py` en el código propio del proyecto.

## Ficheros de seguimiento eliminados

`PENDIENTES.md` ya no existe (eliminado en esta sesión) — lo que seguía abierto de ahí (comparación REST-vs-WS de `grabador_libro.py`, menú de Telegram en caliente aparcado, programador de reinicio sin configurar, cartera simulada sin implementar, cobertura del feed por moneda) queda documentado solo en `anotaciones.md`/`ESTADO.md` de ahora en adelante.

## Otras carpetas

- `herramientas/confirmaciones/`: vacía
- `avisos/` (raíz): vacía
- `alertas/`: `avisos.py` + `__pycache__`
