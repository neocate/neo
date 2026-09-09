$bbs = @(
    @(20, 2.0),
    @(20, 2.5),
    @(14, 2.0)
)

# Periodos de EMA diaria a comparar. Se corre cada uno aislado por lado
# (filtro long y filtro short por separado) para ver si conviene un periodo
# distinto para cada direccion, en vez de forzar el mismo para ambas.
$ema_dias = @(20, 50, 100, 200)
$filtros = @("long", "short")

$coins = @("BTC", "SOL", "ETH")

Write-Host "`n" -ForegroundColor Yellow
Write-Host "=== SCREENING: BOLLINGER + EMA DIARIA POR PERIODO (aislado LONG vs SHORT) ===" -ForegroundColor Yellow

# Solo 4h: es donde se vio el edge en los screenings anteriores. Para sumar
# 1h de nuevo, agregar esas combinaciones a este array.
$configs = @(
    @("4h", 1.5, 2.0),
    @("4h", 1.5, 3.0),
    @("4h", 2.0, 2.0),
    @("4h", 2.0, 3.0)
)

foreach ($coin in $coins) {
    Write-Host "`n=== $coin ===" -ForegroundColor Red

    foreach ($config in $configs) {
        $tf, $sl, $tp = $config
        Write-Host "`n  ▶ $tf | SL ${sl}x TP ${tp}x" -ForegroundColor Cyan
        foreach ($bb in $bbs) {
            $bbp, $bbstd = $bb
            foreach ($filtro in $filtros) {
                foreach ($ema_dia in $ema_dias) {
                    Write-Host "    BB $bbp/$bbstd | filtro $filtro | EMA dia $ema_dia" -ForegroundColor DarkMagenta -NoNewline
                    python .\backtest_bollinger.py --coin $coin --timeframe $tf --bb-period $bbp --bb-std $bbstd --ema-dia $ema_dia --ema-dia-filtro $filtro --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2020-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|ARRIBA|ABAJO|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
                }
            }
        }
    }
}

Write-Host "`n=== SCREENING COMPLETADO ===" -ForegroundColor Yellow
