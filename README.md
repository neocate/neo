# neo

Captura y análisis de datos de mercado de futuros USDT-M en Bitget.

El proyecto tiene una regla de diseño por encima de todas: **la captura no depende
del análisis**. Los grabadores corren solos, cada uno en su proceso, y dejan los
datos en disco. Cualquier cosa que los lea es un consumidor que puede caerse,
reescribirse o desaparecer sin afectar al registro. Los datos del libro de órdenes
y el CVD no se pueden recuperar a posteriori de ningún histórico del exchange: si
el grabador para, ese tramo se pierde para siempre. Por eso mandan los grabadores.

---

## Arquitectura

```
                 Bitget (ccxt / REST)
                          |
        +-----------------+------------------+
        |                 |                  |
  descargar_bit.py     libro.py         contrato.py
  (velas OHLCV)     (libro + CVD)     (specs del par)
        |                 |
        v                 v
  velas/COIN/*.csv   libro/datos/*.csv
        |                 |
        v                 |
   niveles.py             |
  (soportes y             |
   resistencias)          |
        |                 |
        v                 |
  niveles/json/*.json     |
        |                 |
        +--------+--------+
                 v
           consumidor.py
        (cruce y señales)
```

Tres capas de captura independientes entre sí, un consumidor que las cruza.
Nadie escribe donde escribe otro.

---

## Módulos

| Fichero | Qué es | Cómo corre |
|---|---|---|
| `velas/velas_bit.py` | Librería de descarga OHLCV. Rutas, locks, logs, CSV, detección de huecos. | No se ejecuta |
| `velas/descargar_bit.py` | Producción: mantiene los CSV al día. | Daemon (`--loop`) |
| `velas/descargar_hist_bit.py` | Recolección del histórico completo desde el origen del contrato. | Dos o tres veces en la vida |
| `libro/libro.py` | Graba libro de órdenes, CVD, volumen compra/venta, OI, funding, L/S ratio. Autónomo: su única dependencia es ccxt. | Daemon |
| `niveles/niveles.py` | Detecta soportes y resistencias sobre las velas. Un proceso por moneda+TF. | Daemon (`--loop`) |
| `indicadores/indicadores.py` | Indicadores técnicos (SMA, EMA, ATR…). Librería pura, sin E/S. | No se ejecuta |
| `mercado/contrato.py` | Lee del exchange las specs del par: comisiones, márgenes, precisiones, apalancamiento. | Bajo demanda |
| `consumidor.py` | Cruza niveles + velas + libro y emite señales de confluencia. | Puntual o `--loop` |
| `analizador/src/analyzer.py` | Evaluador multi-TF: predice setup en base a indicadores, libro y niveles. Logging independiente por TF. | Daemon multi-instancia (`--tf 5m --loop 60`) |
| `analizador/src/backtest.py` | Valida predicciones vs precios futuros. P&L realista (comisiones reales de `contrato.py`). Compara TF. | Puntual (`--tf 15m` o `--compare`) |
| `analizador/src/tf_efficiency.py` | Diagnóstico: mide eficiencia de cada TF (flip rate, ruido/señal, calidad de niveles). | Puntual |

---

## Instalación

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env              # y rellenar las claves
```

`.env` nunca se sube: está en `.gitignore`.

---

## Puesta en marcha

El orden importa: niveles necesita velas, y el consumidor lo necesita todo.

**1. Histórico de velas** (una vez; ETH completo son ~7M velas, ~525 MB, 1-2 h)

```bash
python velas/descargar_hist_bit.py eth 1h,15m
```

**2. Velas al día** (daemon; cada TF se despierta en su propio cierre)

```bash
nohup venv/bin/python -u velas/descargar_bit.py eth --loop &
```

**3. Libro de órdenes** (daemon; captura cada 15 s por defecto)

```bash
nohup venv/bin/python -u libro/libro.py eth &
```

**4. Niveles** (un proceso por TF; lee `niveles/params_<coin>_<tf>.json`)

```bash
nohup venv/bin/python -u niveles/niveles.py eth 1h --loop 60 &
```

**5. Consumidor**

```bash
python consumidor.py eth 1h --loop 60
```

En `arranques.txt` están los arranques de todos los TF y los `ps`/`kill` de rigor.

**6. Analyzer** (multi-TF, independiente; requiere velas + libro + niveles)

```bash
# Lanzar dos instancias en paralelo (5m y 15m)
nohup venv/bin/python -u analizador/src/analyzer.py --tf 5m --loop 60 >/dev/null 2>&1 &
nohup venv/bin/python -u analizador/src/analyzer.py --tf 15m --loop 60 >/dev/null 2>&1 &

# O un solo TF
nohup venv/bin/python -u analizador/src/analyzer.py --tf 1h --loop 120 >/dev/null 2>&1 &
```

Genera CSVs independientes por TF: `analizador/datos/eth_setup_log_{tf}.csv`
Logs en: `analizador/log/analyzer_{tf}.log`

**7. Backtest** (valida predicciones del analyzer contra precios reales, con P&L realista)

```bash
# Backtest de un TF
venv/bin/python -u analizador/src/backtest.py --tf 5m

# Comparar todos los TF lado a lado
venv/bin/python -u analizador/src/backtest.py --compare

# Con horizonte diferente (2 horas en lugar de 1)
venv/bin/python -u analizador/src/backtest.py --tf 15m --hours 2
```

**8. Eficiencia de TF** (diagnóstico: cuál TF es más efectivo)

```bash
# Analizar estructura de niveles en todos los TF
venv/bin/python -u analizador/src/tf_efficiency.py
```

---

## Datos en disco

Todas las rutas se resuelven por `__file__`, así que mover o copiar el árbol
(`neo`, `neo_prueba`, `neo_copia`…) no rompe nada.

```
velas/COIN/bitget_COIN_TF.csv     historial OHLCV, solo velas CERRADAS
velas/lock/COIN_TF.lock           un proceso por moneda+TF
velas/log/COIN_TF.log             append, nunca pisa
libro/datos/libro_YYYYMMDD_COIN.csv
libro/logs/libro_COIN.log
niveles/params_COIN_TF.json       parámetros de entrada (se releen en caliente)
niveles/json/nivel_COIN_TF_kN_toquesM.json    salida
niveles/logs/niveles_COIN_TF.log

analizador/datos/eth_setup_log_{tf}.csv      predicciones por TF
analizador/datos/eth_backtest_results_{tf}.csv   validación vs precios reales
analizador/log/analyzer_{tf}.log             logs del analyzer por TF
analizador/log/backtest.log                  logs del backtest
analizador/config/config.json                parámetros del analyzer (se relectura en caliente)
```

Nada de esto se versiona salvo los `params_*.json` y `analizador/config/config.json`.

### Formatos

**Velas** — `timestamp, fecha_utc, open, high, low, close, volumen`

**Libro** — `timestamp_local_ms, fecha_utc, timestamp_exchange_ms, estado, coin,
imbalance, imbalance_niveles, open_interest, funding_rate_pct, long_short_ratio,
n_trades, vol_buy, vol_sell, delta_vol, cvd, bids_json, asks_json`

**Niveles** — `{timestamp, params, niveles[], precio_actual, num_niveles}`, donde
cada nivel es `{tipo, precio, toques, primero, ultimo, estado, dist_pct, antig_dias}`
con `tipo ∈ {suelo, techo}` y `estado ∈ {vivo, roto}`.

---

## Convenciones

- **Todo en UTC.** Sin excepciones, en logs, nombres y columnas.
- **La fecha del CSV del libro no es la fecha de las filas.** `libro_20260821_ETH.csv`
  significa "este proceso arrancó el 21", no "aquí están los datos del 21". El nombre
  se calcula una vez al arrancar y no rota a medianoche. Para saber de qué día es una
  fila, mirar `fecha_utc`.
- **Un lock por recurso.** Dos instancias sobre la misma moneda+TF se bloquean; sobre
  TF distintos conviven.
- **Escritura atómica** en los JSON (`tmp` + `os.replace`): un lector nunca ve un
  fichero a medias.
- **Los logs son append.** Cada arranque y cada parada limpia dejan línea.

---

## Estado actual

La captura funciona. El análisis todavía no tiene una señal medible, y conviene
decirlo antes de que alguien construya encima.

### Lo que está medido

Sobre 81 h de libro (21-ago 12:36 → 24-ago 21:54 UTC, 18.329 capturas) y 30 días
de velas:

- **El imbalance ±0.7 no tiene ventaja demostrable.** Agrupando ticks por cubo de
  imbalance sale una escalera monótona de retornos futuros, pero es un artefacto de
  solapamiento: se muestrea cada 16 s y se mide a 30 min, así que cada observación
  comparte el 97 % de su ventana con la anterior. Con muestras no solapadas quedan
  162 observaciones independientes y 6-8 en los cubos extremos; el signo se da la
  vuelta y nada es significativo (t ≈ 0,6). Por día, la dirección alterna.
- **Los niveles detectados no frenan al precio más que un número al azar.** Tasa de
  rechazo a 2 h: 67,65 % en los niveles detectados frente a 68,65 % en precios
  aleatorios del mismo rango (t = −0,50). Ese ~68 % es la tasa base de reversión de
  ETH en rango, no mérito de la detección.
- **La causa está en la calibración, no en la idea.** Con ATR(14) 1h ≈ 30 USD, la
  banda de toque (`tolerancia_atr` 0,15) es de ±4,56 USD, y en la franja de ±2 %
  alrededor del precio hay 24 niveles vivos separados una mediana de 3,27 USD. La
  separación es **0,72×** la anchura de la propia banda: las bandas de niveles
  contiguos se solapan casi 3 a 1 y el precio está siempre dentro de dos o tres a la
  vez. "El precio tocó un nivel" no distingue nada porque siempre es cierto.
- **`toques` mide recencia, no calidad.** Mediana de toques por antigüedad: <7 d →
  199, 7-90 d → 216, 90-365 d → 156, >1 año → 57. Un nivel acumula toques en
  proporción al tiempo que el precio ha pasado cerca. El filtro `toques >= 5` es
  inoperante: el mínimo real en la banda cercana es 210.
- **El CVD es el dato con más contenido y no se usa.** En esas 81 h el precio sube
  4,21 % con el CVD cayendo a −61.066, OI plano (−0,89 %) y funding cruzando a
  negativo. `consumidor.py` solo lo mira como etiqueta `abs(cvd) > 5000`, umbral que
  se cruzó hace días y del que ya no se vuelve.

### Fallos conocidos, por orden de gravedad

1. **`niveles.py` no recarga velas.** `_cargar_velas()` se llama una sola vez, antes
   del bucle. En cada iteración se recalcula todo sobre el mismo conjunto congelado
   al arrancar, y se reescribe el JSON con un `timestamp` nuevo. Resultado: un
   fichero que *parece* fresco y cuyo `precio_actual` y `niveles` no avanzan nunca.
   Un proceso de días sirve datos del momento en que arrancó.
2. **`consumidor.py`: `niveles_vivos[:10]` no está ordenado.** El comentario dice
   "top 10 por validez", pero la lista llega ordenada por precio ascendente desde
   `niveles.py:212`. Los diez primeros son los niveles más baratos del histórico (para
   ETH, 83-130 USD, a un 95 % del precio), así que el filtro de cercanía los descarta
   todos y no se emite ninguna señal jamás.
3. **El filtro de confluencia no filtra.** `confluencia_vela_nivel(tolerancia_atr=0.02)`
   da ±49,67 USD sobre cada nivel: una franja 8× más ancha que la propia vela 1h.
   Simulado con el fallo (2) corregido, confirma 6 de 6 niveles cercanos por 1h y 6 de
   6 por 15m. Arreglar el orden sin tocar esto no produce buenas señales, produce ~660
   "señales" diarias todas con confianza ALTA.
4. **`consumidor.py` no comprueba frescura.** `ts_niveles` se lee en la línea 169 y no
   se usa. Un JSON de hace diez horas pasa en silencio; el único filtro es un
   `if not all([...])` que además es una comprobación de *falsy* (un `precio_actual`
   de 0.0 se reportaría como "datos incompletos").
5. **`cvd_estado` está clavado en `div`.** Ver arriba: umbral fijo sobre un acumulado
   monótono.
6. **`toques_min` es inerte en el consumidor.** `procesar()` lo recibe y lo usa para
   componer el nombre del fichero, pero `filtrar_niveles()` lleva el 5 escrito a mano.
7. **`--loop 60` no es la cadencia real.** En `niveles.py` una iteración de 1h tarda
   2-3 min (≈70k velas × ≈1.400 niveles en Python puro), y el 15m tardó más de 14 min
   en escribir su primer JSON. El `sleep` va *después* del trabajo, así que el periodo
   es `cómputo + 60`. Lo mismo en `consumidor.py`, donde además `leer_ultima_vela()` y
   `leer_ultimo_libro()` recorren el fichero entero para quedarse con la última fila
   (el CSV del libro ya va por 66 MB), y la deriva crece según crece el fichero.
8. **Una parada silenciosa no deja rastro.** `niveles_eth_1h.log` tiene ARRANQUE a las
   11:44 y el siguiente a las 22:05 sin PARADA en medio: el proceso murió a las 12:10 y
   nadie se enteró hasta mirar los `mtime`.
9. **Restos.** `_hash_dict()` en `niveles.py:287` no lo llama nadie. `params._eth.json`
   no corresponde a ningún TF y no lo lee ningún proceso.

### Siguiente paso

Por valor esperado, y en este orden:

1. Medir el CVD como señal por derecho propio (pendiente en ventana móvil contra
   retorno futuro), en lugar del umbral fijo.
2. Recalibrar la detección de niveles para que la separación mínima sea al menos 2×
   la banda de toque (~9 USD con el ATR actual), lo que dejaría ~10 niveles en ±2 % en
   vez de 24. Hasta entonces ningún test de niveles puede dar señal, y no se sabe si
   falla el concepto o la calibración.
3. Sustituir `toques` por una métrica normalizada por el tiempo que el precio pasó en
   la zona.
4. No volver a medir el imbalance hasta tener 3-4 semanas de libro. Con 162 muestras
   independientes, cualquier resultado es ruido.

---

## Licencia

Proyecto privado.
