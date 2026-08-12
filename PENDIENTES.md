# Pendientes

- **Tarea programada de DSM (Programador de tareas)**: confirmado
  2026-08-07 que la opción EXISTE en el DS423 (Synology DSM) — no se
  verificó ni se configuró todavía, solo se comprobó que está disponible.
  Queda pendiente armarla (tarea recurrente, con guardia por PID/lock para
  no duplicar el proceso si ya está corriendo) para todos los procesos en
  vivo del stack (`grabador_libro.py`, `descargar_bit.py --feed`,
  `monitor_niveles.py`/`monitor_senales.py`, `validador_niveles.py`/
  `marcador_tpsl.py`, `telegram_control.py`) - no solo `grabador_libro.py`
  como se pensaba originalmente (la referencia a `fjsl.py` de esta entrada
  ya no aplica, ver `anotaciones.md` 2026-08-12 - se dividió en
  `validador_niveles.py`/`marcador_tpsl.py`, ninguno de los dos existía
  cuando se escribió esto).

- **Cartera simulada** (spec completa en la cabecera de
  `herramientas/marcador_tpsl.py`, ver `anotaciones.md` 2026-08-12): TP/SL
  en % fijo sobre un notional de 20 USDT, comisiones y funding leídos del
  contrato real (ccxt), saldo compuesto entre operaciones. Documentado
  para no perder la especificación, nada implementado todavía - `fjsl.py`
  (nombre libre) queda reservado para cuando se aborde esto como
  orquestador final.

- **Hueco conocido**: el auto-arranque de `descargar_bit.py --feed`
  (cuando existía) y ahora el chequeo `_requerir_feed_velas()` de la
  cascada de dependencias (ver `monitor_comun.py`) solo comprueban si HAY
  algún `--feed` corriendo, no si cubre la moneda que hace falta - un feed
  vivo para btc,eth no detecta que a icp le falta el suyo. Se vio en vivo
  el 2026-08-12 (ICP sin `--feed` durante horas sin que nada avisara hasta
  mirar los logs a mano).

