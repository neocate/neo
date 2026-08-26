import json
import os
import socket
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

DIR_NIVELES = Path(__file__).resolve().parent
DIR_LOGS = DIR_NIVELES / "logs"
DIR_JSON = DIR_NIVELES / "json"


def _crear_directorios():
    for d in (DIR_LOGS, DIR_JSON):
        d.mkdir(parents=True, exist_ok=True)


class LockFile:
    def __init__(self, path, intervalo=60):
        self.path = path
        self.intervalo = intervalo

    def _contenido(self):
        try:
            with open(self.path) as f:
                partes = f.read().strip().split("|")
            return int(partes[0]), (partes[1] if len(partes) > 1 else "")
        except (OSError, ValueError, IndexError):
            return None, ""

    def _caducado(self):
        margen = max(300.0, self.intervalo * 3)
        try:
            return (time.time() - os.stat(self.path).st_mtime) > margen
        except OSError:
            return True

    def _vivo(self):
        pid, host = self._contenido()
        if pid is not None and host == socket.gethostname():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except OSError:
                pass
        return not self._caducado()

    def _firma(self):
        pid, host = self._contenido()
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            mtime = None
        return (pid, host, mtime)

    def latir(self):
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def adquirir(self, timeout=5):
        inicio = time.time()
        while time.time() - inicio < timeout:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(f"{os.getpid()}|{socket.gethostname()}")
                return True
            except FileExistsError:
                if not self._vivo():
                    firma = self._firma()
                    pid, host, _ = firma
                    # Releemos la firma (pid|host|mtime) justo antes de
                    # borrar: si cambio desde que se determino huerfano, otro
                    # proceso ya lo retiro y recreo el suyo -> no lo tocamos,
                    # dejamos que el bucle normal (sleep + reintento de
                    # O_CREAT|O_EXCL, que ya es atomico) se encargue. Esto
                    # estrecha la ventana de carrera, no la elimina del todo:
                    # no hay primitiva atomica portable (Windows/Linux) para
                    # un "borra solo si sigue siendo el mismo" sin depender
                    # de fcntl (no portable) ni de os.replace (rompe la
                    # exclusion mutua porque nunca falla si el destino existe).
                    if self._firma() == firma:
                        print(f"  [AVISO] lock huerfano (PID {pid} de {host or 'equipo desconocido'}), se retira",
                              flush=True)
                        try:
                            os.remove(self.path)
                        except FileNotFoundError:
                            pass
                        continue
                time.sleep(0.5)
        return False

    def liberar(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def _guardar_atomico(ruta, datos):
    ruta = Path(ruta)
    fd, tmp = tempfile.mkstemp(dir=str(ruta.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(datos, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _cargar_json(ruta, default=None):
    if not os.path.exists(ruta):
        return default
    try:
        with open(ruta, encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def leer_ultimo(coin, tf, mercado):
    candidatos = sorted(DIR_JSON.glob(f"nivel_{coin.upper()}_{tf}_{mercado}_k*_toques*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidatos:
        return None
    if len(candidatos) > 1:
        print(f"  [AVISO] {tf}: {len(candidatos)} ficheros con distinto k/toques, "
              f"uso el mas reciente ({candidatos[0].name})", flush=True)
    return _cargar_json(candidatos[0])


def _log(coin, mensaje, consola=False, exc_info=False):
    ruta_log = DIR_LOGS / f"niveles_{coin}.log"
    if consola:
        print(f"  {mensaje}", flush=True)
    texto = f"{mensaje}\n{traceback.format_exc()}" if exc_info else mensaje
    try:
        with open(ruta_log, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {texto}\n")
    except OSError as e:
        print(f"  [AVISO] no se pudo escribir el log: {e}", flush=True)
