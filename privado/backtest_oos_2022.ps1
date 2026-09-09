# Chequeo de robustez out-of-sample: re-corre los candidatos ya elegidos en
# el sweep 2020-2026 pero con --start 2022-01-01, para descartar que el
# resultado dependa del tramo 2020-2021 (sobre todo la salida inicial de
# SOL). No es un split train/test formal, es el paso barato antes de montarlo.

Write-Host "`n=== OOS 2022-01-01 -> hoy ===" -ForegroundColor Yellow

Write-Host "`n--- SOL 1h EMA9/34 (pendiente explicito de resultados_backtest.md) ---" -ForegroundColor Green
$sl_tp = @(@(1.5,2.0), @(1.5,3.0), @(2.0,2.0), @(2.0,3.0))
foreach ($combo in $sl_tp) {
    $sl, $tp = $combo
    Write-Host "  SL ${sl}x TP ${tp}x" -ForegroundColor Cyan -NoNewline
    python .\backtest_ema.py --coin SOL --timeframe 1h --ema1 9 --ema2 34 --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2022-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback" | ForEach-Object { Write-Host " $_" }
}

Write-Host "`n--- BTC/ETH Bollinger(20, 2.5) en 1h y 4h ---" -ForegroundColor Green
$coins = @("BTC", "ETH")
$tfs = @("1h", "4h")
foreach ($coin in $coins) {
    Write-Host "`n  === $coin ===" -ForegroundColor Red
    foreach ($tf in $tfs) {
        Write-Host "`n    $tf" -ForegroundColor Magenta
        foreach ($combo in $sl_tp) {
            $sl, $tp = $combo
            Write-Host "      SL ${sl}x TP ${tp}x" -ForegroundColor Cyan -NoNewline
            python .\backtest_bollinger.py --coin $coin --timeframe $tf --bb-period 20 --bb-std 2.5 --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2022-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback" | ForEach-Object { Write-Host " $_" }
        }
    }
}

Write-Host "`n=== OOS COMPLETADO ===" -ForegroundColor Yellow
