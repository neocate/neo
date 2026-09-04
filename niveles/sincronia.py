# ---------------------------------------------------------------------------
# sincronia.py - Comprueba que los JSON de niveles de varios TF se pueden
# mezclar sin colar un desfase.
#
# Cada TF se recalcula solo cuando CIERRA SU PROPIA vela (ver Vigilante en
# niveles.py), asi que el 'precio_actual' de un JSON de 1h y uno de 5m casi
# nunca corresponden al mismo instante: el de 1h puede tener hasta una hora
# de antiguedad antes de considerarse normal, el de 5m solo cinco minutos.
# Comparar sus precios sin mirar esto no es un fallo, es como funciona el
# sistema. Pero si un TF lleva MAS periodos suyos de los normales sin
# actualizar, eso si es señal de que su Vigilante esta parado (loop caido,
# CSV sin velas nuevas...) y su precio no vale para nada.
#
# Este fichero separa las dos cosas: informa del desfase esperado entre TF
# (informativo) y avisa por separado de los TF cuyo propio JSON esta mas
# parado de lo que su periodo justifica (bloqueante).
#
# Uso:
#   python niveles/sincronia.py eth 1m,3m,5m,15m,1h,4h
#   python niveles/sincronia.py eth --mercado futuros
#   python niveles/sincronia.py eth                       todos los TF con JSON
#
# Salida: tabla por TF + lista de los seguros de mezclar. Exit code 1 si
# algun TF pedido esta parado o sin JSON.
# python niveles/sincronia.py eth "1m","3m","5m","15m","1h","4h","1d"
# ---------------------------------------------------------------------------

import sys
from datetime import datetime, timezone
from pathlib import Path

DIR_NIVELES = Path(__file__).resolve().parent
DIR_NEO = DIR_NIVELES.parent

sys.path.insert(0, str(DIR_NIVELES))
sys.path.insert(0, str(DIR_NEO / "velas"))
from velas_bit import MERCADOS, TF_SEGUNDOS, resolver_tfs
from persistencia import leer_ultimo

MERCADO_POR_DEFECTO = "futuros"

# Cuantos periodos propios de retraso se toleran (contados desde el CIERRE de
# la ultima vela, no desde su apertura) antes de sospechar que el Vigilante de
# ese TF esta parado. 2 da margen a que el equipo que lo corre ande liado con
# el resto de TF y aun asi detecta un proceso muerto de verdad.
#
# Con solo el factor de periodos, un 1m parece siempre "parado": el margen
# fijo de confirmacion de velas_bit.py (MARGEN_CIERRE+CONFIRMA_SEG, ~20s) mas
# el intervalo del propio loop de niveles.py pesan poco sobre un periodo de
# 1h pero son mas de un periodo entero sobre uno de 1m. Igual que
# LockFile._caducado en niveles.py, se pone un suelo fijo que nunca baja de
# esto -sea cual sea el TF- para no confundir ese margen operativo con un
# proceso muerto.
UMBRAL_PERIODOS = 2.0
MARGEN_MINIMO_SEG = 300


def _tf_ms(tf):
    return TF_SEGUNDOS[tf] * 1000


def _fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _duracion(ms):
    seg = ms / 1000
    if seg < 3600:
        return f"{seg / 60:.1f} min"
    if seg < 86400:
        return f"{seg / 3600:.1f} h"
    return f"{seg / 86400:.1f} dias"


def leer_snapshot(coin, tf, mercado):
    """Datos minimos de un JSON de niveles para juzgar si esta al dia.
    None si no hay fichero (o esta roto) para ese TF."""
    datos = leer_ultimo(coin, tf, mercado)
    if datos is None:
        return None

    ts = datos.get("ts_ultima_vela")
    precio = datos.get("precio_actual")
    if ts is None or precio is None:
        print(f"  [ERROR] {tf}: JSON de niveles sin ts_ultima_vela o precio_actual", flush=True)
        return None

    return {"tf": tf, "ts_ultima_vela": ts, "precio_actual": precio}


def es_reciente(ts_ultima_vela, tf, ahora_ms=None):
    """True si 'ts_ultima_vela' no lleva mas de UMBRAL_PERIODOS periodos
    propios de retraso desde que CERRO (no desde que abrio: el ts guardado
    es la apertura, igual que en el CSV de velas_bit.py), con un suelo
    minimo en segundos para que un TF corto no salga siempre 'parado' por
    el margen fijo de confirmacion de velas_bit.py."""
    ahora_ms = ahora_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    tf_ms = _tf_ms(tf)
    cierre_ms = ts_ultima_vela + tf_ms
    margen_ms = max(UMBRAL_PERIODOS * tf_ms, MARGEN_MINIMO_SEG * 1000)
    return (ahora_ms - cierre_ms) <= margen_ms


def evaluar(coin, tfs, mercado, ahora_ms=None):
    """Un snapshot y su estado por TF, mas cuales son seguros de mezclar.

    'seguros' son los que existen y no llevan mas de UMBRAL_PERIODOS periodos
    suyos de retraso desde que CERRO su ultima vela. Ese es el unico criterio
    de fallo real, no la diferencia de fecha entre TF distintos, que es
    normal por diseño (cada uno se actualiza solo al cerrar su propia vela).
    """
    ahora_ms = ahora_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    filas = []
    for tf in tfs:
        snap = leer_snapshot(coin, tf, mercado)
        if snap is None:
            filas.append({"tf": tf, "estado": "SIN_DATOS"})
            continue
        tf_ms = _tf_ms(tf)
        snap["antig_ms"] = ahora_ms - (snap["ts_ultima_vela"] + tf_ms)
        snap["periodos"] = snap["antig_ms"] / tf_ms
        snap["estado"] = "OK" if es_reciente(snap["ts_ultima_vela"], tf, ahora_ms) else "PARADO"
        filas.append(snap)

    seguros = [f for f in filas if f["estado"] == "OK"]
    return filas, seguros


def informe(coin, tfs, mercado):
    ahora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    filas, seguros = evaluar(coin, tfs, mercado, ahora_ms)

    print(f"\n[SINCRONIA] {coin.upper()} {mercado} - {_fecha(ahora_ms)} UTC\n")
    print(f"{'TF':<5} {'ultima vela':<20} {'antiguedad':<12} {'periodos':<9} {'precio':<12} estado")
    print("-" * 78)
    for f in filas:
        if f["estado"] == "SIN_DATOS":
            print(f"{f['tf']:<5} {'-':<20} {'-':<12} {'-':<9} {'-':<12} SIN DATOS")
            continue
        print(f"{f['tf']:<5} {_fecha(f['ts_ultima_vela']):<20} "
              f"{_duracion(f['antig_ms']):<12} {f['periodos']:<9.1f} "
              f"{f['precio_actual']:<12.4f} {f['estado']}")

    parados = [f for f in filas if f["estado"] == "PARADO"]
    faltantes = [f for f in filas if f["estado"] == "SIN_DATOS"]

    print()
    for f in parados:
        print(f"  [BLOQUEA] {f['tf']}: {_duracion(f['antig_ms'])} sin actualizar desde "
              f"que cerro su ultima vela ({f['periodos']:.1f} periodos propios) - su "
              f"Vigilante puede estar parado, no mezclar su precio con el resto")
    if faltantes:
        print(f"  [BLOQUEA] sin JSON: {', '.join(f['tf'] for f in faltantes)}")

    if seguros:
        tfs_ok = ", ".join(f["tf"] for f in seguros)
        spread_ms = (max(f["ts_ultima_vela"] for f in seguros)
                     - min(f["ts_ultima_vela"] for f in seguros))
        print(f"\n  Seguros de mezclar: {tfs_ok}")
        print(f"  Desfase entre sus propias ultimas velas: {_duracion(spread_ms)} "
              f"(normal: cada TF cierra a su ritmo, no es un fallo)")
    else:
        print("\n  Ningun TF seguro de mezclar ahora mismo.")

    return not parados and not faltantes


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__ or "Uso: python sincronia.py <coin> [TF,TF,...] [--mercado spot|futuros]")
        return 0 if args else 1

    coin = args[0]
    resto = args[1:]
    mercado = MERCADO_POR_DEFECTO
    tfs_str = None

    i = 0
    while i < len(resto):
        if resto[i] == "--mercado":
            if i + 1 >= len(resto):
                print("[ERROR] --mercado requiere un valor (spot|futuros)")
                return 1
            i += 1
            mercado = resto[i]
        elif not resto[i].startswith("--"):
            tfs_str = resto[i]
        i += 1

    if mercado not in MERCADOS:
        print(f"[ERROR] mercado invalido: {mercado!r}. Usa: {', '.join(MERCADOS)}")
        return 1

    try:
        tfs = resolver_tfs(tfs_str)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1

    ok = informe(coin, tfs, mercado)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
