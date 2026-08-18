# EJEMPLOS DE USO - NIVELES (TEST)

## Setup Inicial

```bash
# Windows
cd D:\neocat\neo

# Linux/NAS
cd /ruta/neocat/neo
```

---

## 1️⃣ AUDITAR NIVELES

Valida que los JSONs estén bien formados y sean coherentes.

### Auditar un coin/timeframe:
```bash
# Windows / Linux / NAS
python test/auditar_niveles.py eth 4h
python test/auditar_niveles.py btc 1h
python test/auditar_niveles.py bnb 1d
```

### Auditar todos los coins (mismo TF):
```bash
# Windows / Linux / NAS
python test/auditar_niveles.py * 4h
python test/auditar_niveles.py * 1h
```

**Valida:**
- ✓ Estructura JSON válida
- ✓ Estados coherentes (vivo/roto/flip)
- ✓ Techos > Suelos
- ✓ Toques lógicos
- ✓ Antigüedad de niveles

---

## 2️⃣ PRUEBA DE NIVELES

Analiza históricos desde archivos Binance (sin guardar).

### Análisis básico:
```bash
# Windows / Linux / NAS
python test/prueba_niveles.py eth 1m --k 3 --tolerancia-atr 0.25 --toques-min 3
python test/prueba_niveles.py btc 4h --k 3 --tolerancia-atr 0.25 --toques-min 3
```

### Con opciones adicionales:
```bash
# Con confirmación de velas
python test/prueba_niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3 --confirmacion-velas 2

# Con filtro de días recientes
python test/prueba_niveles.py eth 15m --k 3 --tolerancia-atr 0.25 --toques-min 3 --desde-dias 30
```

**Requiere:**
- Archivo: `historicos/[date]_[coin]_[tf]_binance.csv`
- Ejemplo: `historicos/17-08-26_ETH_1m_binance.csv`

---

## 3️⃣ BARRIDO INICIAL (Crear JSONs)

Detecta niveles y guarda en JSON para usar en vivo.

### Windows (.bat):
```bash
barrido_inicial_eth.bat
# Ejecuta: 1d, 4h, 1h, 15m, 5m
# Guarda en: herramientas/niveles/ETH/listado_*.json
```

### Linux/NAS (.sh):
```bash
bash barrido_inicial_eth.sh
# Ejecuta: 1d, 4h, 1h, 15m, 5m
# Guarda en: herramientas/niveles/ETH/listado_*.json
```

### Manual (mismo resultado):
```bash
python herramientas/niveles.py eth 1d --k 3 --tolerancia-atr 0.25 --toques-min 3
python herramientas/niveles.py eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3
python herramientas/niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3
```

---

## 4️⃣ MODO VIVO (Updates cada N segundos)

Requiere JSON guardado previamente.

### Windows / Linux / NAS:
```bash
# Update cada 5 minutos (300 segundos)
python herramientas/niveles.py --actualizar eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 300

# Update cada 1 minuto (60 segundos)
python herramientas/niveles.py --actualizar eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 60

# Multiple coins simultáneamente
python herramientas/niveles.py --actualizar btc 4h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 300 &
python herramientas/niveles.py --actualizar eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 300 &
```

---

## 5️⃣ CAMBIAR PARÁMETROS

### Opción A: JSON (recomendado - afecta todos):
Edita: `json/niveles.json`
```json
{
  "parametros": {
    "k": 3,
    "tolerancia_atr": 0.25,
    "toques_min": 3,
    "confirmacion_velas": 2
  }
}
```
Se carga en el siguiente ciclo (sin reiniciar).

### Opción B: CLI (solo este ciclo):
```bash
python herramientas/niveles.py --actualizar eth 4h --k 5 --tolerancia-atr 0.3 --toques-min 4
```

---

## 📊 ESTRUCTURA DE ARCHIVOS

```
neo/
├── test/
│   ├── niveles/
│   │   ├── ETH/
│   │   │   ├── listado_1d.json
│   │   │   ├── listado_4h.json
│   │   │   └── historial_*.csv
│   │   └── BTC/
│   │       └── listado_*.json
│   ├── auditar_niveles.py
│   ├── prueba_niveles.py
│   └── README_EJEMPLOS.md ← ESTE ARCHIVO
│
├── herramientas/
│   ├── niveles.py (principal)
│   ├── auditar_niveles.py
│   ├── prueba_niveles.py
│   ├── niveles/ (en VIVO)
│   │   └── [COIN]/listado_*.json
│   └── velas/
│       └── [COIN]/
│           └── [TF]_bitget.csv
│
├── historicos/
│   └── [date]_[coin]_[tf]_binance.csv
│
├── json/
│   └── niveles.json (configuración global)
│
└── barrido_inicial_eth.bat / .sh
```

---

## 🔧 SOLUCIÓN DE PROBLEMAS

### Error: "Directorio de guardado no existe"
```bash
# Windows
mkdir "D:\neocat\neo\test\niveles\ETH"

# Linux/NAS
mkdir -p "D:\neocat\neo\test\niveles\ETH"
```

### Error: "No hay historico de ETH 1m"
- Verifica que el archivo existe: `historicos/17-08-26_ETH_1m_binance.csv`
- Descárgalo con: `python herramientas/descargar_bit.py --velas eth 1m`

### JSON no se actualiza
- Verifica permisos de escritura en `test/niveles/[COIN]/`
- Prueba auditar: `python test/auditar_niveles.py eth 4h`

---

## 📈 FLUJO RECOMENDADO

1. **Prueba primero:**
   ```bash
   python test/prueba_niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3
   ```

2. **Audita los parámetros:**
   ```bash
   # Si está bien, pasa a barrido
   ```

3. **Barrido inicial (una sola vez):**
   ```bash
   python herramientas/niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3
   ```

4. **Audita el JSON guardado:**
   ```bash
   python test/auditar_niveles.py eth 1h
   ```

5. **Lanza en VIVO:**
   ```bash
   python herramientas/niveles.py --actualizar eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 60
   ```
