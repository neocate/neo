# ----------------------------------------------------------------------
#  monitor.py  -  Vigila una moneda y avisa de posibles CAMBIOS de precio
#
#  NO dice "compra" ni "vende". Detecta cuando la ultima vela cerrada sugiere
#  un cambio (ruptura, rechazo, divergencia RSI, agotamiento) y, ademas,
#  SIMULA EN PAPEL (sin dinero real) una posicion en la direccion de la
#  señal, para poder ver que habria pasado despues - la parte que antes
#  faltaba: el aviso salia y ahi se acababa la informacion.
#
#  Puedes vigilar UNA o VARIAS monedas (separadas por coma, sin espacios):
#         python monitor.py icp
#         python monitor.py icp,sol,link
#
#  Dos modos:
#    1) Foto puntual (una vez):
#         python monitor.py icp,sol
#    2) Vigilancia continua (avisa por Telegram cuando aparece una senal):
#         python monitor.py icp,sol,link --loop
#
#  Opciones:
#    --tf 4h              timeframe de las velas (default 4h; swing 4h o 1d)
#    --ventana 30         nº de velas para medir el rango (max/min) reciente
#    --cada 15            en --loop, minutos entre revisiones (default 15;
#                         admite decimales, ej. 0.5 = 30s)
#    --rsi-bajo 30        umbral RSI sobreventa (default 30)
#    --rsi-alto 70        umbral RSI sobrecompra (default 70)
#    --capital 50         capital de PAPEL por moneda, en USDT (default 50)
#    --fraccion-entrada 0.02   % del capital que se usa de MARGEN por entrada (default 2%)
#    --leverage 10        multiplicador del margen -> nocional (default 10x)
#    --imb-umbral 0.3     |imbalance| minimo para contar como "fuerte" (default 0.3)
#    --imb-confirmaciones 2    vueltas seguidas de imbalance fuerte a favor
#                              para avisar CONTINUACION (default 2)
#
#  SEÑALES que detecta (sobre la ULTIMA VELA CERRADA):
#    * Impulso al alza / a la baja: movimiento neto de --impulso-lookback
#      velas supera --impulso-min-atr x ATR -> la unica familia que ABRE.
#    * Ruptura al alza / a la baja: cierra fuera del rango de la ventana.
#    * Rechazo en maximo/minimo: pincha fuera del rango pero cierra dentro
#      (mecha de rechazo) -> posible reversion.
#    * Divergencia RSI: nuevo extremo de precio que el RSI no acompaña.
#    * RSI en sobrecompra/sobreventa girando -> agotamiento.
#
#  En --loop solo avisa cuando una senal APARECE (no estaba en la vuelta
#  anterior), para no repetir el mismo aviso cada 15 min.
#
#  SIMULACION DE POSICION (papel, SIN dinero real):
#    Cada señal implica una direccion (largo/corto), pero solo impulso_alza/
#    baja puede ABRIR - ver SENALES_CONTINUACION (estrategia/senales.py) y
#    seccion AMBIGUEDAD mas abajo. Si la moneda esta SIN posicion y aparece
#    una de esas, se abre una posicion de papel: margen = capital * fraccion,
#    nocional = margen * leverage, cantidad = nocional / precio. El STOP es
#    estructural: el minimo (largo) o maximo (corto) de la propia vela que
#    disparo la señal - si se rompe, la tesis de la señal queda invalidada.
#    Se cierra por stop tocado o por una señal de la direccion contraria.
#    El capital de cada moneda ES INDEPENDIENTE y compone con el P&L
#    realizado (no es un numero fijo en cada entrada).
#
#  TODO EN UNIDADES DE ATR (--stop-atr): el stop es el extremo de la vela de
#    la señal MAS un colchon de N veces el ATR de esa vela. El extremo pelado
#    daba un riesgo arbitrario (5 bps en una vela pequeña, 80 en una grande) y
#    con 29 bps de MAE medio los trades morian por ruido, no porque la tesis
#    se rompiera. La tolerancia de FiltroSoporte tambien paso a ATR: con el
#    0.15% fijo que tenia, la banda medía lo mismo que una vela entera y el
#    filtro decia que SI el 85% de las veces.
#
#  AMBIGUEDAD DE SEÑALES y ARBITRO (--tf-arbitro, --di-separacion):
#    Las señales son de dos familias que se contradicen POR CONSTRUCCION: en
#    una caida fuerte el precio rompe el minimo (continuacion) Y el RSI se va
#    a sobreventa (reversion) a la vez. Al anularse en _direccion(), el
#    sistema se quedaba quieto justo en el movimiento y solo operaba despues,
#    cuando quedaba la señal de reversion sola -> sesgo sistematico a operar
#    CONTRA el movimiento. Medido en ETH 5m el 2026-07-29: los 2 cortos
#    anulados habrian dado +74 y +52 bps; los 4 largos que si se operaron no
#    pasaron de +35 en su MEJOR momento y murieron los 4 en stop.
#
#    Ahora el empate lo resuelve la DIRECCION del DI+/DI- del TF arbitro
#    (automatico, el primero mas lento que --tf - ver _ARBITRO_AUTO); si la
#    separacion entre ambos no llega a --di-separacion, se
#    sigue sin operar. Esa direccion manda ademas sobre CUALQUIER apertura (y
#    sobre el cierre por señal contraria), no solo sobre los empates - pero
#    SOLO en la rama 'veto': el proceso corre SIEMPRE las dos ramas a la vez
#    ('libre', que solo anota, y 'veto', que obedece), sobre la MISMA foto de
#    mercado, para compararlas sin ruido de sincronizacion entre procesos
#    (2026-07-30). El arbitro se consulta SIEMPRE y se graba en las dos,
#    cacheado por vela, para poder calcular el contrafactual offline. El ADX
#    se registra pero NO decide: ese dia marcaba RANGO en plena caida.
#
#    Actualizacion 2026-08-01: la causa de fondo no era solo que el empate se
#    resolviera mal, era que las señales de reversion (rechazo/div/rsi) llegan
#    tarde por construccion -solo pueden dispararse cuando el movimiento ya
#    esta girando o aplanandose, nunca durante el- y aun asi podian ABRIR.
#    Medido en los CSV de julio: 15 de 21 aperturas reales eran de reversion,
#    y algunas tardaron hasta 3.6h en llenarse (la orden limite espera a que
#    el precio la rebase, no la toque). Ahora SENALES_CONTINUACION es la unica
#    familia que abre; las demas siguen cerrando una posicion contraria ya
#    abierta (via _direccion(nuevas_claves)), pero no inician una nueva. La
#    ambiguedad de apertura casi desaparece con esto (solo puede darse si
#    impulso_alza Y impulso_baja se disparan en la misma vela).
#
#    Actualizacion 2026-08-04: SENALES_CONTINUACION pasa de ruptura_alza/baja
#    a impulso_alza/baja (ver estrategia/senales.py) - reconstruyendo con
#    velas REALES de Bitget se vio que ruptura_* (rompe un rango de
#    --ventana velas) dispara cuando el precio YA se movio +22 a +28 bps de
#    media en los 30 min previos, recorrido que se devuelve casi siempre
#    (retorno a 30 min negativo en todos los tamaños de ventana probados).
#    impulso_* dispara sobre el momentum reciente (--impulso-lookback velas,
#    --impulso-min-atr x ATR) en vez de la ruptura de rango - backtesteado
#    (no en vivo todavia) con la operacion completa (stop en el extremo del
#    tramo, comision real, trailing por giveback): positivo en TODOS los
#    --impulso-lookback probados (2 a 10 velas) para --impulso-min-atr=3.0,
#    ver anotaciones.md. ruptura_alza/baja se sigue calculando y puede
#    cerrar una posicion contraria, ya no abre.
#
#  COMISIONES (se descuentan de verdad, --comision-maker/--comision-taker):
#    El papel paga comision en las DOS patas, como en real. La SALIDA siempre
#    es TAKER (un stop se ejecuta a mercado); la ENTRADA es MAKER si se lleno
#    en limite o TAKER si escalo a mercado (ver OrdenPendiente) - punto muerto
#    8 o 12 bps de movimiento de precio (0,8% o 1,2% del margen con leverage
#    10) segun cual fue. Antes del 30-jul TODA entrada era taker: sobre 151
#    trades medidos en julio de 2026 el resultado medio fue ~+0,53% del margen
#    contra 1,2% de coste - el papel decia que ganaba porque no las
#    descontaba. El capital compone con el pnl NETO. Las columnas pnl_* siguen
#    siendo brutas y las pnl_neto_*
#    llevan las dos comisiones (la de entrada, ya pagada, y la de salida,
#    estimada al precio actual sobre el nocional de salida).
#
#  RESUMEN PERIODICO A TELEGRAM (--resumen-cada, minutos; 0 = desactivado):
#    Cada N minutos (60 por defecto) manda la foto de conjunto que los avisos
#    sueltos no dan: valor de la cartera (realizado y EQUITY = realizado + lo
#    no realizado neto de lo que sigue abierto), posiciones abiertas con su
#    pnl neto y su stop, operaciones cerradas desde el resumen anterior con el
#    motivo y el desglose bruto/comision/neto, y el estado actual de cada
#    moneda (precio, RSI, tendencia, spread y señales activas). Se manda
#    tambien cuando NO ha pasado nada: es justo cuando interesa saber que el
#    proceso sigue vivo y que no ha operado.
#
#  CONTINUACION via ORDER FLOW (imbalance del libro en vivo):
#    Mientras hay una posicion de papel abierta, cada vuelta se consulta el
#    libro real y se mide el imbalance. Si se mantiene FUERTE (>= umbral) Y
#    A FAVOR de la posicion durante --imb-confirmaciones vueltas seguidas,
#    se avisa de que el order flow CONFIRMA la continuacion del movimiento.
#    OJO METODO: el imbalance es una señal de segundos (ver analizar_flujo.py
#    / README); revisarlo cada --cada minutos es una foto suelta, no un
#    seguimiento continuo - esto es una confirmacion adicional, no una prueba.
#
#  REGISTRO CSV (una fila por VUELTA DE REVISION, no solo por evento):
#    Se graba en monitor_<fecha_UTC>_<monedas>_<tf>.csv (gitignored, igual que
#    flujo_*.csv/historico_*.csv) - un archivo por ejecucion (moneda+tf), para
#    poder correr varios monitor.py a la vez sin que se pisen. Cada vuelta de
#    cada moneda es una fila:
#    precio, RSI, señales activas/nuevas, estado de la posicion de papel
#    (lado, entrada, stop, pnl no realizado bruto y neto), pnl REALIZADO si
#    cerro algo esta vuelta (bruto, comision y neto) con el MOTIVO del cierre
#    (stop / senal_contraria), el mejor BID/ASK y el spread del libro,
#    imbalance/racha si habia posicion, y que EVENTO paso.
#
#    OJO al parsear: la columna 'evento' puede ser COMPUESTA ('cierre;apertura'
#    cuando una posicion se cierra y otra se abre en la misma vuelta). Hay que
#    filtrar con "cierre" in evento, NUNCA con evento == "cierre" - con == se
#    pierde la mayoria de los trades. Y en una fila de cierre las columnas
#    posicion_*/pnl_* describen la posicion NUEVA; el resultado de la que se
#    cerro esta en cierre_*.
#
#    El BID/ASK se graba en CADA vuelta aunque no haya posicion: es lo que
#    permite medir despues, offline, si una orden LIMITE al mejor bid/ask se
#    habria llenado (¿el precio rebaso ese nivel?) - sin cambiar todavia como
#    entra el monitor. De esa pregunta depende que el punto muerto sea 12 bps
#    o 8, que es lo que decide si un TF rapido es viable.
#
#  VALIDACION EXTERNA (ver estrategia/filtros.py):
#    Al ABRIR cada posicion de papel se evaluan 5 filtros. volatilidad (ATR%
#    de la vela) y volumen (RVOL de la vela, solo veta aceleracion_alza/baja)
#    son VETO REAL, ver cfg["volatilidad_veta"]/["rvol_veta"] mas abajo - se
#    promovieron con backtest de escala (9 años y 29k trades respectivamente).
#    funding (posicionamiento agregado del mercado de derivados via funding
#    rate del perpetuo), open_interest (si la ruptura viene con OI creciente
#    = dinero nuevo, o plano/decreciente = cierre de posiciones contrarias) y
#    cvd (tendencia del order flow acumulado sobre la ventana de señal) son
#    SOMBRA - no deciden nada, solo dejan constancia de si ELLOS habrian
#    ejecutado esa misma señal, para poder comparar el PnL real despues antes
#    de considerar promoverlos (mismo tratamiento que tuvieron volatilidad/
#    volumen antes de su backtest). Se graban en la fila de apertura con un
#    id_apertura; la fila de cierre lleva el mismo id en id_cierre para poder
#    cruzar despues, offline, el PnL real contra el veredicto de cada filtro
#    (columnas filtro_*). Hay un sexto, estancamiento (mas abajo), que es
#    aparte: no usa id_apertura/id_cierre, se recalcula cada vuelta mientras
#    la posicion sigue abierta - y a diferencia de los demas SI actua (mueve
#    el stop a breakeven si estancado y el precio sigue del lado bueno de la
#    entrada), no es solo registro.
#    FiltroConfluencia (2026-08-02), FiltroSoporte/FiltroBollinger/FiltroBTC
#    (2026-08-1x) se ELIMINARON: 0/30 True el primero (con SENALES_CONTINUACION,
#    2 señales por lado mutuamente excluyentes, confluencia>=2 es casi
#    imposible por diseño) y los otros tres empeoraron el resultado SIN
#    EXCEPCION al promoverlos a veto en backtest_filtros_combinados.py (9
#    años BTC+ETH) - con la pregunta ya contestada, seguir grabandolos como
#    sombra no aporta nada nuevo. open_interest/cvd usan ahora
#    flujo_<fecha>_<monedas>.csv (herramientas/grabador_libro.py, proceso
#    aparte) en vez de pedir los datos por su cuenta - ver
#    mercado/lectura.py:_serie_flujo.
# ----------------------------------------------------------------------

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

from alertas import avisos
from estrategia.senales import (SENALES_LARGO, SENALES_CORTO, SENALES_CONTINUACION,
                                 _direccion, _ambiguedad)
from estrategia.escalera import _proximo_nivel
from estrategia.contexto import _tf_minutos, _tf_arbitro_auto, _tf_vecino_auto, _arbitro, _regimen, _vecino_senales
from mercado.lectura import leer, formatear
from posicion.posicion import OrdenPendiente, _abrir, _libro_favorece, _gestionar_posicion
from registro.csv_monitor import CAMPOS_CSV, _archivo_registro, _ruta_compatible, _registrar

# NOTA (2026-08-04): este archivo se "adelgazo" repartiendo la mayoria de sus
# funciones a modulos por categoria - ver estrategia/senales.py,
# estrategia/escalera.py, estrategia/contexto.py, mercado/lectura.py,
# posicion/posicion.py, registro/csv_monitor.py. monitor.py se queda solo con
# lo que le es propio: parseo de argumentos, el bucle de decision (_revisar)
# y la orquestacion (main).


# Parametros tocables EN CALIENTE desde Telegram (telegram_control.py,
# 2026-08-03): mismos flags que la CLI, salvo los estructurales que ya se
# resolvieron una vez al arrancar y cambiarlos en marcha no tendria efecto
# limpio (tf, coins, loop - definen el proceso; di_veta/regimen_veta -
# fijos a True desde el 2026-08-07, ver mas abajo, cambiarlos a mano
# dejaria la rama en un estado no probado). 'cfg' es un dict que TODAS las
# funciones consultan en vivo cada vuelta (no se copia a variables locales
# al arrancar), asi que mutar estas claves en marcha SI tiene efecto real
# sin reiniciar - ver _revisar()/_arbitro()/_regimen() etc.
PARAMS_AJUSTABLES = {
    "posiciones_max": (int, "Nº maximo de posiciones de papel CONCURRENTES "
                       "por moneda/rama (2026-08-10, antes era 1 sola fija). "
                       "Una señal nueva en direccion OPUESTA a alguna ya "
                       "abierta no apila un hedge - no abre hasta que esa(s) "
                       "se cierren (ver _revisar())."),
    "ventana": (int, "Nº de velas cerradas para el rango (max/min) que "
                "detecta ruptura_alza/baja."),
    "cada": (float, "Minutos entre vueltas de sondeo (sin orden pendiente)."),
    "rsi_bajo": (float, "Umbral de sobreventa del RSI (rsi_sobreventa)."),
    "rsi_alto": (float, "Umbral de sobrecompra del RSI (rsi_sobrecompra)."),
    "fraccion_entrada": (float, "Fraccion del capital que se arriesga como "
                         "margen en cada apertura NUEVA (no toca posiciones "
                         "ya abiertas). Por Telegram se escribe como "
                         "PORCENTAJE (2 = 2%, ver PARAMS_PORCENTAJE_HUMANO "
                         "en telegram_control.py); por linea de comandos "
                         "(--fraccion-entrada) sigue siendo la fraccion "
                         "cruda (0.02)."),
    "leverage": (float, "Apalancamiento del margen a nocional en aperturas "
                 "NUEVAS."),
    "imb_umbral": (float, "Imbalance del libro (-1 a 1) que cuenta como "
                   "'a favor' para escalar a mercado / continuacion."),
    "imb_confirmaciones": (int, "Vueltas seguidas de imbalance a favor "
                           "antes de avisar 'continuacion'."),
    "comision_maker": (float, "% comision de entrada limite, usada en el "
                       "PnL neto de aperturas NUEVAS."),
    "comision_taker": (float, "% comision de mercado/salida, usada en el "
                       "PnL neto de aperturas NUEVAS."),
    "stop_atr": (float, "Colchon del stop inicial, en multiplos del ATR de "
                 "la vela de señal. 0 = stop crudo en el extremo."),
    "tf_arbitro": (str, "TF mas lento para el DI de desempate/veto ('-' lo "
                  "desactiva)."),
    "di_separacion": (float, "Separacion minima DI+/DI- del arbitro para "
                      "que de una direccion (si no, se abstiene)."),
    "escalera_ventana": (int, "Nº de velas cerradas donde buscar niveles "
                         "probados para la escalera de trailing."),
    "escalera_tolerancia_atr": (float, "Tolerancia (en ATR) para contar un "
                                "toque de nivel en la escalera."),
    "escalera_toques": (int, "Toques minimos dentro de la ventana para que "
                        "un nivel cuente como 'probado'."),
    "escalera_min_atr": (float, "Distancia minima (en ATR) del objetivo de "
                         "la escalera respecto al precio."),
    "trailing_giveback": (float, "Fraccion del recorrido ganado (desde la "
                          "entrada hasta el pico) que se deja entre el stop "
                          "y el pico una vez armado. 0 = usa la escalera de "
                          "siempre en vez de esto."),
    "trailing_armado_atr": (float, "Ganancia minima (en ATR de la vela de "
                            "señal) antes de que el trailing por giveback "
                            "empiece a perseguir el precio."),
    "impulso_lookback": (int, "Nº de velas cerradas hacia atras para medir "
                        "el movimiento neto de impulso_alza/baja."),
    "impulso_min_atr": (float, "Movimiento minimo (en ATR) en esas velas "
                       "para que dispare impulso_alza/baja - la señal que "
                       "ABRE (sustituye a ruptura_alza/baja, ver "
                       "estrategia/senales.py)."),
    "aceleracion_ventana": (int, "Nº de velas cerradas (incluida la de "
                            "disparo) para medir el ritmo previo de "
                            "aceleracion_alza/baja."),
    "aceleracion_min_atr": (float, "Movimiento minimo (en ATR) de la ULTIMA "
                           "vela para que aceleracion_alza/baja pueda "
                           "disparar - señal nueva, sin backtest, corre en "
                           "paralelo a impulso_alza/baja (ver "
                           "estrategia/senales.py)."),
    "aceleracion_mult": (float, "Cuantas veces el ritmo medio de las velas "
                        "anteriores tiene que ser la ultima vela para "
                        "contar como aceleracion real."),
    "orden_max_velas": (float, "Velas de --tf que espera una orden limite "
                       "sin llenarse antes de cancelarse (0 = sin limite)."),
    "cada_orden_pendiente": (float, "Minutos entre vueltas MIENTRAS hay "
                            "alguna orden pendiente (sondeo acelerado)."),
    "orden_espera_mercado": (float, "Fraccion de vela de --tf que espera una "
                            "orden pendiente antes de decidir escalar a "
                            "mercado o cancelar (0 = desactiva la decision)."),
    "tf_vecino_rapido": (str, "TF mas rapido para adelantar aperturas antes "
                        "de que cierre la propia vela ('-' lo desactiva)."),
    "regimen_tf": (str, "TF de las velas donde se mide el regimen de fondo "
                  "(precio vs su SMA) - '1d' por defecto ('-' lo desactiva)."),
    "regimen_sma": (int, "Periodo de la SMA del regimen de fondo."),
    "exit_rr": (float, "Objetivo FIJO a este multiplo del riesgo inicial, en "
               "vez de la escalera/breakeven/señal contraria. 0 = "
               "desactivado (usa la escalera de siempre)."),
}


def _parse_args(argv):
    """Lee los argumentos de linea de comandos a un dict simple."""
    if not argv:
        print("Uso: python monitor.py <coin[,coin2,...]> [--loop] [--tf 4h] "
              "[--posiciones-max 3] "
              "[--ventana 30] [--cada 15] [--rsi-bajo 30] [--rsi-alto 70] "
              "[--capital 50] [--fraccion-entrada 0.02] [--leverage 10] "
              "[--imb-umbral 0.3] [--imb-confirmaciones 2] "
              "[--comision-maker 0.02] [--comision-taker 0.06] "
              "[--resumen-cada 60] [--stop-atr 1.0] "
              "[--tf-arbitro auto] [--di-separacion 5] "
              "[--escalera-ventana 150] [--escalera-tolerancia-atr 0.25] "
              "[--escalera-toques 6] [--escalera-min-atr 2.0] "
              "[--orden-max-velas 3] [--cada-orden-pendiente 0.25] "
              "[--orden-espera-mercado 0.2] [--tf-vecino-rapido auto] "
              "[--aceleracion-ventana 6] [--aceleracion-min-atr 0.8] "
              "[--aceleracion-mult 2.5] [--sin-enfriamiento] [--sin-rvol-veta] "
              "[--sin-volatilidad-veta]")
        print("  --tf-arbitro por defecto es AUTOMATICO (el primero mas lento que --tf,")
        print("  ver _ARBITRO_AUTO) - pasa un TF explicito para fijarlo, o '-' para apagarlo.")
        print("  --orden-max-velas cancela una orden limite que lleve mas de N velas de --tf")
        print("  sin llenarse (0 = esperar para siempre).")
        print("  --cada-orden-pendiente (minutos, admite decimales): mientras haya alguna")
        print("  orden pendiente en cualquier moneda/rama, el bucle duerme esto en vez de")
        print("  --cada - el sondeo lento normal es la ventana donde se pierde el recorrido.")
        print("  --orden-espera-mercado (fraccion de vela de --tf, no minutos fijos - 0.2 =")
        print("  1/5 de vela por defecto): pasado esto sin llenarse, escala a mercado si el")
        print("  libro sigue a favor (--imb-umbral) o cancela si no (0 = solo")
        print("  --orden-max-velas y las cancelaciones de siempre, sin decision activa).")
        print("  --tf-vecino-rapido por defecto es AUTOMATICO (el inmediato mas rapido que")
        print("  --tf, ver _VECINO_RAPIDO_AUTO) - si ese vecino ya muestra la misma señal de")
        print("  continuacion, abre/cierra antes de que cierre la propia vela. '-' lo apaga.")
        print("  --aceleracion-* controla aceleracion_alza/baja (señal NUEVA sin backtest,")
        print("  corre en PARALELO a impulso_alza/baja, ver estrategia/senales.py) -")
        print("  dispara si la ULTIMA vela cerrada rompe ella sola el ritmo de las")
        print("  anteriores, en vez de esperar el movimiento acumulado de impulso.")
        print("  --sin-enfriamiento apaga el enfriamiento tras cierre (por defecto ON): no")
        print("  reabrir de inmediato en la misma racha de señal de continuacion que se")
        print("  acaba de cerrar, hasta que deje de estar activa una vuelta.")
        print("  --sin-rvol-veta apaga el veto RVOL (por defecto ON, ver")
        print("  herramientas/backtest_rvol_filtro.py): sin RVOL>=1.0 en la vela de señal,")
        print("  aceleracion_alza/baja no abre. impulso_* no se toca.")
        print("  --sin-volatilidad-veta apaga el veto de volatilidad (por defecto ON, ver")
        print("  herramientas/backtest_filtros_combinados.py): sin ATR% >= 0.12 en la vela")
        print("  de señal, no abre - aplica a impulso_* Y aceleracion_*.")
        print("  Ejemplos: python monitor.py icp")
        print("            python monitor.py icp,sol,link --loop --cada 30")
        print("  Las comisiones van en PORCENTAJE por lado (defaults reales de")
        print("  Bitget). El papel las descuenta: entrada a mercado = taker.")
        print("  Corre SIEMPRE las dos ramas (libre/veto) a la vez, sobre la")
        print("  misma foto de mercado: monitor_<...>.csv y monitor_<...>_veto.csv")
        sys.exit(0)

    cfg = {
        "coins": [c.strip().upper() for c in argv[0].split(",") if c.strip()],
        "loop": False,
        "tf": "4h",
        # Hasta esto de posiciones de papel CONCURRENTES por moneda/rama
        # (2026-08-10) - antes solo se permitia 1. No apila hedges: una
        # señal en direccion opuesta a alguna ya abierta no abre otra hasta
        # que esa(s) se cierren (ver _revisar()).
        "posiciones_max": 3,
        "ventana": 30,
        "cada": 15,
        "rsi_bajo": 30.0,
        "rsi_alto": 70.0,
        "capital": 50.0,
        "fraccion_entrada": 0.02,
        "leverage": 10.0,
        "imb_umbral": 0.3,
        "imb_confirmaciones": 2,
        # Comisiones REALES de Bitget, en % por lado. El papel las descuenta:
        # sin esto el CSV miente (una i/v taker son 12 bps = 1,2% del margen
        # con leverage 10, y muchas señales no mueven ni eso).
        "comision_maker": 0.02,
        "comision_taker": 0.06,
        # Colchon del stop en multiplos del ATR de la vela de señal (0 = stop
        # crudo en el extremo, el comportamiento viejo). Con 29 bps de MAE
        # medio medido en julio, el extremo pelado moria por ruido.
        "stop_atr": 1.0,
        # TF arbitro para desempatar señales enfrentadas. None (default) =
        # AUTOMATICO, el primero mas lento que --tf (ver _ARBITRO_AUTO, se
        # resuelve al final de _parse_args) - antes era un "1h" fijo: en el
        # proceso de --tf 4h eso dejaba el arbitro MAS RAPIDO que lo operado
        # (al reves de lo que pide main()), y en el de --tf 1h a la misma
        # velocidad (sin aportar nada) - los CSV del 31-jul mostraban
        # arbitro_tf="1h" por igual en las 4 franjas, sin distinguir cual se
        # estaba operando. "-" lo desactiva del todo. Decide por DI+/DI-; el
        # ADX se graba pero no decide (ver _arbitro).
        "tf_arbitro": None,
        "di_separacion": 5.0,
        # Si el DI del TF arbitro VETA toda apertura contraria, no solo los
        # empates. La idea se probo corriendo una rama con veto y otra sin
        # el (2026-07-29 a 2026-08-07); desde que herramientas/
        # backtest_sma_rama.py confirmo la fusion veto+regimen+RR fijo
        # contra 9 años de historico (mejor winrate que regimen solo, en
        # 15m y 1h, sin perder media - ver anotaciones.md), main() fija
        # esta cfg a True siempre. Se deja el default en False aqui porque
        # es el valor "neutro" de la CLI si alguien corre monitor.py sin
        # pasar por el arranque normal (tests, otro entrypoint).
        "di_veta": False,
        # Veto REAL (no sombra) del filtro RVOL (FiltroVolumenSenal,
        # umbral=1.0) sobre aceleracion_alza/baja UNICAMENTE - impulso_* no
        # se toca, no se valido ahi (ver herramientas/backtest_rvol_filtro.py
        # 2026-08-07: RVOL>=1.0 mejora aceleracion_* en 29k trades de 9 años,
        # BTC+ETH 15m, +0.76%->+1.00% media/trade, 76.0%->77.5% winrate; en
        # impulso_* la diferencia es marginal). ON por defecto: esto SI se
        # decidio con evidencia de escala antes de activarlo.
        "rvol_veta": True,
        # Veto REAL (no sombra) del filtro volatilidad (FiltroVolatilidad,
        # ATR% de la vela de señal >= 0.12%) sobre CUALQUIER motivo (a
        # diferencia de RVOL, que solo aplica a aceleracion_*) - ver
        # herramientas/backtest_filtros_combinados.py, 2026-08-09: neutro/
        # positivo en 5m/15m/1h sobre 9 años BTC+ETH (a diferencia de
        # soporte/bollinger/btc, que empeoraron sin excepcion y se quedan
        # como sombra). ON por defecto, --sin-volatilidad-veta lo apaga.
        "volatilidad_veta": True,
        # La escalera: siguiente soporte/resistencia PROBADO por delante del
        # precio (ventana de velas cerradas, tolerancia y toques minimos en
        # ATR - mismos defaults que FiltroSoporte, MISMO criterio de "toque"
        # por diseño, ver _proximo_nivel). Se marca (nivel_escalera/
        # distancia_escalera_bps) en cada intento de apertura, informativo -
        # ya no bloquea la apertura (solo bloqueaba señales de reversion, que
        # ya no abren, ver SENALES_CONTINUACION), pero SI decide el objetivo
        # real de _objetivo_escalera/trailing en posiciones abiertas.
        # escalera_toques quedo DESINCRONIZADO de FiltroSoporte.umbral_toques
        # el 2026-08-02: se recalibro el filtro (2->6, el 2 nunca discriminaba
        # nada, 30/30 True) pero no esta copia - corregido el mismo dia.
        "escalera_ventana": 150,
        "escalera_tolerancia_atr": 0.25,
        "escalera_toques": 6,
        # Trailing por escalones (VETO_TF.md sec. 3): cuando el precio
        # alcanza el objetivo, ese nivel pasa a ser el nuevo stop y el
        # objetivo salta al siguiente. El objetivo exige minimo esto x ATR
        # de distancia - con menos, el escalon no cubre ni el punto muerto.
        "escalera_min_atr": 2.0,
        # Trailing por GIVEBACK del pico (2026-08-04, sustituye a la escalera
        # de arriba como salida por defecto en libre/veto - ver
        # anotaciones.md). Se arma cuando la ganancia supera
        # --trailing-armado-atr x ATR de la vela de señal (evita que el
        # ruido de apertura expulse antes de que la operacion respire);
        # desde ahi el stop persigue el pico dejando SOLO esta fraccion del
        # recorrido ganado entre el stop y el maximo. Encontrado explorando
        # ~130 combinaciones (fijo, escalones ATR, chandelier, chandelier
        # adaptado a expansion de ATR/Bollinger, R+trailing, salida por
        # tiempo) sobre las 162 posiciones reales de 4 sesiones
        # (31-jul/01-ago/02-ago/03-04-ago): el mejor resultado de todos,
        # media -0.325% vs -1.650% de la escalera estructural en las mismas
        # operaciones. 0 desactiva esto y vuelve a la escalera de siempre
        # (_objetivo_escalera/_proximo_nivel, sin tocar). No aplica a la
        # rama "sma" (--exit-rr>0 ya tiene su propia salida fija).
        "trailing_giveback": 0.20,
        "trailing_armado_atr": 0.5,
        # Señal de ENTRADA "impulso" (2026-08-04, sustituye a ruptura_alza/
        # baja como unica familia que ABRE - ver SENALES_CONTINUACION en
        # estrategia/senales.py y anotaciones.md).
        #
        # Recalibrado 2026-08-07 con backtest de ROBUSTEZ real (55 ventanas
        # de 60 dias, 2017-2026, herramientas/backtest_impulso_aceleracion.py
        # -> herramientas/robustez_impulso.csv): no hay un lookback/umbral que
        # sea el optimo en TODAS las franjas a la vez. lookback=4/min_atr=3.0
        # (el default anterior) es el propio optimo en 1h (100% de ventanas
        # ganadoras, media +7.64%/ventana) pero en 15m se queda en 94.5% de
        # ventanas (peor ventana -1.00%). lookback=3/min_atr=2.5 en cambio da
        # 100% de ventanas ganadoras TAMBIEN en 15m (nunca perdio en 9 años,
        # peor ventana +0.05%) a cambio de bajar el 1h a media +6.43%/ventana
        # (sigue siendo 100% ganador ahi, solo menos extremo). Eleccion
        # deliberada: se prioriza la robustez de 15m sobre exprimir el pico
        # de 1h.
        "impulso_lookback": 3,
        "impulso_min_atr": 2.5,
        # aceleracion_alza/baja (2026-08-05, ver estrategia/senales.py):
        # señal NUEVA que corre en PARALELO a impulso_alza/baja, sin
        # sustituirla - dispara en la ultima vela cerrada si ella sola rompe
        # el ritmo de las --aceleracion-ventana anteriores, en vez de
        # esperar a que el movimiento se acumule en --impulso-lookback
        # velas.
        #
        # aceleracion_min_atr recalibrado 2026-08-07 (mismo backtest de
        # robustez que impulso, ver herramientas/robustez_aceleracion.csv):
        # a diferencia de impulso, aqui SI hay un valor que mejora en las
        # CUATRO franjas (3m/5m/15m/1h) sin excepcion - 0.8 (el default
        # anterior) perdia en la mayoria de las 55 ventanas en 3m/5m/15m
        # (7.3% / 21.8% / 81.8% de ventanas ganadoras); subiendo a 1.2 sube
        # a 23.6% / 47.3% / 94.5% (1h tambien mejora, de 98.2% a 100%). Sin
        # tension entre franjas, mejora limpia. aceleracion_ventana/_mult se
        # dejan sin tocar - el barrido no encontro un ganador tan consistente
        # ahi como con min_atr.
        "aceleracion_ventana": 6,
        "aceleracion_min_atr": 1.2,
        "aceleracion_mult": 2.5,
        # Enfriamiento tras cierre (2026-08-05): si una señal de
        # CONTINUACION sigue activa cuando se cierra una posicion en su
        # misma direccion, no se reabre de inmediato en esa señal - hay que
        # esperar a que deje de estar activa al menos una vuelta (que "se
        # apague") antes de poder volver a abrir con ella. Visto en vivo
        # BTC 5m 2026-08-04 23:15-23:18 UTC: una posicion se cerro GANANDO
        # +0.31% por el trailing mientras impulso_alza seguia activo, y en
        # la MISMA vuelta se reabrio otra vez en largo -ya en el ultimo
        # tramo del pico- que murio en -7.06% cuarenta minutos despues. El
        # "enfriamiento tras stop" que existio antes (ver estrategia/
        # senales.py, retirado el 2026-07-29) se penso innecesario porque el
        # veto por DI ya cubria las reentradas problematicas de aquella
        # sesion - pero esas eran todas CONTRA el DI; esta fue A FAVOR (DI+
        # 19.5 > DI- 13.3), asi que el veto no la habria bloqueado. On por
        # defecto porque ataca un caso real medido; --sin-enfriamiento lo
        # apaga para comparar.
        "enfriamiento_continuacion": True,
        # Cancela una OrdenPendiente que lleve mas de esto (en velas de --tf)
        # sin llenarse ni cancelarse por señal/DI - 0 desactiva (espera para
        # siempre, el comportamiento viejo). Sin esto se vieron esperas de
        # hasta 3.6h en 15m (212 vueltas, ver anotaciones.md 2026-08-01): para
        # cuando llenaba, el contexto de la ruptura que la origino ya estaba
        # viejo - la mitad de "entra tarde" que SENALES_CONTINUACION no
        # arreglaba por si sola (esa ataca la señal, esta ataca el llenado).
        "orden_max_velas": 3.0,
        # Mientras haya AL MENOS UNA orden pendiente (en cualquier moneda o
        # rama), el bucle duerme esto en vez de --cada - el sondeo normal
        # (varios minutos en 1h/4h) es justo la ventana donde, medido en vivo,
        # se pierde el recorrido: para cuando main() vuelve a mirar, el precio
        # ya se movio sin que nadie decidiera nada. No decide SI llenar o
        # cancelar (eso sigue igual) - solo decide con que frecuencia se
        # vuelve a mirar mientras hay algo pendiente. Compromiso deliberado:
        # acelera el sondeo de TODAS las monedas/ramas, no solo la que tiene
        # la orden - separar el sondeo por orden exigiria un bucle por
        # moneda/rama en vez de uno solo compartido (pendiente de decidir).
        "cada_orden_pendiente": 0.25,
        # Cuanto esperar, en FRACCION DE VELA de --tf (no minutos fijos - un
        # fijo de 2 min son 48 velas de 5m pero 0.5 de 4h, significa algo
        # distinto en cada franja), antes de decidir activamente que hacer
        # con una OrdenPendiente que no se lleno: pasado esto, si el libro
        # sigue a favor (mismo criterio que --imb-umbral) escala a mercado;
        # si no, cancela. 0.2 = 1/5 de vela por defecto (1 min en 5m, 3 min
        # en 15m, 12 min en 1h, 48 min en 4h) - ni tan corto que interrumpa
        # un llenado normal en curso (la mayoria llena en menos de esto) ni
        # tan largo que vuelva a la espera indefinida que motivo todo este
        # cambio; se queda comodo por debajo de --orden-max-velas (3 velas)
        # en cualquier franja. 0 desactiva esta decision (solo quedan
        # --orden-max-velas y las cancelaciones por señal/DI de siempre).
        # Reemplaza el intento anterior (vueltas==1 and confluencia>=2), que
        # no disparo ni una vez en ~7700 posiciones de julio: exigia
        # confluencia doble (casi nunca se da) Y comprobaba una sola vuelta
        # despues de poner la orden, no "cuando la señal ya no es fresca".
        "orden_espera_mercado": 0.2,
        # TF vecino RAPIDO para intentar abrir/cerrar antes de que cierre la
        # propia vela (--tf-vecino-rapido). None (default) = automatico (ver
        # _VECINO_RAPIDO_AUTO): 15m/1h/4h usan TODOS 5m directamente, no una
        # cadena por vecino inmediato (1h<-5m sale igual o mejor que
        # 1h<-15m, con mas muestra - ver comentario junto a
        # _VECINO_RAPIDO_AUTO). "-" lo desactiva. 5m no tiene vecino (nada
        # mas rapido definido - 1m se probo y se descarto por demasiado
        # volatil, ver anotaciones.md 2026-08-04). Confirmado tambien contra
        # datos reales de la sesion 03/04-ago: comparando las entradas de 1h
        # contra la ventana de 5m (el vecino real) el gap mediana baja de
        # -141.8 a +6.5 bps - casi identico al 5m puro.
        "tf_vecino_rapido": None,
        # Regimen de fondo (2026-08-03, ver anotaciones.md): precio vs su SMA
        # en --regimen-tf (1d por defecto). MUY mas lento que el DI del
        # arbitro (dias, no velas) - discrimina mucho mas: alineado con la
        # direccion del trade da +0.146%/trade vs -0.087% en contra (RR 1:3,
        # backtest 1 año, n=965) contra el ~0% de diferencia que daba el DI.
        # "-" en --regimen-tf lo desactiva (regimen=None, nunca veta).
        "regimen_tf": "1d",
        "regimen_sma": 50,
        # Si el regimen VETA aperturas contrarias. main() fija esto a True
        # siempre desde el 2026-08-07 (ver di_veta arriba y anotaciones.md,
        # "Rama unica") - default False aqui solo como valor neutro de CLI.
        "regimen_veta": False,
        # Objetivo FIJO a este multiplo del riesgo inicial (stop), en vez de
        # la escalera/breakeven/cierre-por-señal de siempre. 0 = "usa el
        # default de main()" (3.0 desde el 2026-08-07, ver mas abajo);
        # backtest 1 año: RR 1:3 fue el UNICO mecanismo de salida de los 7
        # probados que dio positivo en conjunto (+0.043%/trade vs -0.078%
        # de la escalera) - ver anotaciones.md.
        "exit_rr": 0.0,
    }
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--loop":
            cfg["loop"] = True
        elif a == "--tf":
            i += 1; cfg["tf"] = argv[i]
        elif a == "--posiciones-max":
            i += 1; cfg["posiciones_max"] = int(argv[i])
        elif a == "--ventana":
            i += 1; cfg["ventana"] = int(argv[i])
        elif a == "--cada":
            i += 1; cfg["cada"] = float(argv[i])
        elif a == "--rsi-bajo":
            i += 1; cfg["rsi_bajo"] = float(argv[i])
        elif a == "--rsi-alto":
            i += 1; cfg["rsi_alto"] = float(argv[i])
        elif a == "--capital":
            i += 1; cfg["capital"] = float(argv[i])
        elif a == "--fraccion-entrada":
            i += 1; cfg["fraccion_entrada"] = float(argv[i])
        elif a == "--leverage":
            i += 1; cfg["leverage"] = float(argv[i])
        elif a == "--imb-umbral":
            i += 1; cfg["imb_umbral"] = float(argv[i])
        elif a == "--imb-confirmaciones":
            i += 1; cfg["imb_confirmaciones"] = int(argv[i])
        elif a == "--comision-maker":
            i += 1; cfg["comision_maker"] = float(argv[i])
        elif a == "--comision-taker":
            i += 1; cfg["comision_taker"] = float(argv[i])
        elif a == "--stop-atr":
            i += 1; cfg["stop_atr"] = float(argv[i])
        elif a == "--tf-arbitro":
            i += 1; cfg["tf_arbitro"] = argv[i] if argv[i] != "-" else ""
        elif a == "--di-separacion":
            i += 1; cfg["di_separacion"] = float(argv[i])
        elif a == "--escalera-ventana":
            i += 1; cfg["escalera_ventana"] = int(argv[i])
        elif a == "--escalera-tolerancia-atr":
            i += 1; cfg["escalera_tolerancia_atr"] = float(argv[i])
        elif a == "--escalera-toques":
            i += 1; cfg["escalera_toques"] = int(argv[i])
        elif a == "--escalera-min-atr":
            i += 1; cfg["escalera_min_atr"] = float(argv[i])
        elif a == "--trailing-giveback":
            i += 1; cfg["trailing_giveback"] = float(argv[i])
        elif a == "--trailing-armado-atr":
            i += 1; cfg["trailing_armado_atr"] = float(argv[i])
        elif a == "--impulso-lookback":
            i += 1; cfg["impulso_lookback"] = int(argv[i])
        elif a == "--impulso-min-atr":
            i += 1; cfg["impulso_min_atr"] = float(argv[i])
        elif a == "--aceleracion-ventana":
            i += 1; cfg["aceleracion_ventana"] = int(argv[i])
        elif a == "--aceleracion-min-atr":
            i += 1; cfg["aceleracion_min_atr"] = float(argv[i])
        elif a == "--aceleracion-mult":
            i += 1; cfg["aceleracion_mult"] = float(argv[i])
        elif a == "--sin-enfriamiento":
            cfg["enfriamiento_continuacion"] = False
        elif a == "--sin-rvol-veta":
            cfg["rvol_veta"] = False
        elif a == "--sin-volatilidad-veta":
            cfg["volatilidad_veta"] = False
        elif a == "--orden-max-velas":
            i += 1; cfg["orden_max_velas"] = float(argv[i])
        elif a == "--cada-orden-pendiente":
            i += 1; cfg["cada_orden_pendiente"] = float(argv[i])
        elif a == "--orden-espera-mercado":
            i += 1; cfg["orden_espera_mercado"] = float(argv[i])
        elif a == "--tf-vecino-rapido":
            i += 1; cfg["tf_vecino_rapido"] = argv[i] if argv[i] != "-" else ""
        elif a == "--regimen-tf":
            i += 1; cfg["regimen_tf"] = argv[i] if argv[i] != "-" else ""
        elif a == "--regimen-sma":
            i += 1; cfg["regimen_sma"] = int(argv[i])
        elif a == "--exit-rr":
            i += 1; cfg["exit_rr"] = float(argv[i])
        else:
            print(f"(aviso) argumento no reconocido: {a}")
        i += 1
    if cfg["tf_arbitro"] is None:
        cfg["tf_arbitro"] = _tf_arbitro_auto(cfg["tf"])
    if cfg["tf_vecino_rapido"] is None:
        cfg["tf_vecino_rapido"] = _tf_vecino_auto(cfg["tf"])
    return cfg


def _proximo_sleep(cfg):
    """Cuanto dormir hasta la proxima vuelta. Normalmente --cada minutos,
    pero si el proximo cierre de vela cae DENTRO de esa espera, se despierta
    justo despues de ese cierre (con un margen para que el exchange ya tenga
    la vela lista) en vez de esperar el intervalo entero.

    Por que: con un --cada ciego, el retraso entre "cierra la vela" y "el
    monitor la ve por primera vez" puede llegar a ser --cada minutos enteros,
    segun donde caiga la fase - visto en vivo el 30-jul: dos entradas varios
    minutos despues del cierre real, con el precio ya movido en contra o
    comiendose parte del recorrido. El resto del tiempo (fuera de un cierre
    inminente) el coste de llamadas no cambia nada, solo se adelanta el
    despertar CUANDO hace falta."""
    tf_min = _tf_minutos(cfg["tf"])
    normal = cfg["cada"] * 60
    if tf_min <= 0:
        return normal
    tf_seg = tf_min * 60
    margen = 8  # segundos tras el cierre, para no leer una vela a medio cuajar
    ahora = datetime.now(timezone.utc).timestamp()
    hasta_cierre = tf_seg - (ahora % tf_seg)
    if hasta_cierre + margen < normal:
        return hasta_cierre + margen
    return normal


def _revisar(coin, cfg, m, arb, vecino, regimen, vistas, vistas_vecino, vela_inicio,
             bloqueo, posiciones, capitales, ordenes_pendientes, writer, arch):
    """Gestiona la posicion de papel de UNA rama (libre u obedece) sobre una
    foto de mercado 'm' y un veredicto de arbitro 'arb' ya calculados fuera -
    compartidos entre las dos ramas de la misma moneda, para que ambas decidan
    sobre la MISMA vela y no sobre lecturas de la API 281 ms desincronizadas
    (visto en vivo el 2026-07-30: max_rango y ATR distintos entre libre/veto
    en la misma vuelta porque cada rama era un proceso que sondeaba aparte).
    Imprime su estado, avisa de senales/continuacion nuevas y GRABA una fila
    en el CSV con el resultado de esta vuelta. 'vistas' es el set de claves ya
    avisadas de ESA moneda para ESTA rama (se actualiza). 'vecino' es el
    resultado de _vecino_senales() (o None) - mismo trato que 'arb', tambien
    calculado UNA vez fuera y compartido entre ramas. 'vistas_vecino' es el
    equivalente de 'vistas' pero para las señales del vecino. 'regimen' es
    el resultado de _regimen() (o None) - mismo trato que 'arb'/'vecino'.
    'bloqueo' es el set de claves de SENALES_CONTINUACION en enfriamiento
    para ESTA moneda y ESTA rama (ver cfg["enfriamiento_continuacion"]).
    'vela_inicio' es el dict COMPLETO {coin: timestamp o None} de la rama
    (no el valor de ESTA moneda, a diferencia de 'vistas'/'bloqueo') porque
    aqui se ESCRIBE la primera vez que se ve cada moneda - mismo patron que
    'posiciones'/'capitales'/'ordenes_pendientes'."""
    try:
        print(formatear(coin, cfg, m))

        nuevas = [(clave, txt) for clave, txt in m["senales"] if clave not in vistas]
        for _, txt in nuevas:
            print(f"   >>> {txt}")
        # OJO: la señal en si NO avisa por Telegram (solo consola + CSV) -
        # demasiado ruido con varios monitores en --loop. Solo avisan por
        # Telegram apertura/cierre de posicion (ver _abrir/_cerrar).

        # Recordamos las senales actuales; si desaparecen, se podran volver a
        # avisar mas adelante (nuevo cruce), no antes.
        vistas.clear()
        vistas.update(clave for clave, _ in m["senales"])
        nuevas_claves = {clave for clave, _ in nuevas}
        # Todas las señales activas AHORA, nuevas o no. La ambiguedad y la
        # direccion de ENTRADA se deciden sobre esto, no sobre nuevas_claves:
        # si ruptura_baja nacio hace un par de vueltas y sigue activa (la
        # misma vela cerrada, --cada mas corto que el tf), y AHORA entra
        # rsi_sobreventa, el conflicto es real aunque una de las dos señales
        # no sea "nueva" esta vuelta (audit 2026-07-30, punto 3 - 0 casos
        # reales en las ~2300 filas de esta noche, pero el hueco es real).
        activas_claves = {clave for clave, _ in m["senales"]}

        # Señales del vecino RAPIDO (2026-08-02): mismo tratamiento vistas/
        # nuevas/activas que las propias, pero de una foto de mercado
        # DISTINTA (el vecino, no 'm') - por eso vive en su propio set
        # 'vistas_vecino', no se mezcla con 'vistas'. Vacio si no hay vecino
        # configurado (--tf 5m, o --tf-vecino-rapido -).
        if vecino:
            nuevas_vecino = [(c, t) for c, t in vecino["senales"] if c not in vistas_vecino]
            vistas_vecino.clear()
            vistas_vecino.update(c for c, _ in vecino["senales"])
            nuevas_claves_vecino = {c for c, _ in nuevas_vecino}
            activas_claves_vecino = {c for c, _ in vecino["senales"]}
        else:
            nuevas_claves_vecino = set()
            activas_claves_vecino = set()

        # Enfriamiento: libera cualquier clave bloqueada que ya NO este
        # activa esta vuelta (ni en la propia ni en el vecino) - "se apago",
        # puede volver a disparar una apertura la proxima vez que aparezca.
        # Si esta apagado por cfg, 'bloqueo' se queda siempre vacio (nunca se
        # le añade nada mas abajo tampoco, ver el cierre != None de abajo).
        if cfg.get("enfriamiento_continuacion", True):
            bloqueo &= (activas_claves | activas_claves_vecino)

        # ---------------- Posiciones de PAPEL (sin dinero real) ----------------
        # Lista, no una sola (2026-08-10): hasta cfg["posiciones_max"]
        # concurrentes por moneda/rama - ver PARAMS_AJUSTABLES.
        abiertas = posiciones[coin]
        orden = ordenes_pendientes[coin]
        precio = m["precio"]
        # El stop/objetivo/orden pendiente se comprueban contra el PEOR o
        # MEJOR precio de esta vuelta, no solo el tick puntual: entre dos
        # vueltas (2-5 min) el precio puede tocar un nivel y recuperarse sin
        # que 'precio' (la foto de AHORA) se entere. La vela en formacion
        # (m["velas"][-1]) ya trae ese recorrido intra-vuelta.
        vela_form = m["velas"][-1]

        # Calentamiento (2026-08-05, ver anotaciones.md (7)): 'senales()'
        # decide sobre la vela CERRADA, m["velas"][-2] (ver estrategia/
        # senales.py). Se guarda el timestamp de la primera que ve este
        # proceso para esta moneda/rama - mientras siga siendo esa MISMA
        # vela, cualquier señal de continuacion activa puede llevar rato
        # corriendo desde antes de arrancar (vistas/bloqueo empiezan
        # vacios, la tratarian como recien nacida). En cuanto cierra una
        # vela NUEVA, calentando pasa a False para siempre en esta moneda.
        vela_cerrada_ts = m["velas"][-2][0] if len(m["velas"]) >= 2 else None
        if vela_inicio.get(coin) is None and vela_cerrada_ts is not None:
            vela_inicio[coin] = vela_cerrada_ts
        calentando = (vela_cerrada_ts is not None
                       and vela_cerrada_ts == vela_inicio.get(coin))

        # direccion_nueva/es_continuacion/vetado_cierre son de la MONEDA esta
        # vuelta, no de una posicion en particular (2026-08-10, antes vivian
        # dentro del bloque de la unica posicion) - se calculan UNA vez y se
        # comparten entre TODAS las posiciones abiertas de esta moneda/rama.
        direccion_nueva = _direccion(nuevas_claves)
        # Solo una señal contraria de CONTINUACION (ruptura_alza/baja) cierra
        # de inmediato - misma logica que ya decide quien ABRE (ver
        # SENALES_CONTINUACION): una de REVERSION sola (rechazo/div/rsi) ya
        # no cierra, ver mas abajo. Encontrado en vivo el 2026-08-02: un
        # largo con +0.3% de MFE (escalera sin activar todavia) se cerro en
        # -1.44% por un rechazo_max justo en una pausa del impulso, no en
        # una reversion real - anotaciones.md.
        direccion_continuacion = _direccion(nuevas_claves & SENALES_CONTINUACION)
        es_continuacion = (direccion_continuacion is not None
                            and direccion_continuacion == direccion_nueva)
        # En la rama VETO, una señal contraria que el DI tampoco dejaria
        # ABRIR no deberia poder CERRAR una posicion ganadora: si no,
        # --di-veta obedece al DI para entrar pero lo ignora para salir, que
        # es la asimetria del audit 2026-07-30 (punto 4). Con --di-veta
        # apagado (rama libre) esto no cambia nada.
        vetado_cierre = (cfg["di_veta"] and direccion_nueva is not None
                          and arb and arb["direccion"]
                          and arb["direccion"] != direccion_nueva)
        if vetado_cierre and abiertas:
            print(f"   (se ignora señal contraria {direccion_nueva}: "
                  f"el DI de {arb['tf']} tampoco la dejaria abrir — "
                  f"DI+ {arb['di_mas']:.1f} / DI- {arb['di_menos']:.1f})")

        ambiguedad = _ambiguedad(activas_claves)

        def _fila_vuelta(pos_final, cierre, eventos, imb_val, imb_racha_val,
                          estancamiento_veredicto, estancamiento_valor,
                          id_apertura="", veto_rvol="", veto_volatilidad="",
                          bloqueada="", desempate="", nivel_escalera="",
                          distancia_escalera="", escalera_bloqueada="",
                          enfriamiento_bloqueada=""):
            """Construye y graba UNA fila del CSV - se llama una vez por
            posicion gestionada esta vuelta (pre-existentes, en el bucle de
            abajo) y una vez mas por el intento de apertura/orden pendiente
            (al final de _revisar). Cierra sobre 'precio'/'m'/'ambiguedad'/
            'arb'/'orden'/'nuevas_claves'/'coin'/'capitales' del scope de
            _revisar - 'orden' se lee tal cual este AL MOMENTO de la llamada
            (values distintos segun se llame antes o despues de gestionar la
            orden pendiente, intencional)."""
            pnl_usdt = pnl_pct = pnl_neto_usdt = pnl_neto_pct = comision_ent = ""
            if pos_final is not None:
                pnl_usdt, pnl_pct = pos_final.pnl(precio)
                pnl_neto_usdt, pnl_neto_pct = pos_final.pnl_neto(precio)
                comision_ent = pos_final.comision_entrada
            # Veredictos de los filtros SOLO si esta vuelta abrio una
            # posicion nueva (id_apertura no vacio) - son una foto del
            # momento de la entrada, no algo que se recalcule cada vuelta.
            veredictos = pos_final.veredictos if (id_apertura and pos_final is not None) else {}
            def _v(nombre):
                ok, _ = veredictos.get(nombre, (None, None))
                return ok if ok is not None else ""
            def _val(nombre):
                _, valor = veredictos.get(nombre, (None, None))
                return valor if valor is not None else ""
            ahora = datetime.now(timezone.utc)
            fila = {
                "timestamp_ms": int(ahora.timestamp() * 1000),
                "fecha_utc": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                "fecha_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "coin": coin,
                "precio": precio,
                "rsi": m["rsi"],
                "tendencia": m["tendencia"],
                "atr": m.get("atr") if m.get("atr") is not None else "",
                "atr_pct": m.get("atr_pct") if m.get("atr_pct") is not None else "",
                "bollinger_ancho_pct": (m.get("bollinger_ancho_pct")
                                        if m.get("bollinger_ancho_pct") is not None else ""),
                "ambiguedad": ambiguedad,
                "apertura_bloqueada": bloqueada,
                "desempate": desempate,
                "enfriamiento_bloqueada": enfriamiento_bloqueada,
                "veto_rvol": veto_rvol,
                "veto_volatilidad": veto_volatilidad,
                "nivel_escalera": nivel_escalera,
                "distancia_escalera_bps": distancia_escalera,
                "escalera_bloqueada": escalera_bloqueada,
                "arbitro_tf": arb["tf"] if arb else "",
                "arbitro_adx": arb["adx"] if (arb and arb["adx"] is not None) else "",
                "arbitro_di_mas": arb["di_mas"] if arb else "",
                "arbitro_di_menos": arb["di_menos"] if arb else "",
                "cambio24h_pct": m["cambio24h"],
                "max_rango": m["max"],
                "min_rango": m["min"],
                "senales": ";".join(clave for clave, _ in m["senales"]),
                "senales_nuevas": ";".join(sorted(nuevas_claves)),
                "bid": m.get("bid") if m.get("bid") is not None else "",
                "ask": m.get("ask") if m.get("ask") is not None else "",
                "spread_bps": m.get("spread_bps") if m.get("spread_bps") is not None else "",
                "orden_pendiente_lado": orden.lado if orden else "",
                "orden_pendiente_precio": orden.precio_limite if orden else "",
                "orden_pendiente_motivo": orden.motivo if orden else "",
                "orden_pendiente_desde": orden.hora if orden else "",
                "orden_pendiente_vueltas": orden.vueltas if orden else "",
                "posicion_lado": pos_final.lado if pos_final else "",
                "posicion_entrada": pos_final.entrada if pos_final else "",
                "posicion_entrada_tipo": getattr(pos_final, "entrada_tipo", "") if pos_final else "",
                "posicion_cantidad": pos_final.cantidad if pos_final else "",
                "posicion_margen": pos_final.margen if pos_final else "",
                "posicion_nocional": pos_final.nocional if pos_final else "",
                "posicion_stop": pos_final.stop if pos_final else "",
                "posicion_stop_origen": getattr(pos_final, "stop_origen", "") if pos_final else "",
                "posicion_riesgo_bps": (abs(pos_final.entrada - pos_final.stop)
                                        / pos_final.entrada * 1e4) if pos_final else "",
                "posicion_objetivo": (pos_final.objetivo or "") if pos_final else "",
                "pnl_usdt": pnl_usdt,
                "pnl_pct_margen": pnl_pct,
                "pnl_neto_usdt": pnl_neto_usdt,
                "pnl_neto_pct_margen": pnl_neto_pct,
                "comision_entrada_usdt": comision_ent,
                "cierre_pnl_usdt": cierre["bruto"] if cierre else "",
                "cierre_pnl_pct": cierre["pct_bruto"] if cierre else "",
                "cierre_comision_usdt": cierre["comision"] if cierre else "",
                "cierre_pnl_neto_usdt": cierre["neto"] if cierre else "",
                "cierre_pnl_neto_pct": cierre["pct_neto"] if cierre else "",
                "motivo_cierre": cierre["motivo"] if cierre else "",
                "imbalance": imb_val if imb_val is not None else "",
                "imb_racha": imb_racha_val if imb_racha_val is not None else "",
                "evento": ";".join(eventos),
                "capital_actual": capitales[coin],
                "id_apertura": id_apertura,
                "filtro_volatilidad_veredicto": _v("volatilidad"),
                "filtro_volatilidad_valor": _val("volatilidad"),
                "filtro_volumen_veredicto": _v("volumen"),
                "filtro_volumen_valor": _val("volumen"),
                "filtro_cvd_veredicto": _v("cvd"),
                "filtro_cvd_valor": _val("cvd"),
                "filtro_funding_veredicto": _v("funding"),
                "filtro_funding_valor": _val("funding"),
                "filtro_open_interest_veredicto": _v("open_interest"),
                "filtro_open_interest_valor": _val("open_interest"),
                "filtro_estancamiento_veredicto": estancamiento_veredicto,
                "filtro_estancamiento_valor": estancamiento_valor,
                "id_cierre": cierre["id"] if cierre else "",
            }
            _registrar(writer, arch, fila)

        # Gestiona cada posicion PRE-EXISTENTE: stop, salida RR fija o
        # escalera/trailing/breakeven/señal contraria, estancamiento y
        # continuacion por order flow - cada una puede cerrar de forma
        # independiente de las demas. Una fila de CSV por posicion.
        for pos in list(abiertas):
            resultado = _gestionar_posicion(coin, cfg, pos, precio, vela_form, m,
                                             direccion_nueva, es_continuacion,
                                             vetado_cierre, capitales)
            cierre = resultado["cierre"]
            if cierre is not None:
                abiertas.remove(pos)
                if cfg.get("enfriamiento_continuacion", True):
                    # No reabrir en la MISMA racha de continuacion que se
                    # acaba de cerrar, aunque haya cerrado GANANDO (trailing)
                    # - ver el default de "enfriamiento_continuacion" arriba
                    # para el caso real que lo motivo.
                    conjunto_lado_cerrado = SENALES_LARGO if cierre["lado"] == "largo" else SENALES_CORTO
                    bloqueo |= (activas_claves | activas_claves_vecino) & SENALES_CONTINUACION & conjunto_lado_cerrado
            _fila_vuelta(resultado["pos"], cierre, resultado["eventos"],
                         resultado["imb_val"], resultado["imb_racha_val"],
                         resultado["estancamiento_veredicto"], resultado["estancamiento_valor"])

        # ---------------- Orden pendiente / intento de apertura NUEVA ----------------
        eventos = []
        id_apertura = ""
        veto_rvol = ""                 # "SI" si _abrir() rechazo por RVOL esta vuelta (ver cfg["rvol_veta"])
        veto_volatilidad = ""          # "SI" si _abrir() rechazo por volatilidad esta vuelta (ver cfg["volatilidad_veta"])
        pos_nueva = None                # posicion abierta ESTA vuelta (via orden llenada o apertura directa)

        if orden is not None:
            # Gestion de la orden LIMITE pendiente (VETO_TF.md sec. 5): se
            # cancela si el DI (en veto) deja de respaldar su direccion o
            # aparece señal contraria, se llena si el precio REBASA el nivel
            # (no solo lo toca), o escala a mercado si sigue sin llenarse una
            # vuelta despues y la señal original era doble. No depende de si
            # hay otras posiciones ya abiertas (2026-08-10): puede haber una
            # orden pendiente para un slot mientras otras posiciones de la
            # misma moneda siguen su curso por separado.
            direccion_nueva_orden = _direccion(nuevas_claves | nuevas_claves_vecino)
            # Una orden pendiente no tiene NADA arriesgado todavia -a
            # diferencia de una posicion abierta, donde exigimos señal nueva
            # Y que el DI respalde esa señal, para no salir por un bailoteo
            # del DI sin motivo de precio- asi que aqui el DI puede cancelar
            # el solo, cada vuelta, sin esperar a una señal nueva: es el
            # mismo trato que ya recibe cualquier intento de apertura fresco.
            di_cancela = (cfg["di_veta"] and arb and arb["direccion"]
                          and arb["direccion"] != orden.lado)
            vetado_señal = (cfg["di_veta"] and direccion_nueva_orden is not None
                             and arb and arb["direccion"]
                             and arb["direccion"] != direccion_nueva_orden)
            señal_cancela = (direccion_nueva_orden is not None
                              and direccion_nueva_orden != orden.lado and not vetado_señal)
            # --orden-max-velas (2026-08-01): sin esto una orden con
            # confluencia simple espera para siempre si no aparece señal
            # contraria ni el DI se da vuelta - se vieron esperas de hasta
            # 3.6h en 15m (212 vueltas). Minutos REALES desde que se coloco
            # (orden.creada_dt), no vueltas: independiente de --cada.
            expirada = (cfg["orden_max_velas"] > 0 and _tf_minutos(cfg["tf"]) > 0
                        and (datetime.now(timezone.utc) - orden.creada_dt).total_seconds()
                            >= cfg["orden_max_velas"] * _tf_minutos(cfg["tf"]) * 60)
            if di_cancela:
                motivo_cancel = f"el DI de {arb['tf']} ya no respalda {orden.lado}"
            elif señal_cancela:
                motivo_cancel = f"señal contraria ({direccion_nueva_orden})"
            elif expirada:
                motivo_cancel = (f"expiro sin llenarse "
                                  f"({cfg['orden_max_velas']:.0f} velas de {cfg['tf']})")
            else:
                motivo_cancel = None
            if motivo_cancel:
                texto = (f"ORDEN CANCELADA {orden.lado.upper()} @ "
                         f"{orden.precio_limite:.4f} ({motivo_cancel})")
                print(f"   >>> {texto}")
                ordenes_pendientes[coin] = orden = None
                eventos.append("orden_cancelada")
            else:
                if orden.lado == "largo":
                    rebaso = max(precio, vela_form[2]) > orden.precio_limite
                else:
                    rebaso = min(precio, vela_form[3]) < orden.precio_limite
                if rebaso:
                    rechazo = []
                    pos = _abrir(
                        coin, cfg, m, orden.motivo, capitales, set(),
                        señal=orden.señal, precio_entrada=orden.precio_limite,
                        entrada_tipo="limite", confluencia_override=orden.confluencia,
                        rechazo=rechazo)
                    if pos is not None:
                        abiertas.append(pos)
                        pos_nueva = pos
                        id_apertura = pos.id
                        eventos.append("apertura")
                    else:
                        eventos.append("orden_cancelada")
                        if "rvol" in rechazo:
                            veto_rvol = "SI"
                        if "volatilidad" in rechazo:
                            veto_volatilidad = "SI"
                    ordenes_pendientes[coin] = orden = None
                else:
                    orden.vueltas += 1
                    # Reemplaza el intento viejo (vueltas==1 and confluencia>=2,
                    # que no disparo ni una vez en ~7700 posiciones de julio -
                    # ver anotaciones.md 2026-08-02). Tiempo REAL desde que se
                    # puso (orden.creada_dt), no vueltas: independiente de
                    # --cada-orden-pendiente, que solo decide cada cuanto se
                    # mira, no cuando decidir. El libro (no el ADX del arbitro,
                    # que no cambia en un par de minutos) dice si el impulso
                    # todavia lo merece - mismo umbral que --imb-umbral.
                    espera_min = cfg["orden_espera_mercado"] * _tf_minutos(cfg["tf"])
                    espera_cumplida = (cfg["orden_espera_mercado"] > 0 and espera_min > 0
                                        and (datetime.now(timezone.utc) - orden.creada_dt).total_seconds()
                                            >= espera_min * 60)
                    if espera_cumplida:
                        imb, favorece = _libro_favorece(m["simbolo"], orden.lado, cfg["imb_umbral"])
                        imb_txt = f"{imb:+.2f}" if imb is not None else "sin datos"
                        if favorece:
                            texto = (f"ORDEN ESCALA A MERCADO {orden.lado.upper()} por "
                                     f"'{orden.motivo}': no se lleno en "
                                     f"{espera_min:.1f} min ({cfg['orden_espera_mercado']:.2f}x vela de "
                                     f"{cfg['tf']}) y el libro sigue a favor (imbalance {imb_txt})")
                            print(f"   >>> {texto}")
                            rechazo = []
                            pos = _abrir(
                                coin, cfg, m, orden.motivo, capitales, set(),
                                señal=orden.señal, entrada_tipo="mercado",
                                confluencia_override=orden.confluencia, rechazo=rechazo)
                            if pos is not None:
                                abiertas.append(pos)
                                pos_nueva = pos
                                id_apertura = pos.id
                                eventos.append("apertura")
                            else:
                                eventos.append("orden_cancelada")
                                if "rvol" in rechazo:
                                    veto_rvol = "SI"
                                if "volatilidad" in rechazo:
                                    veto_volatilidad = "SI"
                            ordenes_pendientes[coin] = orden = None
                        else:
                            texto = (f"ORDEN CANCELADA {orden.lado.upper()} @ "
                                     f"{orden.precio_limite:.4f} (no lleno en "
                                     f"{espera_min:.1f} min y el libro no respalda, "
                                     f"imbalance {imb_txt})")
                            print(f"   >>> {texto}")
                            ordenes_pendientes[coin] = orden = None
                            eventos.append("orden_cancelada")
                    else:
                        print(f"   orden pendiente: {orden.lado.upper()} @ "
                              f"{orden.precio_limite:.4f} por '{orden.motivo}' "
                              f"({orden.vueltas} vueltas esperando)")

        bloqueada = ""
        desempate = ""
        nivel_escalera = distancia_escalera = ""
        escalera_bloqueada = ""
        enfriamiento_bloqueada = ""
        # 'pos_nueva is None' evita re-evaluar un intento de apertura fresco
        # en la MISMA vuelta en que una orden pendiente acaba de llenarse
        # (ese slot ya se uso esta vuelta) - si en cambio la orden se
        # CANCELO (pos_nueva sigue None), si se re-evalua, mismo
        # comportamiento que tenia la version de una sola posicion.
        if pos_nueva is None and len(abiertas) < cfg["posiciones_max"] and orden is None:
            # Solo SENALES_CONTINUACION puede ABRIR (ver definicion de la
            # constante) - el resto de señales activas (agotamiento) no
            # cuentan aqui, aunque sigan activas y aunque hayan decidido el
            # cierre de una posicion contraria mas arriba en esta misma vuelta.
            # Se suman las de CONTINUACION del vecino rapido (2026-08-02): si
            # el vecino ya rompio en la misma direccion, cuenta igual que si
            # lo hubiera hecho la propia vela - normalmente antes, que es
            # justo el punto (simulado sobre 10 dias de 1m: 71-79% de mejor
            # entrada en 15m<-5m y 1h<-15m).
            if calentando:
                # No abrir en la primera vela vista tras arrancar (ver
                # 'calentando' mas arriba) - cualquier señal activa ahora
                # mismo pudo nacer minutos u horas antes de que este proceso
                # se pusiera a mirar. Caso real 2026-08-05(7): ETH 5m abrio
                # con RSI=80.97 un minuto despues de reiniciar, sobre un
                # tramo que llevaba 23 min y +1.2% corriendo desde antes.
                claves_apertura_propia = claves_apertura_vecino = claves_apertura = set()
                bloqueada = "calentando"
                print(f"   (calentando: primera vela vista tras arrancar, no "
                      f"se abre aunque haya señal de continuacion activa)")
            else:
                claves_apertura_propia = (activas_claves & SENALES_CONTINUACION) - bloqueo
                claves_apertura_vecino = (activas_claves_vecino & SENALES_CONTINUACION) - bloqueo
                claves_apertura = claves_apertura_propia | claves_apertura_vecino
            enfriando = bloqueo & (activas_claves | activas_claves_vecino)
            if enfriando:
                enfriamiento_bloqueada = "+".join(sorted(enfriando))
                print(f"   (en enfriamiento, no reabre con {enfriamiento_bloqueada}: sigue "
                      f"activa desde que se cerro una posicion en ese lado)")
            ambiguedad_apertura = _ambiguedad(claves_apertura)
            direccion = _direccion(claves_apertura)
            if ambiguedad_apertura and arb and arb["direccion"]:
                # El empate se resuelve por DIRECCION del TF arbitro. Antes se
                # resolvia SIEMPRE a favor de no operar, y eso no es neutral:
                # descarta sistematicamente las rupturas con recorrido (el
                # 2026-07-29 tiro dos cortos de +74 y +52 bps para quedarse
                # con cuatro largos que no pasaron de +35 en su mejor momento).
                direccion = arb["direccion"]
                desempate = (f"{direccion} por DI ({arb['di_mas']:.1f}/"
                             f"{arb['di_menos']:.1f} en {arb['tf']})")
                print(f"   desempate: señales enfrentadas ({ambiguedad_apertura}) -> "
                      f"manda {desempate}")
            elif ambiguedad_apertura:
                motivo_no = "sin arbitro" if not arb else \
                            f"DI empatado ({arb['separacion']:.1f} < {cfg['di_separacion']})"
                print(f"   (sin abrir: señales enfrentadas -> {ambiguedad_apertura}; {motivo_no})")
                direccion = None
            # VETO por DI: solo si --di-veta. La direccion del TF arbitro manda
            # sobre cualquier apertura, no solo sobre los empates. El 2026-07-29
            # habria vetado los 4 largos (los 4 murieron en stop) y dejado pasar
            # los 2 cortos (+74 y +52 bps): 6 de 6. Pero es UNA sesion y toda
            # bajista - por eso es opcional, para correr una rama que obedece y
            # otra que solo anota.
            if (direccion is not None and cfg["di_veta"] and arb
                    and arb["direccion"] and arb["direccion"] != direccion):
                bloqueada = direccion
                print(f"   (sin abrir {direccion}: el DI de {arb['tf']} manda "
                      f"{arb['direccion']} — DI+ {arb['di_mas']:.1f} / "
                      f"DI- {arb['di_menos']:.1f})")
                direccion = None
            # VETO por REGIMEN (rama "sma", 2026-08-03): solo si --regimen-veta.
            # A diferencia del DI (vela anterior, no discrimina - ver arriba),
            # el regimen es precio vs su SMA de dias/semanas - alineado con la
            # direccion del trade da +0.146%/trade vs -0.087% en contra
            # (backtest 1 año, RR 1:3, n=965). 'regimen' es "alcista"/"bajista"
            # o None (sin dato, nunca veta) - ver _regimen().
            if (direccion is not None and cfg["regimen_veta"] and regimen
                    and ((regimen == "alcista" and direccion == "corto")
                         or (regimen == "bajista" and direccion == "largo"))):
                bloqueada = direccion
                print(f"   (sin abrir {direccion}: el regimen de fondo "
                      f"({regimen}, SMA{cfg['regimen_sma']} {cfg['regimen_tf']}) "
                      f"va en contra)")
                direccion = None
            # Guarda anti-hedge (2026-08-10, ver PARAMS_AJUSTABLES
            # ["posiciones_max"]): con varias posiciones concurrentes
            # permitidas, una señal en direccion OPUESTA a alguna YA
            # ABIERTA no debe apilar un hedge sobre si misma (mismo activo,
            # las dos patas se anulan y solo suman comision) - no abre hasta
            # que esa(s) se cierren por su propio stop/objetivo/señal
            # contraria.
            if direccion is not None and any(p.lado != direccion for p in abiertas):
                n_contrarias = sum(1 for p in abiertas if p.lado != direccion)
                bloqueada = direccion
                print(f"   (sin abrir {direccion}: ya hay {n_contrarias} posicion(es) "
                      f"{'CORTO' if direccion == 'largo' else 'LARGO'} abierta(s) en "
                      f"{coin} - no se apila un hedge)")
                direccion = None
            if direccion is not None:
                # Busca en claves_apertura, no en nuevas_claves: la direccion
                # se resolvio sobre TODAS las activas de continuacion (arriba),
                # asi que la señal ganadora puede no ser nueva esta vuelta.
                # sorted() para que, si coinciden dos señales del mismo lado,
                # cual queda registrada como motivo sea reproducible entre
                # ejecuciones (antes salia de iterar un set - audit 2026-07-30,
                # punto 5).
                motivo = next(c for c in sorted(claves_apertura)
                              if c in (SENALES_LARGO if direccion == "largo" else SENALES_CORTO))
                # Si 'motivo' viene EXCLUSIVAMENTE del vecino (no esta activa
                # en la propia vela), el stop/ATR/filtros de _abrir() deben
                # calcularse sobre la vela del VECINO, no sobre 'm' - son
                # escalas de precio y volatilidad distintas.
                motivo_de_vecino = (motivo in claves_apertura_vecino
                                     and motivo not in claves_apertura_propia)
                # Se consulta SIEMPRE que se intenta abrir, solo para marcar
                # la zona (nivel_escalera/distancia_escalera_bps) - ya no
                # bloquea nada (eso solo aplicaba a señales de reversion, que
                # ya no llegan a este punto).
                nivel, distancia = _proximo_nivel(m["velas"], m.get("atr"), precio,
                                                   direccion, cfg)
                if nivel is not None:
                    nivel_escalera, distancia_escalera = nivel, distancia
            if direccion is not None:
                # Entrada en LIMITE al mejor bid/ask (VETO_TF.md sec. 5), no a
                # mercado directo: se coloca una OrdenPendiente y se resuelve
                # en las vueltas siguientes (rebasa el nivel -> llena; señal
                # contraria -> cancela; sigue sin llenarse con confluencia
                # doble -> escala a mercado). Sin bid/ask (libro caido), cae
                # al precio actual - mismo comportamiento que antes de esto.
                conjunto_lado = SENALES_LARGO if direccion == "largo" else SENALES_CORTO
                confluencia = sum(1 for c in (nuevas_claves | nuevas_claves_vecino)
                                   if c in conjunto_lado)
                precio_limite = (m.get("ask") if direccion == "largo" else m.get("bid"))
                if precio_limite is None:
                    precio_limite = precio
                id_op = f"{coin}_{cfg['tf']}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
                # 'señal_snap' fija el stop/ATR/filtros sombre sobre la vela
                # que de verdad disparo 'motivo' - la del vecino si vino de
                # ahi, si no la propia (igual que ya hacia OrdenPendiente con
                # llenados tardios, ver docstring de esa clase).
                if motivo_de_vecino:
                    señal_snap = {"alto_c": vecino["alto_c"], "bajo_c": vecino["bajo_c"],
                                  "atr": vecino.get("atr"), "velas": vecino["velas"],
                                  "funding_pct": m.get("funding_pct"),
                                  "oi_serie": m.get("oi_serie"),
                                  "cvd_serie": m.get("cvd_serie")}
                else:
                    señal_snap = {"alto_c": m["alto_c"], "bajo_c": m["bajo_c"],
                                  "atr": m.get("atr"), "velas": m["velas"],
                                  "funding_pct": m.get("funding_pct"),
                                  "oi_serie": m.get("oi_serie"),
                                  "cvd_serie": m.get("cvd_serie")}
                ordenes_pendientes[coin] = orden = OrdenPendiente(
                    direccion, precio_limite, motivo, confluencia,
                    datetime.now().strftime("%H:%M:%S"), id_op, señal_snap)
                origen_txt = f" [vecino {vecino['tf']}]" if motivo_de_vecino else ""
                texto = (f"ORDEN LIMITE {direccion.upper()} por '{motivo}'{origen_txt}: "
                         f"{precio_limite:.4f} (confluencia={confluencia})")
                print(f"   >>> {texto}")
                eventos.append("orden_puesta")

        # pos_nueva: la posicion abierta ESTA vuelta (via orden llenada o
        # apertura directa), o None si solo se coloco/gestiono una orden o no
        # paso nada. El estancamiento nunca aplica aqui (exige >=30 min en
        # trade, imposible para algo recien abierto en esta misma vuelta) -
        # se gestiona a partir de la vuelta siguiente, ya como "pre-existente"
        # en el bucle de arriba.
        _fila_vuelta(pos_nueva, None, eventos, None, None, "", "",
                     id_apertura=id_apertura, veto_rvol=veto_rvol,
                     veto_volatilidad=veto_volatilidad, bloqueada=bloqueada,
                     desempate=desempate, nivel_escalera=nivel_escalera,
                     distancia_escalera=distancia_escalera,
                     escalera_bloqueada=escalera_bloqueada,
                     enfriamiento_bloqueada=enfriamiento_bloqueada)

    except Exception as e:
        print(f"[{coin}] (error gestionando la posicion: {e})")


def _preparar_rama(cfg_modo):
    """Monta el estado independiente de UNA rama (vistas/posiciones/capital
    por moneda, su propio CSV) - varias ramas pueden compartir la misma foto
    de mercado (ver _revisar) sin compartir NADA de su estado de trading."""
    vistas = {coin: set() for coin in cfg_modo["coins"]}
    vistas_vecino = {coin: set() for coin in cfg_modo["coins"]}
    # Calentamiento (2026-08-05, ver anotaciones.md (7)): guarda el timestamp
    # de la PRIMERA vela cerrada que ve este proceso para cada moneda -
    # mientras _revisar() siga viendo esa misma vela, cualquier señal de
    # continuacion activa puede ser vieja (el tramo ya llevaba rato corriendo
    # antes de arrancar) en vez de recien nacida. None hasta la primera
    # lectura real.
    vela_inicio = {coin: None for coin in cfg_modo["coins"]}
    # Enfriamiento tras cierre (ver PARAMS_AJUSTABLES/cfg["enfriamiento_continuacion"]):
    # claves de SENALES_CONTINUACION en "cooldown" para cada moneda, por
    # rama (cada rama abre/cierra de forma independiente, asi que el
    # enfriamiento tambien lo es).
    bloqueo_continuacion = {coin: set() for coin in cfg_modo["coins"]}
    # Lista, no un unico PosicionSim/None (2026-08-10): hasta
    # cfg["posiciones_max"] posiciones CONCURRENTES por moneda/rama.
    posiciones = {coin: [] for coin in cfg_modo["coins"]}
    ordenes_pendientes = {coin: None for coin in cfg_modo["coins"]}
    capitales = {coin: cfg_modo["capital"] for coin in cfg_modo["coins"]}
    ruta = _ruta_compatible(_archivo_registro(cfg_modo))
    nuevo = not os.path.exists(ruta)
    arch = open(ruta, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(arch, fieldnames=CAMPOS_CSV)
    if nuevo:
        writer.writeheader()
    print(f"Registro CSV: {ruta}")
    return {
        "cfg": cfg_modo,
        "vistas": vistas,
        "vistas_vecino": vistas_vecino,
        "vela_inicio": vela_inicio,
        "bloqueo_continuacion": bloqueo_continuacion,
        "posiciones": posiciones,
        "ordenes_pendientes": ordenes_pendientes,
        "capitales": capitales,
        "arch": arch,
        "writer": writer,
    }


_COMANDOS_DIR = "comandos"
_comandos_aplicados = set()   # nombres de fichero ya procesados por ESTE proceso


def _limpiar_comandos_viejos(dias=2):
    """Borra comandos/*.json de mas de 'dias' - se acumularian para siempre
    si no (el buzon no se vacia solo, varios procesos pueden necesitar leer
    el mismo comando). Se llama una vez al arrancar, no en el bucle."""
    if not os.path.isdir(_COMANDOS_DIR):
        return
    limite = time.time() - dias * 86400
    for nombre in os.listdir(_COMANDOS_DIR):
        ruta = os.path.join(_COMANDOS_DIR, nombre)
        try:
            if os.path.getmtime(ruta) < limite:
                os.remove(ruta)
        except OSError:
            pass


def _aplicar_comandos(ramas):
    """Revisa comandos/*.json escritos por telegram_control.py y aplica los
    que le tocan a ESTA ejecucion. Es un buzon COMPARTIDO (2026-08-03): varios
    monitor.py (5m/15m/1h/4h) pueden estar corriendo a la vez y todos leen la
    misma carpeta - cada uno solo actua si tiene la rama que pide el comando
    (desde el 2026-08-07 solo existe la rama "libre", ver main()). No
    se borran los ficheros aqui (ver _limpiar_comandos_viejos) - un proceso
    que arranca mas tarde tiene que poder verlos tambien; cada proceso lleva
    su propia lista en memoria de cuales ya aplico, para no repetirlos cada
    vuelta.

    'cfg' es un dict que TODAS las funciones consultan en vivo cada vuelta
    (no se copia a variables locales al arrancar - ver PARAMS_AJUSTABLES),
    asi que mutarlo aqui tiene efecto real desde la vuelta siguiente, sin
    reiniciar el proceso."""
    if not os.path.isdir(_COMANDOS_DIR):
        return
    for nombre in sorted(os.listdir(_COMANDOS_DIR)):
        if not nombre.endswith(".json") or nombre in _comandos_aplicados:
            continue
        _comandos_aplicados.add(nombre)
        ruta = os.path.join(_COMANDOS_DIR, nombre)
        try:
            with open(ruta, encoding="utf-8") as f:
                cmd = json.load(f)
        except Exception as e:
            print(f"(comando {nombre} ilegible: {e})")
            continue

        rama_pedida = cmd.get("rama")
        indicador = cmd.get("indicador")
        valor_crudo = cmd.get("valor")
        tf_pedido = cmd.get("tf")  # None (4 partes, rama/indicador/valor/tf) = todas las franjas
        if rama_pedida not in ramas:
            continue  # este proceso no tiene esa rama, nada que hacer
        if tf_pedido and ramas[rama_pedida]["cfg"]["tf"] != tf_pedido:
            continue  # comando dirigido a OTRA franja, no a este proceso
        if indicador not in PARAMS_AJUSTABLES:
            print(f"(comando {nombre} ignorado: indicador no permitido '{indicador}')")
            continue
        tipo, _desc = PARAMS_AJUSTABLES[indicador]
        try:
            valor = tipo(valor_crudo)
        except (TypeError, ValueError):
            print(f"(comando {nombre} ignorado: valor invalido para "
                  f"{indicador}: {valor_crudo!r})")
            continue

        anterior = ramas[rama_pedida]["cfg"].get(indicador)
        ramas[rama_pedida]["cfg"][indicador] = valor
        print(f"   >>> [telegram] rama {rama_pedida}: {indicador} "
              f"{anterior} -> {valor}")


def main():
    cfg = _parse_args(sys.argv[1:])
    cada_texto = f"{cfg['cada']}m" if cfg['cada'] >= 1 else f"{cfg['cada']*60:.0f}s"
    print(f"Monitor {','.join(cfg['coins'])} | tf={cfg['tf']} | ventana={cfg['ventana']}"
          f"{' | loop cada ' + cada_texto if cfg['loop'] else ''}")
    print(f"Posicion de PAPEL (sin dinero real): capital {cfg['capital']:.2f} USDT/moneda | "
          f"entrada {cfg['fraccion_entrada']*100:.1f}% margen | leverage {cfg['leverage']:.0f}x | "
          f"continuacion: imbalance >= {cfg['imb_umbral']} durante {cfg['imb_confirmaciones']} vueltas")
    # El arbitro tiene que ser un TF MAS LENTO que el que opera: la idea es que
    # aporte el contexto que el rapido no ve. Al reves no tiene sentido - este
    # aviso solo deberia poder dispararse si --tf-arbitro se fuerza a mano,
    # el automatico (_tf_arbitro_auto) ya elige uno mas lento por franja.
    if cfg["tf_arbitro"]:
        m_op, m_arb = _tf_minutos(cfg["tf"]), _tf_minutos(cfg["tf_arbitro"])
        if m_op and m_arb and m_arb <= m_op:
            print(f"(aviso) el arbitro ({cfg['tf_arbitro']}) NO es mas lento que el "
                  f"timeframe que opera ({cfg['tf']}): no puede aportar contexto "
                  f"que el propio tf no vea ya. Usa --tf-arbitro con un TF mayor, "
                  f"o '-' para desactivarlo.")
        else:
            print(f"Arbitro de desempate: {cfg['tf_arbitro']} por DI "
                  f"(separacion minima {cfg['di_separacion']})")
    else:
        print(f"Sin arbitro de desempate ({cfg['tf']} no tiene un TF mas lento "
              f"definido en _ARBITRO_AUTO, o se paso --tf-arbitro -).")

    print("Enfriamiento tras cierre: " + (
        "ON (no reabre en la misma racha de señal hasta que se apague)."
        if cfg["enfriamiento_continuacion"] else "OFF (--sin-enfriamiento)."))
    print(f"Aceleracion (señal nueva, sin backtest, junto a impulso_alza/baja): "
          f"ventana={cfg['aceleracion_ventana']} min_atr={cfg['aceleracion_min_atr']}x "
          f"mult={cfg['aceleracion_mult']}x")

    if cfg["loop"]:
        print("Telegram: " + ("OK, consultable via telegram_control.py." if avisos.configurado()
                               else "NO configurado (sin TELEGRAM_TOKEN/CHAT_ID)."))

    # Rama unica (2026-08-07, ver anotaciones.md "Rama unica"): hasta el
    # 2026-08-06 corrian 2-3 ramas en paralelo (libre/veto/sma) sobre la
    # MISMA foto de mercado para poder comparar sin ruido de sincronizacion
    # - ya se comparo (herramientas/backtest_sma_rama.py, 9 años BTC+ETH) y
    # la fusion de veto DI + filtro de regimen + salida RR fija gana a
    # cualquiera de las piezas sueltas en winrate, sin perder media, tanto
    # en 15m como en 1h. Se queda como la UNICA configuracion.
    #
    # La clave "libre" del dict se mantiene por compatibilidad: es el
    # fallback que telegram_control.py ya asume para un CSV sin sufijo
    # _veto/_sma (ver registro/csv_monitor.py:_archivo_registro) - ya no
    # significa "no obedece nada", es la fusion completa.
    if cfg["exit_rr"] <= 0:
        cfg["exit_rr"] = 3.0
    cfg["di_veta"] = True
    cfg["regimen_veta"] = True
    # ramas["libre"]["cfg"] ES 'cfg' (mismo objeto, YA NO una copia con
    # dict(cfg, ...) como hasta el 2026-08-09) - leer()/_arbitro()/
    # _vecino_senales()/_regimen(), mas abajo en el bucle, siguen leyendo
    # la variable 'cfg' del bucle principal, NO ramas["libre"]["cfg"]. Con
    # dos dict distintos, _aplicar_comandos() (que muta
    # ramas["libre"]["cfg"]) nunca llegaba a esas 4 funciones: un ajuste
    # por Telegram de tf_arbitro/tf_vecino_rapido/regimen_tf/regimen_sma/
    # di_separacion/impulso_*/aceleracion_*/etc. se confirmaba en el chat y
    # se imprimia en consola, pero no cambiaba nada en vivo - confirmado el
    # 2026-08-09 (arbitro_tf en el CSV nunca se movio del valor AUTO pese a
    # varios cambios por Telegram durante 27h). Params que solo usa
    # _revisar() (exit_rr, stop_atr, trailing_*, fraccion_entrada, etc.) no
    # tenian este problema - ya llegaban via ramas["libre"]["cfg"].
    ramas = {"libre": _preparar_rama(cfg)}
    print(f"Rama unica: veto DI del arbitro + filtro de regimen "
          f"(SMA{cfg['regimen_sma']} {cfg['regimen_tf']}) + salida RR fija "
          f"1:{cfg['exit_rr']:.1f} (exit_rr=0 por Telegram cambia a "
          f"trailing/escalera, ver estrategia/escalera.py).")

    _limpiar_comandos_viejos()

    try:
        while True:
            _aplicar_comandos(ramas)
            for coin in cfg["coins"]:
                try:
                    m = leer(coin, cfg)
                    arb = _arbitro(coin, cfg)
                    vecino = _vecino_senales(coin, cfg)
                    regimen = _regimen(coin, cfg)
                except Exception as e:
                    print(f"[{coin}] (error leyendo mercado: {e})")
                    continue
                for r in ramas.values():
                    _revisar(coin, r["cfg"], m, arb, vecino, regimen, r["vistas"][coin],
                             r["vistas_vecino"][coin], r["vela_inicio"],
                             r["bloqueo_continuacion"][coin],
                             r["posiciones"], r["capitales"],
                             r["ordenes_pendientes"], r["writer"], r["arch"])

            if not cfg["loop"]:
                break
            sleep_normal = _proximo_sleep(cfg)
            hay_orden_pendiente = any(
                o is not None
                for r in ramas.values()
                for o in r["ordenes_pendientes"].values())
            if hay_orden_pendiente and cfg["cada_orden_pendiente"] > 0:
                sleep_acelerado = cfg["cada_orden_pendiente"] * 60
                if sleep_acelerado < sleep_normal:
                    print(f"(orden pendiente en curso: sondeo acelerado a "
                          f"{sleep_acelerado:.0f}s en vez de {sleep_normal:.0f}s)")
                time.sleep(min(sleep_normal, sleep_acelerado))
            else:
                time.sleep(sleep_normal)
    finally:
        for r in ramas.values():
            r["arch"].close()


if __name__ == "__main__":
    main()
