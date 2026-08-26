# ETH Setup Analyzer

Analizador de setups técnicos para ETH futuros, con logging automático para backtesting.

## Estructura

```
analizador/
├── src/
│   ├── analyzer.py       # Núcleo del análisis
│   └── backtest.py       # Backtesting tool
├── log/                  # Logs de ejecución
├── datos/                # CSVs de análisis
├── config/               # Configuración
├── run.sh                # Script ejecutor (Linux/Mac/Git Bash)
├── requirements.txt      # Dependencias Python
└── README.md            # Este archivo
```

## Instalación

### Requisitos
- Python 3.7+
- pandas
- numpy

### Setup

```bash
cd analizador
pip install -r requirements.txt
chmod +x run.sh
```

## Uso

### Ejecutar análisis una sola vez
```bash
python3 src/analyzer.py
```

### Ejecutar en loop (cada 60 segundos)
```bash
python3 src/analyzer.py --loop 60
```

### Ejecutar en loop con script
```bash
./run.sh 60
```

### Ejecutar en background con nohup (estilo niveles.py)
```bash
cd neo/analizador
nohup python3 -u src/analyzer.py --loop 60 >/dev/null 2>&1 &
```

### Con parámetros personalizados
```bash
python3 src/analyzer.py --coin ETH --mercado futuros --loop 60 --verbose
```

### Correr backtest
```bash
python3 src/backtest.py
```

### Ver logs en tiempo real
```bash
tail -f log/analyzer.log
```

### Ver último análisis
```bash
tail -1 datos/eth_setup_log.csv
```

## Archivos Generados

### datos/eth_setup_log.csv
Registro de todos los análisis con columnas:
- `timestamp`: Momento del análisis
- `signal`: LONG/SHORT/WAIT
- `confidence`: % de confianza (0-1)
- `strength`: STRONG/MEDIUM/WEAK
- `price`: Precio actual
- `sma_5m`, `high_5m`, `low_5m`: Datos 5m
- `trend_5m`, `trend_15m`: Tendencias
- `vol_ratio`: Volumen relativo
- `imbalance`: Desequilibrio del libro
- `delta`: Delta de volumen
- `funding_rate`: Tasa de financiamiento
- `rsi_5m`: RSI del 5m

### log/analyzer.log
Log detallado de ejecuciones

## Argumentos de línea de comandos

```
python3 src/analyzer.py [OPTIONS]

Options:
  --coin COIN              Coin to analyze (default: ETH)
  --mercado MARKET         Market type: futuros/spot (default: futuros)
  --loop SECONDS           Loop interval in seconds (default: single run)
  --verbose                Enable verbose output
  --help                   Show this help message
```

### Ejemplos
```bash
# Una sola vez
python3 src/analyzer.py

# Loop cada 30 segundos
python3 src/analyzer.py --loop 30

# Background con nohup
nohup python3 -u src/analyzer.py --loop 60 >/dev/null 2>&1 &

# Con logs visibles
nohup python3 -u src/analyzer.py --loop 60 > log/analyzer_nohup.log 2>&1 &

# Verbose mode
python3 src/analyzer.py --loop 60 --verbose
```

## Configuración

Editar `config/config.json` para customizar:

```json
{
  "candles": {
    "1m": 50,
    "5m": 30,
    "15m": 25,
    "1h": 10
  },
  "thresholds": {
    "imbalance_long": 0.2,
    "imbalance_short": -0.2,
    "delta_long": 50,
    "delta_short": -50,
    "vol_ratio_high": 0.7,
    "vol_ratio_low": 0.4
  },
  "signals": {
    "min_conditions_long": 3,
    "min_conditions_short": 3,
    "strong_conditions": 4
  }
}
```

## Backtesting

El script `backtest.py` compara las predicciones vs los precios reales posteriores:

```bash
python3 src/backtest.py
```

Genera estadísticas:
- Win rate por signal type
- P&L promedio
- Trades correctos/incorrectos
- Trades pendientes

## Logs

Todos los análisis se registran en:
- `log/analyzer.log` - Archivo
- Stdout - Consola

## Arquitectura Cross-platform

- Compatible con Linux, macOS, Windows (Git Bash)
- Paths relativos automáticos
- Detección automática de archivos
- Manejo de errores robusto

## Notas

- Los datos se leen de `../` (directorio neo/)
- Los resultados se guardan localmente en `datos/`
- Logs en `log/`
- El script es idempotente (se puede ejecutar múltiples veces)

## Procesos en Background

### Usar el script manager (recomendado)
```bash
cd neo/analizador

# Iniciar
./manage.sh start
./manage.sh start 30    # Con intervalo custom

# Ver estado
./manage.sh status

# Ver logs en tiempo real
./manage.sh logs

# Ver últimas N líneas
./manage.sh logs-tail 50

# Ver datos más recientes
./manage.sh data

# Parar
./manage.sh stop

# Reiniciar
./manage.sh restart
```

### Iniciar analyzer manualmente en background
```bash
cd neo/analizador
nohup python3 -u src/analyzer.py --loop 60 >/dev/null 2>&1 &
```

### Ver procesos corriendo
```bash
ps aux | grep analyzer.py
```

### Ver logs en tiempo real (mientras corre en background)
```bash
tail -f log/analyzer.log
```

### Matar el proceso
```bash
# Encontrar PID
ps aux | grep analyzer.py | grep -v grep

# Matar por PID
kill -9 <PID>

# O matar todos los analyzer.py
pkill -f "analyzer.py"
```

### Mantener corriendo después de desconectar SSH
```bash
# Con nohup (recomendado)
nohup python3 -u src/analyzer.py --loop 60 > log/analyzer_nohup.log 2>&1 &

# O con screen (si disponible)
screen -S analyzer -d -m python3 -u src/analyzer.py --loop 60
screen -S analyzer -ls  # Ver sesión
screen -r analyzer     # Reconectar
```

## Troubleshooting

**"Python not found"**
```bash
# Verificar instalación
python3 --version

# O usar python en lugar de python3
# Editar analyzer.py línea 1: #!/usr/bin/env python
```

**"Module not found"**
```bash
pip install -r requirements.txt
```

**"Permission denied"**
```bash
chmod +x run.sh
chmod +x src/analyzer.py
```

**Datos no actualizados**
- Verificar conexión NAS: `ls ../libro/datos/`
- Verificar permisos de lectura
- Ver log: `tail log/analyzer.log`

**Proceso se cuelga**
```bash
# Ver si el proceso está zombie
ps aux | grep analyzer.py

# Matar y reiniciar
pkill -9 analyzer.py
nohup python3 -u src/analyzer.py --loop 60 >/dev/null 2>&1 &
```
