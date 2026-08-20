# Fixes para niveles_soporte.py → niveles.py

## Issues de Código

### 1. extremos_locales() en mercado/indicadores.py (línea 333-336)
**Problema:** Usa `>=` y `<=` → marca múltiples velas iguales como extremos (falsos positivos)
```python
# ACTUAL (malo)
if alto_j >= max(vecinos_altos):
if bajo_j <= min(vecinos_bajos):

# DEBE SER (estricto)
if alto_j > max(vecinos_altos):
if bajo_j < min(vecinos_bajos):
```
**Impacto:** Detecta extremos falsos en precios iguales o tendencias planas → niveles espurios

---

### 2. niveles_soporte.py línea 278 — _evaluar_estado() con tolerancia=0
**Problema:** Pasa tolerancia=0 en lugar de la tolerancia calculada
```python
# ACTUAL (malo)
estado, ts_rotura, ts_flip = _evaluar_estado(
    velas, niv["precio"], niv["tipo"], 0, niv["ultimo"], confirmacion_velas)

# DEBE SER
tolerancia_a_usar = ...  # la calculada del ATR
estado, ts_rotura, ts_flip = _evaluar_estado(
    velas, niv["precio"], niv["tipo"], tolerancia_a_usar, niv["ultimo"], confirmacion_velas)
```
**Impacto:** Evaluación de rotura muy estricta (sin tolerancia) → puede no detectar rupturas reales

---

### 3. niveles_soporte.py línea 280 — dist_pct
**Problema:** Validación presente pero dividir por 0 aún es riesgoso
```python
niv["dist_pct"] = (niv["precio"] - precio_actual) / precio_actual * 100 if precio_actual else 0
```
**Mejora:** Agregar validación adicional para valores muy pequeños

---

### 4. niveles_soporte.py línea 441 — Mínimo de 20 velas
**Problema:** Muy bajo para decisiones de producción
```python
if len(velas_objetivo) >= 20:  # ← Bajo
```
**Recomendación:** Aumentar a 50-100 velas mínimo según TF (ejemplo: 5 horas de velas de 5m)

---

### 5. Falta validación de parámetros
**Problema:** k, tolerancia_atr, toques_min vienen de JSON sin validar rangos
```python
k = params.get("k")  # ¿Está entre 1-10?
tolerancia_atr = params.get("tolerancia_atr")  # ¿> 0?
toques_min = params.get("toques_min")  # ¿> 0?
```
**Mejora:** Agregar validación de rangos sensatos en `loop_principal()`

---

## Rename: niveles_soporte.py → niveles.py

### Seguridad del cambio
- ✓ **NO hay imports** — ningún archivo lo importa
- ✓ **NO hay referencias** en código ni .md
- ⚠️ **Cambios necesarios:**
  - Docstring línea 4: `python niveles_soporte.py` → `python niveles.py`
  - Actualizar `comparar_multitf.py` si referencia el nombre (revisar)

### Paso a paso
1. Renombrar archivo
2. Actualizar docstring
3. Verificar comparar_multitf.py no hardcodee el nombre
4. Listo

---

## Prioridad de Fix

**CRÍTICO (antes de producción):**
1. extremos_locales() — falsos positivos
2. _evaluar_estado() tolerancia=0 — no detecta rupturas
3. Validación de parámetros — rangos sensatos

**IMPORTANTE:**
4. Mínimo de 20 velas → aumentar a 50-100
5. Rename a niveles.py

**NICE-TO-HAVE:**
6. Protección adicional para dist_pct con valores muy pequeños
