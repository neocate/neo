@echo off
REM Barrido inicial de ETH para todos los timeframes
REM Guarda en: herramientas/niveles/ETH/

echo === Barrido inicial ETH - Todos los TF ===
echo.

python herramientas/niveles.py eth 1d --k 3 --tolerancia-atr 0.25 --toques-min 3
echo [OK] ETH 1d
echo.

python herramientas/niveles.py eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3
echo [OK] ETH 4h
echo.

python herramientas/niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3
echo [OK] ETH 1h
echo.

python herramientas/niveles.py eth 15m --k 3 --tolerancia-atr 0.25 --toques-min 3
echo [OK] ETH 15m
echo.

python herramientas/niveles.py eth 5m --k 3 --tolerancia-atr 0.25 --toques-min 3
echo [OK] ETH 5m
echo.

python herramientas/niveles.py eth 5m --k 3 --tolerancia-atr 0.25 --toques-min 3
echo [OK] ETH 3m
echo.

echo === Barrido completado ===
echo Archivos guardados en: herramientas/niveles/ETH/
pause
