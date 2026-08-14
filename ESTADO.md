# Estado actual — neo

Foto tomada 2026-08-14 (sesión larga; procesos confirmados vía `ps -ef` real
pegado por Fran durante la sesión, no solo por antigüedad de ficheros —
ver `anotaciones.md` para la cronología completa y el razonamiento detrás
de cada cambio de hoy).

## Git

- Rama: `senales-vela`, sincronizada con `origin/senales-vela`
- Working tree: cambios sin commitear de la sesión de hoy (ver más abajo) —
  se commitean y suben al cerrar esta sesión
- `herramientas/velas/` y `herramientas/niveles/` añadidos a `.gitignore`
  (datos que se regeneran corriendo los scripts, mismo criterio que
  `herramientas/libro/`/`herramientas/grabador_libro/`)
- `herramientas/arranques.txt` (nuevo, sin trackear): notas propias de Fran
  con los comandos `nohup` de arranque de hoy — no incluido en el commit de
  esta sesión, sin decidir si se sube o se queda como scratch local

## Grabador de libro (order book / OI / funding / CVD / long-short)

- PID **5513** (arrancado 13/08, WS vía `ccxt.pro`), sigue vivo — pero con
  el código **anterior al fix de portabilidad Windows** (ver más abajo):
  el fichero en disco ya tiene el fix (`ThreadedResolver`), pendiente de
  que Fran lo relance para que entre en marcha
  (`kill -INT 5513` + relanzar, comando en `anotaciones.md`)

## Historico de velas — dos sistemas en paralelo

**`herramientas/libro/` (90 días, vía `--feed`)**: histórico corto que
siguen usando `monitor_senales.py`/`backtest_senales.py`/`marcador_tpsl.py`
— no verificado en esta sesión si `--feed` sigue corriendo (no se ha tocado
ni comprobado hoy).

**`herramientas/velas/<COIN>/` (permanente, sin cap, nuevo 2026-08-14)** —
único consumidor: `niveles.py`. Confirmado con `ps -ef` real:

| Proceso | Coin | TFs | Cadencia |
|---|---|---|---|
| `descargar_bit.py --velas btc --cada 60` | BTC | las 7 de `TIMEFRAMES_NIVELES` | 60s |
| `descargar_bit.py --velas eth --cada 60` | ETH | las 7 | 60s |
| `descargar_bit.py --velas icp --cada 60` | ICP | las 7 | 60s |

Las 21 combinaciones (3 coins × 7 TF) tienen CSV con datos reales
verificados. Profundidad real por TF (varía por moneda, límite propio de
Bitget — ver `anotaciones.md`): 1m/3m/5m/15m ~29 días, 1h ~2 meses, 4h
~8 meses (desde 2025-12-18/19), 1d varios años (ETH/BTC desde 2022-09,
ICP más corto por ser moneda más nueva en el exchange).

## Listado de niveles persistente (`herramientas/niveles/<COIN>/`, nuevo 2026-08-14)

`niveles.py --actualizar` — confirmado con `ps -ef` real, 9 procesos:

| Coin | TF | Cadencia |
|---|---|---|
| BTC / ETH / ICP | 4h | 60s cada uno |
| BTC / ETH / ICP | 1h | 60s cada uno |
| BTC / ETH / ICP | 15m | 60s cada uno |

Todos con `--k 3 --tolerancia-atr 0.25 --toques-min 4` (mismos parámetros
en las 9). Cada coin/TF tiene `listado_<TF>.json` (estado vivo) +
`historial_<TF>.csv` (log append-only) ya poblados con el barrido inicial.
1m/3m/5m no tienen proceso `niveles.py` corriendo todavía (solo el feed de
velas) — pendiente si se quiere ampliar ahí.

**Caveat conocido sin arreglar** (ver `anotaciones.md`): `niveles.py` lee
el CSV de velas sin lock propio, mientras `descargar_bit.py --velas` sí
bloquea al escribir — ventana pequeña de colisión, autocorrectiva (avisa y
reintenta la siguiente vuelta), vigilar `herramientas/niveles/*.log` por
si se acumulan avisos.

## Monitores y confirmación en vivo (sin verificar en esta sesión)

Estado heredado de la foto del 2026-08-13, **no comprobado hoy** —
`monitor_niveles.py`/`monitor_senales.py`/`validador_niveles.py`/
`marcador_tpsl.py` no aparecieron en ningún `ps -ef` pegado en esta sesión
(los `grep herramientas` de hoy solo mostraban grabador_libro/descargar_bit/
niveles.py, pero tampoco se buscó explícitamente el resto — su ausencia en
el grep no es garantía de que no estén corriendo). Comprobar con
`ps -ef | grep herramientas | grep -v grep` antes de asumir nada.

## Telegram (sin verificar en esta sesión)

Sin cambios conocidos hoy — no comprobado.

## Operación — parar/relanzar `grabador_libro.py`

```bash
kill -INT <pid>
nohup venv/bin/python -u herramientas/grabador_libro.py btc,eth,icp >> herramientas/grabador_libro/_salida.log 2>&1 &
```

`kill -INT`, no `kill` a secas (ver motivo en fotos anteriores). PID actual
en vivo: **5513** — comprobar con `ps -ef` antes de matar, puede haber
cambiado.

## Operación — arrancar velas/niveles permanentes

```bash
nohup venv/bin/python -u herramientas/descargar_bit.py --velas <coin> --cada 60 >> herramientas/velas/<coin>.log 2>&1 &
nohup venv/bin/python -u herramientas/niveles.py --actualizar <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 4 --cada 60 >> herramientas/niveles/<coin>_<tf>.log 2>&1 &
```

Ver `herramientas/arranques.txt` (sin trackear en git) para los comandos
exactos ya usados hoy en las 3 monedas.

## Programador de reinicio del proyecto

Sigue sin haber ninguna tarea programada (DSM Task Scheduler ni cron) para
ningún proceso del stack — todo se arranca/relanza a mano por SSH. Sin
cambios respecto a la foto anterior.

## Código — cambios de hoy (2026-08-14, ver `anotaciones.md` para el detalle completo)

- **`grabador_libro.py`**: fix de portabilidad Windows (`ThreadedResolver`
  en vez de `aiodns` por defecto) — pendiente de relanzar en producción.
- **`descargar_bit.py`**: nuevo modo `--velas` (histórico permanente,
  `herramientas/velas/`) con modo daemon `--cada`; dos bugs reales
  arreglados (el `since` de Bitget es exclusivo — afectaba también a
  `--feed`/`actualizar()` ya existentes, no solo a `--velas`; la búsqueda
  binaria del inicio real del histórico podía colgarse sin converger,
  ahora con tope de 50 pasos).
- **`niveles_soporte.py` renombrado a `niveles.py`** — 8 ficheros
  actualizados (imports + comentarios).
- **`niveles.py`**: nuevo modo `--actualizar` (listado persistente +
  historial append-only de niveles) con modo daemon `--cada`.

Verificado con `python -m py_compile` en los ficheros tocados, y en vivo
contra la API real de Bitget y contra dos máquinas Windows independientes
(el fix de `grabador_libro.py`) — ver `anotaciones.md` para cada
verificación concreta.

## Compatibilidad Windows/Linux pendiente en el resto del proyecto

Sigue pendiente (sin cambios hoy): `monitor_comun.py`/`telegram_control.py`
usan `subprocess.run(["ps", "-ef"])` sin rama Windows — fallan en silencio
ahí. Nuevo hallazgo de hoy, ya arreglado en `grabador_libro.py` pero
**pendiente de revisar si algún otro script usa `ccxt.pro`/WS** (de
momento ninguno más lo usa, `descargar_bit.py --velas` es REST puro a
propósito por este mismo motivo — ver `anotaciones.md`).

## Ficheros de seguimiento eliminados

`PENDIENTES.md` sigue sin existir (eliminado 13/08) — todo el seguimiento
vive en `anotaciones.md`/`ESTADO.md`. Sin cambios.

## Otras carpetas

- `herramientas/confirmaciones/`: vacía (sin comprobar hoy)
- `avisos/` (raíz): vacía (sin comprobar hoy)
- `alertas/`: `avisos.py` + `__pycache__` (sin comprobar hoy)
- `herramientas/velas/`: nueva hoy, 3 monedas × 7 TF, gitignorada
- `herramientas/niveles/`: nueva hoy, 3 monedas × 3 TF (4h/1h/15m), gitignorada
