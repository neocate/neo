$emas = @(
    @(9, 18),
    @(12, 26),
    @(20, 50),
    @(50, 200),
    @(8, 17)
)

Write-Host "`n" -ForegroundColor Yellow
Write-Host "=== SCREENING: SCALPING vs INTRADAY ===" -ForegroundColor Yellow

# SCALPING (1m-5m)
Write-Host "`n--- SCALPING (1m-5m) | ATR 14 ---" -ForegroundColor Green

$scalp_configs = @(
    @("1m", 1.0, 1.5),
    @("1m", 1.0, 2.0),
    @("1m", 1.5, 1.5),
    @("1m", 1.5, 2.0),
	@("3m", 1.0, 1.5),
    @("3m", 1.0, 2.0),
    @("3m", 1.5, 1.5),
    @("3m", 1.5, 2.0),
    @("5m", 1.0, 1.5),
    @("5m", 1.0, 2.0),
    @("5m", 1.5, 1.5),
    @("5m", 1.5, 2.0)
)

foreach ($config in $scalp_configs) {
    $tf, $sl, $tp = $config
    Write-Host "`n  ▶ $tf | SL ${sl}x TP ${tp}x" -ForegroundColor Cyan
    foreach ($pair in $emas) {
        $ema1, $ema2 = $pair
        Write-Host "    EMA $ema1/$ema2" -ForegroundColor DarkCyan -NoNewline
        python .\backtest_simulator.py --coin BTC --timeframe $tf --ema1 $ema1 --ema2 $ema2 --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2026-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
    }
}

# INTRADAY (15m-1h)
Write-Host "`n--- INTRADAY (15m-1h) | ATR 14 ---" -ForegroundColor Magenta

$intra_configs = @(
    @("15m", 1.5, 2.0),
    @("15m", 1.5, 3.0),
    @("15m", 2.0, 2.0),
    @("15m", 2.0, 3.0),
    @("1h", 1.5, 2.0),
    @("1h", 1.5, 3.0),
    @("1h", 2.0, 2.0),
    @("1h", 2.0, 3.0),
	@("4h", 1.5, 2.0),
    @("4h", 1.5, 3.0),
    @("4h", 2.0, 2.0),
    @("4h", 2.0, 3.0)
)

foreach ($config in $intra_configs) {
    $tf, $sl, $tp = $config
    Write-Host "`n  ▶ $tf | SL ${sl}x TP ${tp}x" -ForegroundColor Cyan
    foreach ($pair in $emas) {
        $ema1, $ema2 = $pair
        Write-Host "    EMA $ema1/$ema2" -ForegroundColor DarkMagenta -NoNewline
        python .\backtest_simulator.py --coin BTC --timeframe $tf --ema1 $ema1 --ema2 $ema2 --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2024-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
    }
}

Write-Host "`n=== SCREENING COMPLETADO ===" -ForegroundColor Yellow
