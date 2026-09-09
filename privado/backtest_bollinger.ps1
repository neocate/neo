$bbs = @(
    @(20, 2.0),
    @(20, 2.5),
    @(14, 2.0)
)

$coins = @("BTC", "SOL", "ETH")

Write-Host "`n" -ForegroundColor Yellow
Write-Host "=== SCREENING: BOLLINGER BREAKOUT ===" -ForegroundColor Yellow

$configs = @(
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

    foreach ($config in $configs) {
        $tf, $sl, $tp = $config
        Write-Host "`n  ▶ $tf | SL ${sl}x TP ${tp}x" -ForegroundColor Cyan
        foreach ($bb in $bbs) {
            $bbp, $bbstd = $bb
            Write-Host "    BB $bbp/$bbstd" -ForegroundColor DarkMagenta -NoNewline
            python .\backtest_bollinger.py --coin $coin --timeframe $tf --bb-period $bbp --bb-std $bbstd --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2022-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
        }
    }
}

Write-Host "`n=== SCREENING COMPLETADO ===" -ForegroundColor Yellow
