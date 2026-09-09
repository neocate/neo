$emas = @(
    @(9, 34),
    @(9, 42)
)

$coins = @("BTC", "SOL", "ETH")

Write-Host "`n" -ForegroundColor Yellow
Write-Host "=== SCREENING: SCALPING vs INTRADAY ===" -ForegroundColor Yellow

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

foreach ($coin in $coins) {
    Write-Host "`n=== $coin ===" -ForegroundColor Red

    # INTRADAY (15m-1h)
    Write-Host "`n--- INTRADAY (15m-1h) | ATR 14 ---" -ForegroundColor Magenta

    foreach ($config in $intra_configs) {
        $tf, $sl, $tp = $config
        Write-Host "`n  ▶ $tf | SL ${sl}x TP ${tp}x" -ForegroundColor Cyan
        foreach ($pair in $emas) {
            $ema1, $ema2 = $pair
            Write-Host "    EMA $ema1/$ema2" -ForegroundColor DarkMagenta -NoNewline
            python .\backtest_ema.py --coin $coin --timeframe $tf --ema1 $ema1 --ema2 $ema2 --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2022-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
        }
    }
}

Write-Host "`n=== SCREENING COMPLETADO ===" -ForegroundColor Yellow
