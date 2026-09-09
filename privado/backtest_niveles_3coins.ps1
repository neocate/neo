# Combo validado (BB20/2.5, EMA-diaria 20/50 ambos, 4h, OOS 2022-2026) + filtro
# de niveles de soporte/resistencia, en BTC/ETH/SOL.
$coins = @("BTC", "ETH", "SOL")
$sl_tp = @(@(1.5,2.0), @(1.5,3.0), @(2.0,2.0), @(2.0,3.0))

Write-Host "=== BOLLINGER + EMA-DIARIA(20/50,ambos) + NIVELES(ambos) | 4h | OOS 2022-2026 ===" -ForegroundColor Yellow
foreach ($coin in $coins) {
    Write-Host "`n=== $coin ===" -ForegroundColor Red
    foreach ($combo in $sl_tp) {
        $sl, $tp = $combo
        Write-Host "  SL ${sl}x TP ${tp}x" -ForegroundColor Cyan -NoNewline
        python .\backtest_bollinger.py --coin $coin --timeframe 4h --bb-period 20 --bb-std 2.5 --ema-dia 20 --ema-dia-short 50 --ema-dia-filtro ambos --niveles-filtro ambos --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2022-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback" | ForEach-Object { Write-Host " $_" }
    }
}
Write-Host "`n=== COMPLETADO ===" -ForegroundColor Yellow
