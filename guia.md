# AUDITORÍA Y OPTIMIZACIÓN - neo-verificado

## SESIÓN: 2026-08-18

### 1. AUDITORÍA COMPLETA: grabador_libro.py

**Hallazgos críticos identificados:**
- 3,083 spreads inválidos (ask < bid) en ETH - CCXT no valida bidireccional
- 2,649 trades sin bid/ask - libro considerado "no fresco" pero trades se procesaban
- 61 huecos en ICP - recuperación limitada (10 llamadas, 5000 trades máx)
- 75% de datos del libro completo NO grabados (solo cada 60s)

**Restricciones CCXT encontradas:**
- Checksum limitado a 25 niveles (profundidad=1000 sin validación)
- No valida ask >= bid
- ignoreDuplicates en trades puede perder datos out-of-order
- Mejor esfuerzo en entrega (no garantizado)
- Falta sincronización entre streams

### 2. VERSIÓN OPTIMIZADA: grabador_libro.py

**Correcciones implementadas:**
- ✓ Validación bidireccional de spread (línea 400-415)
- ✓ Sincronización con asyncio.Lock() (línea 240, 408-410)
- ✓ Umbral de frescura extendido 30→60s (línea 657)
- ✓ Topes de recuperación aumentados 10→20 llamadas, 5k→50k trades (línea 302-303)
- ✓ Lectura defensiva del libro con try/except (línea 665-677)
- ✓ Contador de spreads inválidos para monitoreo

**Compatibilidad:** 100% backward compatible

### 3. AUDITORÍA: mercado/datos.py y mercado/flujo.py

**datos.py:**
- ✓ Sin bugs críticos
- ✓ Manejo de excepciones presente
- Cliente ccxt sincrónico (separado de async en grabador)

**flujo.py:**
- ✓ Validación robusta de spreads (detecta ask < bid)
- ✓ Cálculos correctos (spread_bps, microprecio, imbalance)
- ✓ Prevención de división por cero

**Ambos módulos:** Aptos para producción

### 4. AUDITORÍA COMPLETA: descargar_bit.py

**Problemas de concurrencia identificados:**
- actualizar() SIN lock - race condition
- Lock insuficiente - no se comparte entre procesos
- _anexar_nuevas() race condition
- _reemplazar_ultima_fila() sin sincronización
- Retry infinito en errores (sin backoff exponencial)

**Restricciones iniciales:**
- VENTANA_MAXIMA (senales.py) limitaba descarga innecesariamente
- Acoplamiento innecesario a módulo de análisis

### 5. OPTIMIZACIONES: descargar_bit.py

**Cambios realizados:**
- ✓ Removida dependencia senales.py (independencia de módulos)
- ✓ Descarga de TODO el histórico sin restricciones
- ✓ Nueva función: validar_historico() - detecta gaps en CSV
- ✓ Nueva función: estado_vela_actual() - estado en vivo de velas
- ✓ Nueva función: validar_todo() - valida todos los TF de una moneda
- ✓ Nuevos comandos CLI:
  - `--validar <coin> <timeframe>` - valida un TF específico
  - `--validar-todo <coin>` - valida todos los TF consolidado
  - `--estado <coin> <timeframe>` - estado en vivo de vela

**Flujo de uso:**
```bash
# Descargar histórico completo
python descargar_bit.py --velas eth

# Validar integridad (todos los TF)
python descargar_bit.py --validar-todo eth

# Monitorear velas en vivo
python descargar_bit.py --estado btc 5m
```

**Capacidades agregadas:**
- Detecta saltos de tiempo (gaps) en datos históricos
- Valida monotonicidad de timestamps
- Tolerancia configurable (5%) para variaciones de procesamiento
- Reporte consolidado de validación
- Estado en tiempo real de vela actual (% completitud, tiempo hasta cierre)

### 6. DOCUMENTACIÓN GENERADA

**Archivos de referencia:**
- `INFORME_AUDITORIA_GRABADOR_LIBRO.md` - Hallazgos detallados
- `ANALISIS_CCXT_Y_BUGS.md` - Restricciones y bugs identificados
- `CAMBIOS_REALIZADOS.md` - Correcciones en grabador_libro.py
- `CAMBIOS_DESCARGAR_BIT.md` - Optimizaciones en descargar_bit.py
- `VALIDAR_TODO.md` - Guía del nuevo comando

### 7. ESTADO ACTUAL

**grabador_libro.py:**
- ✓ Datos validados en auditoría
- ✓ Optimizado para evitar spreads inválidos
- ✓ Sincronización robusta
- ✓ Mejor recuperación de huecos
- ✓ En producción: recolectando datos de BTC, ETH, ICP

**descargar_bit.py:**
- ✓ Independiente de módulos de análisis
- ✓ Descarga histórica completa sin límites
- ✓ Validación automática de integridad
- ✓ Monitoreo en vivo de velas
- ✓ Listo para múltiples plataformas (Bitget ahora, X mañana)

**mercado/datos.py y mercado/flujo.py:**
- ✓ Verificados y funcionales
- ✓ Validaciones robustas
- ✓ Listos para uso

### 8. NUEVA FUNCIONALIDAD: DETECCIÓN DE NIVELES DE SOPORTE/RESISTENCIA

**Objetivo:** Sistema de niveles en vivo para operaciones, con barrido inicial offline y actualizaciones incrementales.

**Archivos creados:**
- `herramientas/niveles.py` - Detector de niveles (prod)
- `test/auditar_niveles.py` - Validador de JSONs
- `test/prueba_niveles.py` - Análisis offline
- `test/README_EJEMPLOS.md` - Guía de uso completa
- `barrido_inicial_eth.bat` / `.sh` - Scripts automatizados

**Funcionalidades:**
- ✓ Detección de extremos locales (soportes y resistencias)
- ✓ Ponderación por volumen (toques más fuertes = más peso)
- ✓ Detección de gaps (saltos sin tocar nivel = muy fuerte)
- ✓ Evaluación de rupturas confirmadas (múltiples velas)
- ✓ Monitoreo en vivo con actualizaciones incrementales (<1s/ciclo)
- ✓ Configuración en caliente desde JSON (sin reiniciar)
- ✓ Auditoría automática de integridad
- ✓ Historial de transiciones (nivel_nuevo, toque, rotura, flip, recuperado)
- ✓ Output con precio actual + resistencia/soporte más cercanos

**Flujo de uso:**

```bash
# 1. Barrido inicial (una sola vez)
python herramientas/niveles.py eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3

# 2. Auditar integridad
python test/auditar_niveles.py eth 4h

# 3. Modo vivo (actualizaciones cada 5 min)
python herramientas/niveles.py --actualizar eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 300
```

**Output en vivo:**
```
[OK] ETH 4H: 487 niveles (1 cambios)
Precio: 1913.6000
Resistencia: 1949.5000 (+1.88%)
Soporte: 1908.0000 (-0.29%)
```

**Parámetros configurables:**
- `k`: ventana de extremos (3-5 recomendado)
- `tolerancia_atr`: multiplicador ATR (0.2-0.3 normal, 0.5+ agresivo)
- `toques_min`: confirmación de nivel (3-4 recomendado)
- `confirmacion_velas`: velas para confirmar rotura (2-3 normal)
- `gap_multiplier`: peso de gaps vs toques normales (1.5-2.0)

---

## ARQUITECTURA DE GUARDADO: CSV vs JSON

**Criterio de elección según tipo de dato:**

### JSON - Para datos jerárquicos/actualizables
```
✓ Niveles detectados (herramientas/niveles/[COIN]/listado_[TF].json)
  - Estructura compleja: params, atr_ref, tolerancia, niveles[]
  - Se actualiza estado (vivo→roto) sin reescribir todo
  - Cambios de parámetros se aplican fácil
  - Velocidad lectura: ~100ms (4.7M velas)

✓ Configuración global (json/niveles.json)
  - Se carga cada ciclo sin reiniciar
  - Cambios aplican en siguiente ciclo
  - Estructura jerárquica (parametros, atr, logica, historial)
```

### CSV - Para series temporales/append-only
```
✓ Histórico de velas (herramientas/velas/[COIN]/[TF]_bitget.csv)
  - Datos secuenciales, nunca se actualizan
  - 4.7M de velas → CSV (30% menor que JSON)
  - Lectura de últimas N velas: ~10ms vs 500ms JSON
  - Append vela nueva: 1 línea vs reescribir todo

✓ Historial de cambios (herramientas/niveles/[COIN]/historial_[TF].csv)
  - Eventos secuenciales (nivel_nuevo, toque, rotura, flip, recuperado)
  - Append-only (nunca se modifica)
  - Fácil análisis con pandas/Excel
```

### .txt - Para configuración de usuario
```
✓ arranques.txt
  - Comandos para iniciar en vivo (Windows/Linux)
  - No se modifica por código, solo por usuario
  - Fácil copiar entre equipos
```

**Tabla de referencia:**
| Dato | Formato | Razón | Actualización |
|------|---------|-------|---------------|
| Velas históricas | CSV | 30% menor, rápida lectura parcial | Nunca (append-only) |
| Niveles detectados | JSON | Estructura compleja, actualizar estado | Cada ciclo (estado) |
| Historial eventos | CSV | Secuencial append-only | Append cada evento |
| Config global | JSON | Jerárquica, se carga en vivo | Manual (usuario) |
| Scripts arranque | .txt | Legible, sin parsing | Manual (usuario) |

---

## ESTRUCTURA DE CARPETAS - PRODUCCIÓN vs TEST

```
neo/
├── herramientas/
│   ├── niveles.py                    ← PRODUCCIÓN
│   ├── niveles/                      ← Datos en vivo
│   │   ├── ETH/
│   │   │   ├── listado_1d.json       (JSON: estructura + estado)
│   │   │   ├── historial_1d.csv      (CSV: eventos)
│   │   │   └── extremos_1d.json      (auxiliar)
│   │   └── BTC/
│   ├── velas/
│   │   ├── ETH/
│   │   │   ├── 1m_bitget.csv         (CSV: histórico)
│   │   │   ├── 4h_bitget.csv
│   │   │   └── 1d_bitget.csv
│   │   └── BTC/
│   └── descargar_bit.py
│
├── test/                             ← TESTING
│   ├── niveles/                      ← Datos de prueba
│   │   └── ETH/
│   │       └── listado_*.json
│   ├── auditar_niveles.py
│   ├── prueba_niveles.py
│   └── README_EJEMPLOS.md
│
├── json/
│   └── niveles.json                  (JSON: configuración global)
│
├── guia.md                           ← Este archivo
└── arranques.txt                     (txt: comandos inicio)
```

**Para deploy en otro equipo:**
1. ✓ Todo el código (*.py, *.sh, *.bat, *.md, *.txt) VIAJA
2. ✅ Estructura de carpetas SE CREA automáticamente (mkdir en scripts)
3. ❌ No copiar: historicos/, velas/, niveles/ (datos específicos)
4. ⚠️ Actualizar: json/niveles.json, arranques.txt (config local)
5. 📥 Descargar: velas con `python descargar_bit.py --velas eth 4h`
6. 🚀 Barrido inicial: `barrido_inicial_eth.bat` o `.sh`

---

## COMMITS REALIZADOS

1. ✓ grabador_libro.py - Versión optimizada
2. ✓ descargar_bit.py - Nuevas funciones y comandos
3. ✓ niveles.py - Sistema de detección de niveles (NUEVO)
4. ✓ auditar_niveles.py - Validador (NUEVO)
5. ✓ prueba_niveles.py - Análisis offline (NUEVO)
6. ✓ barrido_inicial_eth.bat/.sh - Automatización (NUEVO)
7. ✓ test/README_EJEMPLOS.md - Documentación completa (NUEVO)
8. ✓ json/niveles.json - Configuración (NUEVO)
9. ✓ guia.md - Este registro (ACTUALIZADO)

