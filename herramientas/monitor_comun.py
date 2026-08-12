# ---------------------------------------------------------------
# monitor_comun.py - Funciones compartidas por monitor_niveles.py (toques
# de nivel) y monitor_senales.py (señales de vela) - los dos leen el mismo
# flujo_*.csv de grabador_libro.py y el mismo historico_*_bitget.csv de
# descargar_bit.py --feed, cada uno vigilando algo distinto (ver cabecera
# de cada uno). Nada de esto es logica de trading - solo lectura en vivo
# y comprobacion de que los procesos de los que dependen esten corriendo
# (2026-08-12: ya NO los arranca por su cuenta, ver _requerir_grabador_libro
# mas abajo).
#
# 2026-08-11: monitor_niveles.py hacia estas dos cosas fusionadas en un
# solo proceso (niveles + señales, añadido en el commit 7a11670 - nunca
# hubo una version separada anterior, se extrajo de cero). Se separan por
# el mismo motivo que ya justifico separar grabador_libro.py de monitor.py:
# señales es logica que se ajusta/prueba con frecuencia, niveles es
# mecanico y estable - un ajuste de señales no deberia obligar a reiniciar
# (y cortar la serie de) el vigilante de niveles.
# ---------------------------------------------------------------

import csv
import io
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.grabador_libro import CAMPOS_CSV as CAMPOS_LIBRO
from herramientas.descargar_bit import DIR_LIBRO

CAMPOS_AVISOS = ["timestamp_ms", "fecha_utc", "coin", "evento", "tipo", "origen",
                  "nivel_precio", "precio_actual", "imbalance", "cvd", "pid"]


def _flt(s):
    """Parsea un campo del CSV de grabador_libro.py a float, o None si esta
    vacio (API fallo esa vuelta - ver grabador_libro.py._fila)."""
    if s in (None, ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _localizar_csv_libro(coin):
    """Busca en herramientas/libro/ el CSV de grabador_libro.py vigente para
    esta moneda - normalmente flujo_<COIN>.csv (un fichero por moneda desde
    2026-08-12, ver grabador_libro.py._archivo). El patron tambien acepta
    los formatos viejos (flujo_<MONEDA1-MONEDA2>.csv sin fecha, o con fecha
    delante, de antes de esos cambios) por si queda algun fichero de
    entonces. Si hay varios candidatos (_v2/_v3 por cabecera cambiada, o
    restos del formato viejo conviviendo con el nuevo), toma el modificado
    mas reciente. None si no encuentra ninguno."""
    if not os.path.isdir(DIR_LIBRO):
        return None
    candidatos = []
    for nombre in os.listdir(DIR_LIBRO):
        m = re.match(r"^flujo_(?:\d+_)?(.+?)(?:_v\d+)?\.csv$", nombre)
        if not m:
            continue
        monedas = m.group(1).split("-")
        if coin.upper() in monedas:
            ruta = os.path.join(DIR_LIBRO, nombre)
            candidatos.append((os.path.getmtime(ruta), ruta))
    if not candidatos:
        return None
    candidatos.sort()
    return candidatos[-1][1]


def _tail_csv(ruta, offset, fieldnames=CAMPOS_LIBRO):
    """Lee las lineas COMPLETAS agregadas a 'ruta' desde 'offset' bytes (una
    fila a medio escribir se deja sin consumir para la proxima vuelta).
    Devuelve (filas_dict, nuevo_offset). 'fieldnames' por defecto es el
    esquema de flujo_*.csv (CAMPOS_LIBRO) - monitor_telegram.py lo llama
    con CAMPOS_AVISOS para leer senales_*.csv en vez de flujo."""
    with open(ruta, "rb") as f:
        f.seek(offset)
        data = f.read()
    if not data:
        return [], offset
    corte = data.rfind(b"\n")
    if corte == -1:
        return [], offset
    bloque, nuevo_offset = data[:corte + 1], offset + corte + 1
    texto = bloque.decode("utf-8", errors="replace")
    filas = list(csv.DictReader(io.StringIO(texto), fieldnames=fieldnames))
    return filas, nuevo_offset


def _ultima_fila_coin(ruta):
    """Ultima fila COMPLETA de 'ruta' (flujo_<COIN>.csv de grabador_libro.py,
    UNA sola moneda desde 2026-08-12 - ya no hace falta filtrar por coin
    dentro del fichero) - lee solo la cola del fichero (puede tener decenas
    de MB tras dias corriendo) en vez de todo. Se descarta la primera linea
    de la cola por si quedo cortada a medias por el propio seek. None si no
    hay ninguna fila valida."""
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 262_144))
        cola = f.read()
    # .rstrip("\r"): csv.writer termina cada fila en '\r\n' pese a newline=""
    # al abrir - el split por '\n' deja un '\r' colgando que rompia la
    # comparacion contra 'cabecera' de mas abajo (2026-08-12).
    lineas = [l.rstrip("\r") for l in cola.decode("utf-8", errors="replace").split("\n") if l.strip()]
    # Mismo caso que grabador_libro._ultimo_cvd: si la cola capturo solo la
    # cabecera (fichero recien creado, sin filas de datos todavia), hay que
    # descartarla explicitamente - contar lineas no basta, con 1 sola linea
    # esa cabecera pasaria el chequeo de longitud de campos y se leeria
    # como si fuera una fila real (2026-08-12, encontrado en flujo_ICP.csv).
    cabecera = ",".join(CAMPOS_LIBRO)
    if lineas and lineas[0] == cabecera:
        lineas = lineas[1:]
    elif len(lineas) > 1:
        lineas = lineas[1:]
    for linea in reversed(lineas):
        campos = next(csv.reader([linea]))
        if len(campos) != len(CAMPOS_LIBRO):
            continue
        return dict(zip(CAMPOS_LIBRO, campos))
    return None


def _listar_procesos():
    """Lineas de 'ps -ef' (todos los procesos del sistema), o [] si falla.

    OJO: tiene que ser 'ps -ef', NUNCA 'ps w' a secas - 'ps w' solo lista
    los procesos de la sesion/terminal ACTUAL, y grabador_libro.py/
    descargar_bit.py --feed corren siempre demonizados desde OTRA sesion
    SSH (PPID=1, sin TTY). Con 'ps w' este chequeo daba un falso "no esta
    corriendo" y disparaba un segundo grabador_libro.py real (2026-08-12,
    encontrado via 'ps -ef' - ver memoria del proyecto sobre el incidente
    de corrupcion de CVD por el mismo motivo, un dia antes, con el propio
    operador comprobando a mano)."""
    try:
        salida = subprocess.run(["ps", "-ef"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    return salida.splitlines()


def _proceso_corriendo(fragmentos):
    """True si hay un proceso vivo cuya linea de comando contiene TODOS
    los 'fragmentos' (lista de substrings) - p.ej. ["descargar_bit.py",
    "--feed"] para distinguir el modo daemon del uso puntual. DSM/busybox
    no tiene pgrep (ver anotaciones.md), se parsea 'ps -ef' a mano (ver
    _listar_procesos). Si 'ps' fallara por lo que sea, asume que NO esta
    corriendo (mejor avisar de mas que quedarse esperando para siempre por
    un falso negativo)."""
    return any(all(frag in linea for frag in fragmentos) for linea in _listar_procesos())


# 2026-08-12: cambio de filosofia - ANTES estos monitores auto-arrancaban
# grabador_libro.py/descargar_bit.py --feed si no los encontraban corriendo
# (_asegurar_grabador_libro/_asegurar_feed_velas, ya retirados). Fran, tras
# liarse relanzando manualmente varios procesos en cascada y dejarse uno
# sin relanzar por error: "cada py que lanzamos depende de otro,
# grabador_libro el principal, para monitor_niveles y monitor_senales si no
# existe grabador_libro corriendo, un aviso y que se pare la ejecucion...
# de esta forma evito dejar sin relanzar por error un py". Cadena pedida:
# grabador_libro.py + descargar_bit.py --feed (raiz) -> monitor_niveles.py/
# monitor_senales.py (comprueban la raiz) -> validador_niveles.py/
# marcador_tpsl.py (comprueban que monitor_niveles.py Y monitor_senales.py
# esten vivos). Solo se comprueba AL ARRANCAR (confirmado explicitamente),
# no de forma continua durante la ejecucion - si una dependencia se cae
# DESPUES de arrancar, este monitor no se entera ni se para solo.


def _pid_vivo(pid):
    """True si el proceso 'pid' sigue vivo, SIN arriesgarse a matarlo.

    En POSIX (el NAS, donde corre grabador_libro.py de verdad), os.kill(pid,
    0) es el patron estandar: la señal 0 no se entrega, solo prueba
    existencia/permisos. En Windows (donde este chequeo tambien puede
    correr - ver anotaciones.md, monitor_niveles.py soportado desde
    PowerShell), os.kill() con cualquier señal que no sea
    CTRL_C_EVENT/CTRL_BREAK_EVENT llama de verdad a TerminateProcess() - si
    el PID leido del .lock (escrito por un proceso Linux del NAS)
    coincidiera por casualidad con un proceso vivo en la maquina Windows,
    esto lo mataria en vez de solo comprobarlo. Se evita abriendo el
    proceso con permisos de SOLO CONSULTA (PROCESS_QUERY_LIMITED_INFORMATION)
    en vez de con os.kill."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    import ctypes.wintypes as wt
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    abrir = ctypes.windll.kernel32.OpenProcess
    # restype/argtypes explicitos: sin esto ctypes asume que OpenProcess
    # devuelve un 'int' de 32 bits, pero un HANDLE de Windows es de 64 bits
    # en sistemas x64 - en la practica los handles que da el kernel caben
    # en 32 bits (no se ha visto truncarse), pero mejor no depender de eso.
    abrir.restype = wt.HANDLE
    abrir.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    handle = abrir(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _requerir_grabador_libro(coin):
    """True si hay un grabador_libro.py vivo para 'coin' - comprueba el
    lock DIR_LIBRO/grabador_libro_<COIN>.lock (por moneda, ver
    grabador_libro._bloquear_instancia_unica) y que el PID de dentro siga
    vivo (ver _pid_vivo). NO se puede comprobar con
    _proceso_corriendo(["grabador_libro.py", coin]): si se lanzo combinado
    ("grabador_libro.py btc,eth"), la moneda no aparece como token suelto
    en la linea de comando tal cual se escribio - el lock, en cambio, es
    siempre por moneda, se lance como se lance (y Fran ha confirmado que a
    partir de ahora lanza cada moneda como proceso independiente, lo que
    hace este chequeo aun mas fiable)."""
    ruta_lock = os.path.join(DIR_LIBRO, f"grabador_libro_{coin.upper()}.lock")
    if not os.path.exists(ruta_lock):
        return False
    try:
        with open(ruta_lock) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    return _pid_vivo(pid)


def _requerir_feed_velas():
    """True si hay CUALQUIER descargar_bit.py --feed vivo. No distingue
    que monedas cubre en concreto (mismo hueco conocido que ya tenia
    _asegurar_feed_velas - ver memoria del proyecto: si el feed vivo no
    cubre la moneda de este monitor, esto no lo detecta)."""
    return _proceso_corriendo(["descargar_bit.py", "--feed"])
