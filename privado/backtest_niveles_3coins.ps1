# Combo validado (BB20/2.5 + EMA-diaria asimetrica + niveles, ambos "ambos"),
# 4h, OOS 2022-2026, en BTC/ETH/SOL. --niveles-filtro no se pasa por flag
# (default "ambos" en backtest_bollinger.py). El periodo de EMA-diaria SI se
# pasa explicito via $ema_dia_long/$ema_dia_short (no asumir que 20/50 va a
# seguir siendo el mejor para siempre — se probo tambien 50/50 simetrico y
# perdio en 1h para las 3 monedas y en 4h para SOL/ETH, solo ganaba en BTC 4h;
# si en el futuro un sweep de periodos da otro combo mejor, cambiar aca).
# Ver resultados_backtest.md, seccion "20/50 vs 50/50".
$ema_dia_long = 20
$ema_dia_short = 50
$coins = @("BTC", "ETH", "SOL")
$sl_tp = @(@(1.5,2.0), @(1.5,3.0), @(2.0,2.0), @(2.0,3.0))

Write-Host "=== BOLLINGER + EMA-DIARIA($ema_dia_long/$ema_dia_short,ambos) + NIVELES(ambos) | 4h | OOS 2022-2026 ===" -ForegroundColor Yellow
foreach ($coin in $coins) {
    Write-Host "`n=== $coin ===" -ForegroundColor Red
    foreach ($combo in $sl_tp) {
        $sl, $tp = $combo
        Write-Host "  SL ${sl}x TP ${tp}x" -ForegroundColor Cyan -NoNewline
        python .\backtest_bollinger.py --coin $coin --timeframe 4h --bb-period 20 --bb-std 2.5 --ema-dia $ema_dia_long --ema-dia-short $ema_dia_short --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2022-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback" | ForEach-Object { Write-Host " $_" }
    }
}
Write-Host "`n=== COMPLETADO ===" -ForegroundColor Yellow
