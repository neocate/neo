$bbs = @(
    @(20, 2.0),
    @(20, 2.5),
    @(14, 2.0)
)

# Compara el filtro "ambos" con un solo periodo (EMA50, ya validado) contra
# la version asimetrica (EMA corta para LONG, mas larga para SHORT), que fue
# la mejor combinacion en promedio sobre las 36 combinaciones del sweep
# anterior (backtest_bollinger_ema_dia_periodos.ps1).
$variantes = @(
    @(50, 50),   # baseline: mismo periodo para ambos lados
    @(20, 50),
    @(20, 100)
)

$coins = @("BTC", "SOL", "ETH")

Write-Host "`n" -ForegroundColor Yellow
Write-Host "=== SCREENING: FILTRO AMBOS - PERIODO UNICO vs ASIMETRICO (LONG/SHORT) ===" -ForegroundColor Yellow

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
            foreach ($variante in $variantes) {
                $ema_long, $ema_short = $variante
                Write-Host "    BB $bbp/$bbstd | ambos EMA long=$ema_long / short=$ema_short" -ForegroundColor DarkMagenta -NoNewline
                python .\backtest_bollinger.py --coin $coin --timeframe $tf --bb-period $bbp --bb-std $bbstd --ema-dia $ema_long --ema-dia-short $ema_short --ema-dia-filtro ambos --atr-period 14 --sl-atr $sl --tp-atr $tp --start "2020-01-01" 2>&1 | Select-String "Entradas|Equity final|Win rate|Expectativa|Profit factor|AVISO|Error|Traceback|FileNotFoundError" | ForEach-Object { Write-Host " $_" }
            }
        }
    }
}

Write-Host "`n=== SCREENING COMPLETADO ===" -ForegroundColor Yellow
