$cvd_emas = @(
    @(9, 26),
    @(9, 34),
    @(9, 42),
    @(12, 26)
)

$timeframes = @("1m", "15m", "1h")

Write-Host "`n" -ForegroundColor Yellow
Write-Host "=== SCREENING: FLUJO (CVD) ===" -ForegroundColor Yellow

$configs = @(
    @(1.5, 2.0),
    @(1.5, 3.0),
    @(2.0, 2.0),
    @(2.0, 3.0)
)

foreach ($tf in $timeframes) {
    Write-Host "`n=== $tf ===" -ForegroundColor Red

    foreach ($config in $configs) {
        $sl, $tp = $config
        Write-Host "`n  ▶ SL ${sl}x TP ${tp}x" -ForegroundColor Cyan
        foreach ($ema in $cvd_emas) {
            $e1, $e2 = $ema
            Write-Host "    CVD EMA $e1/$e2" -ForegroundColor DarkMagenta -NoNewline
            python .\backtest_flujo.py --coin ETH --timeframe $tf --cvd-ema1 $e1 --cvd-ema2 $e2 --atr-period 14 --sl-atr $sl --tp-atr $tp 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
        }
    }
}

Write-Host "`n=== SCREENING COMPLETADO ===" -ForegroundColor Yellow
