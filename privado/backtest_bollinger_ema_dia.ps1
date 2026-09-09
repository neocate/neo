$bbs = @(
    @(20, 2.0),
    @(20, 2.5),
    @(14, 2.0)
)

$ema_dias = @(50)
$filtros = @("none", "short", "ambos")

$coins = @("BTC", "SOL", "ETH")

Write-Host "`n" -ForegroundColor Yellow
Write-Host "=== SCREENING: BOLLINGER BREAKOUT + FILTRO EMA DIARIA (none/short/ambos) ===" -ForegroundColor Yellow

$configs = @(
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
            foreach ($ema_dia in $ema_dias) {
                foreach ($filtro in $filtros) {
                    Write-Host "    BB $bbp/$bbstd | EMA dia $ema_dia | filtro $filtro" -ForegroundColor DarkMagenta -NoNewline
                    python .\backtest_bollinger.py --coin $coin --timeframe $tf --bb-period $bbp --bb-std $bbstd --ema-dia $ema_dia --ema-dia-filtro $filtro --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2020-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|ARRIBA|ABAJO|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
                }
            }
        }
    }
}

Write-Host "`n=== SCREENING COMPLETADO ===" -ForegroundColor Yellow
