# ---------------------------------------------------------------
#  registro/csv_monitor.py - Cabecera y helpers de escritura del CSV que
#  graba monitor.py cada vuelta.
#
#  Extraido de monitor.py el 2026-08-04 (ver estrategia/senales.py para el
#  motivo).
# ---------------------------------------------------------------

import os
from datetime import datetime, timezone

CAMPOS_CSV = [
    # fecha_utc es la referencia canonica (evita el lio del cambio de hora);
    # fecha_local es la MISMA marca en la hora del reloj de la maquina, solo
    # para leer el CSV comodo - la consola y Telegram van en local.
    "timestamp_ms", "fecha_utc", "fecha_local", "coin", "precio", "rsi",
    "tendencia", "atr", "atr_pct",
    # Ancho de banda de Bollinger (% del precio), CADA vuelta, no solo al
    # abrir - junto a tendencia/atr_pct/arbitro_adx (ya grabados) es la
    # cuarta pieza de un futuro clasificador de regimen (direccion, fuerza,
    # dos medidas de volatilidad independientes). No decide nada todavia.
    "bollinger_ancho_pct",
    "cambio24h_pct", "max_rango", "min_rango", "senales", "senales_nuevas",
    # Los momentos en que el sistema decide NO actuar: hasta ahora eran
    # invisibles y resulta que son los importantes.
    "ambiguedad", "apertura_bloqueada", "desempate",
    # Enfriamiento tras cierre (2026-08-05): claves de SENALES_CONTINUACION
    # que siguen activas y en cooldown esta vuelta (no pudieron abrir por
    # eso) - vacio si no hay ninguna, ver cfg["enfriamiento_continuacion"].
    "enfriamiento_bloqueada",
    # Veto RVOL real (2026-08-07, ver cfg["rvol_veta"] y
    # herramientas/backtest_rvol_filtro.py): "SI" si una orden pendiente de
    # aceleracion_alza/baja iba a llenarse esta vuelta y NO se abrio porque
    # el RVOL de la vela de señal no confirma. impulso_* nunca lo pone.
    "veto_rvol",
    # Veto volatilidad real (2026-08-09, ver cfg["volatilidad_veta"] y
    # herramientas/backtest_filtros_combinados.py): "SI" si una orden
    # pendiente (impulso_* O aceleracion_*) iba a llenarse esta vuelta y NO
    # se abrio porque el ATR% de la vela de señal no confirma (< 0.12%).
    "veto_volatilidad",
    # La escalera: siguiente soporte/resistencia PROBADO por delante del
    # precio en cada intento de apertura, solo informativo. "escalera_bloqueada"
    # queda siempre vacia desde el 2026-08-01 (ver SENALES_CONTINUACION) - se
    # mantiene la columna para no romper el CSV en curso, no se borra.
    "nivel_escalera", "distancia_escalera_bps", "escalera_bloqueada",
    # TF arbitro: el DI decide el desempate, el ADX solo se registra (fallo en
    # los dos casos del 2026-07-29, hace falta mas muestra antes de fiarse).
    "arbitro_tf", "arbitro_adx", "arbitro_di_mas", "arbitro_di_menos",
    # Libro: se graba en CADA vuelta, haya posicion o no.
    "bid", "ask", "spread_bps",
    # Orden LIMITE pendiente (VETO_TF.md sec. 5, 2026-07-30): entre la señal
    # y la posicion. Vacio si no hay ninguna en curso para esta moneda/rama.
    "orden_pendiente_lado", "orden_pendiente_precio", "orden_pendiente_motivo",
    "orden_pendiente_desde", "orden_pendiente_vueltas",
    "posicion_lado", "posicion_entrada", "posicion_entrada_tipo",
    "posicion_cantidad", "posicion_margen",
    "posicion_nocional", "posicion_stop", "posicion_stop_origen",
    "posicion_riesgo_bps", "posicion_objetivo",
    # pnl_* son BRUTOS (solo precio). Los *_neto descuentan las DOS comisiones:
    # la de entrada (ya pagada) y la de salida (estimada al precio actual).
    "pnl_usdt", "pnl_pct_margen", "pnl_neto_usdt", "pnl_neto_pct_margen",
    "comision_entrada_usdt",
    "cierre_pnl_usdt", "cierre_pnl_pct",
    "cierre_comision_usdt", "cierre_pnl_neto_usdt", "cierre_pnl_neto_pct",
    "motivo_cierre",
    "imbalance", "imb_racha",
    "evento", "capital_actual",
    # filtro_btc/soporte/confluencia/bollinger se eliminaron (2026-08-1x,
    # ver estrategia/filtros.py): confluencia daba 0/30 True por diseño
    # desde SENALES_CONTINUACION; los otros tres empeoraron el resultado SIN
    # EXCEPCION al promoverse a veto en backtest_filtros_combinados.py (9
    # años BTC+ETH) - con la pregunta ya contestada, seguir grabandolos como
    # sombra no aportaba nada. Un CSV en curso con la cabecera VIEJA no se
    # corrompe por esto: _ruta_compatible() ya rota a un _v2 solo.
    "id_apertura", "filtro_volatilidad_veredicto", "filtro_volatilidad_valor",
    "filtro_volumen_veredicto", "filtro_volumen_valor",
    "filtro_funding_veredicto", "filtro_funding_valor",
    "filtro_open_interest_veredicto", "filtro_open_interest_valor",
    # cvd (2026-08-1x): tendencia del order flow acumulado sobre la ventana
    # de señal, leida de flujo_*.csv (grabador_libro.py) - ver
    # estrategia/filtros.py:FiltroCVD. Sombra, igual que funding/open_interest.
    "filtro_cvd_veredicto", "filtro_cvd_valor",
    # A diferencia de los filtros de arriba (foto de la vela de señal, solo
    # en la fila de apertura), este se recalcula EN CADA vuelta mientras hay
    # posicion abierta - ver PosicionSim.mfe_pct/mfe_dt. Vacio hasta los 30
    # min de la posicion (antes de eso, cualquier lectura es ruido). Deja de
    # ser solo registro (2026-08-1x): si estancado, mueve el stop a
    # breakeven - ver el bloque en monitor.py._revisar.
    "filtro_estancamiento_veredicto", "filtro_estancamiento_valor",
    "id_cierre",
]


def _archivo_registro(cfg):
    """monitor_<fecha_UTC>_<monedas>_<tf>.csv - un fichero por ejecucion,
    para poder correr varios monitor.py a la vez (distintas monedas/tf) sin
    que se pisen. Igual de gitignored que flujo_*.csv/historico_*.csv.

    Hasta el 2026-08-06 el nombre llevaba ademas un sufijo _veto/_sma para
    no mezclar las series de las 2-3 ramas que corrian en paralelo sobre el
    mismo par/tf (ver anotaciones.md, "Rama unica") - desde que monitor.py
    solo corre una configuracion fija, ya no hace falta distinguir modo."""
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    monedas = "-".join(cfg["coins"])
    return f"monitor_{fecha}_{monedas}_{cfg['tf']}.csv"


def _ruta_compatible(ruta, campos_csv=CAMPOS_CSV):
    """Si el CSV ya existe pero con OTRA cabecera (la escribio una version
    anterior del monitor, con un CAMPOS_CSV distinto), NO se puede seguir
    escribiendo en el: se abre en modo append y DictWriter volcaria las
    columnas NUEVAS debajo de la cabecera VIEJA, dejando el archivo
    desalineado y sin avisar de nada. En ese caso se abre uno nuevo con
    sufijo _v2, _v3...

    'campos_csv' por defecto es el de monitor.py; el parametro queda
    disponible por si otro consumidor necesita su propia cabecera."""
    if not os.path.exists(ruta):
        return ruta
    with open(ruta, newline="", encoding="utf-8") as f:
        primera = f.readline().strip()
    if primera == ",".join(campos_csv):
        return ruta
    base, ext = os.path.splitext(ruta)
    n = 2
    while os.path.exists(f"{base}_v{n}{ext}"):
        n += 1
    nueva = f"{base}_v{n}{ext}"
    print(f"(aviso) {ruta} lo escribio otra version del monitor (cabecera distinta).")
    print(f"        Para no corromperlo, esta sesion va a {nueva}")
    return nueva


def _registrar(writer, arch, fila):
    """Escribe una fila y hace flush (sobrevive a un corte/QuickEdit, igual
    que runner_flujo.py)."""
    writer.writerow(fila)
    arch.flush()
