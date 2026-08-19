# ================================================================================
# revisar_push.py
#
# Deja por escrito QUE viaja en el push y que se queda fuera, para que el
# repo lleve todo lo ligero (config, json de parametros, scripts, notas) y
# nada pesado (velas, libro, historicos - eso va por FTP).
#
# NO toca el .gitignore. Solo mira y escribe un inventario en:
#
#     contenido_push.txt
#
# ...con el arbol de directorios y ficheros que SI viajan, sus tamaños y el
# total. Ese fichero se revisa a mano y, si algo sobra o falta, se ajusta el
# .gitignore uno mismo. Que un script edite las reglas del repo por su cuenta
# es demasiado poder para lo poco que ahorra.
#
# Se apoya en git para saber el estado real de cada fichero (check-ignore y
# ls-files): no reimplementa las reglas del .gitignore, que tienen mas
# esquinas de las que parece (negaciones, barras, precedencias).
#
# Ademas avisa por consola de los dos fallos que de verdad pasan:
#
#   1. PESADO QUE SE SUBE     un fichero grande que ningun patron atrapa. Es
#                             el que hincha el repo y ya no hay quien lo
#                             saque: queda en el historial para siempre.
#   2. LIGERO QUE NO VIAJA    un json de config o un .txt de notas que un
#                             patron global se ha llevado por delante sin
#                             querer. Se pierde en silencio: clonas en otro
#                             equipo y no esta.
#
# Y de carpetas que no llegaran al clon por estar vacias para git (todo su
# contenido ignorado y sin .gitkeep).
#
# El peso sirve para DETECTAR, nunca para decidir: quien excluye sigue siendo
# un patron por tipo de fichero. Un csv de velas recien creado ocupa 2 KB y
# pasaria cualquier filtro de tamaño, para ser 300 MB ya versionados una
# semana despues.
#
# Y cuando el arbol esta limpio, hace el trabajo: add + commit + push.
# Si detecta pesados sueltos NO sube nada y para: subirlos es lo unico de
# todo esto que no tiene arreglo despues, porque quedan en el historial
# aunque los borres en el commit siguiente.
#
# USO
#   python revisar_push.py                    # listado + commit + push
#   python revisar_push.py --m "que he hecho" # con mensaje de commit propio
#   python revisar_push.py --solo-listado     # solo mira, no toca el repo
#   python revisar_push.py --sin-push         # commitea en local, no sube
#   python revisar_push.py --forzar           # sube aunque haya pesados
#   python revisar_push.py --max 512          # umbral de pesado en KB (def. 1024)
# ================================================================================

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
LISTADO = RAIZ / 'contenido_push.txt'

# No se recorren: no son parte del proyecto ni de lo que se versiona.
# .claude es configuracion de la herramienta en ESTA maquina, no del proyecto.
PODAR = {'.git', '.claude', 'venv', '.venv', '__pycache__', 'node_modules',
         '.pytest_cache'}

# Nunca viajan, pesen lo que pesen HOY. Son ficheros que solo crecen, asi que
# decidir por peso no sirve para ellos: se quedan fuera por lo que son, no
# por lo que miden.
SIEMPRE_FUERA = {'.csv', '.log', '.lock'}

# Extensiones que casi siempre son fuente/config: si una de estas es ligera
# y esta ignorada, casi seguro es un patron global llevandosela por delante.
UTILES = {'.py', '.json', '.txt', '.md', '.cfg', '.ini', '.yaml', '.yml',
          '.toml', '.example', '.sh', '.ps1', '.bat', '.sql'}

# Ignorados a proposito aunque sean ligeros.
NUNCA_SUBIR = {'.env'}

MAX_KB_DEFECTO = 1024


def _git(args):
    """Ejecuta git y devuelve (codigo, stdout). No lanza excepcion."""
    r = subprocess.run(['git'] + args, cwd=str(RAIZ), capture_output=True, text=True)
    return r.returncode, r.stdout


def _git_full(args):
    """Como _git pero devuelve tambien stderr: git manda ahi casi todo lo
    interesante de push y commit."""
    r = subprocess.run(['git'] + args, cwd=str(RAIZ), capture_output=True, text=True)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def _es_repo():
    return _git(['rev-parse', '--git-dir'])[0] == 0


def _recorrer():
    """Todos los ficheros del arbol, como rutas relativas con / (git las
    quiere asi en cualquier plataforma)."""
    ficheros = []
    for base, dirs, nombres in os.walk(RAIZ):
        dirs[:] = [d for d in dirs if d not in PODAR]
        for nombre in nombres:
            ruta = Path(base) / nombre
            try:
                ficheros.append((ruta.relative_to(RAIZ).as_posix(), ruta.stat().st_size))
            except OSError:
                pass
    return sorted(ficheros)


def _ignorados(rutas):
    """Subconjunto de 'rutas' que git ignora. Una sola llamada para todas.

    En BYTES a proposito: con text=True, Python traduce en Windows el '\\n'
    de stdin a '\\r\\n', git recibe cada ruta con un '\\r' pegado al final, no
    casa con ningun patron y devuelve vacio. El script se creia entonces que
    no se ignora nada y daba por buenos para el push los csv de velas.
    """
    if not rutas:
        return set()
    entrada = ('\n'.join(rutas) + '\n').encode('utf-8')
    r = subprocess.run(['git', 'check-ignore', '--stdin'], cwd=str(RAIZ),
                       input=entrada, capture_output=True)
    return {l.strip() for l in r.stdout.decode('utf-8', 'replace').splitlines()
            if l.strip()}


def _regla(rel):
    """Que regla ignora ese fichero: (fichero_de_reglas, texto). ('','') si
    ninguna."""
    salida = _git(['check-ignore', '-v', rel])[1]
    if not salida.strip():
        return '', ''
    cabeza = salida.split('\t')[0]
    return cabeza.rsplit(':', 2)[0].strip('"'), cabeza.strip()


def _rescatable(rel):
    """True si la regla que lo ignora vive DENTRO del repo.

    Si lo ignora el .gitignore global del usuario (core.excludesFile) o el
    .git/info/exclude, es una decision deliberada de esta maquina: config
    local, credenciales, cosas de la herramienta. No se propone tocarlos.
    """
    fichero = _regla(rel)[0]
    if not fichero:
        return False
    try:
        return Path(fichero).resolve().is_relative_to(RAIZ)
    except (OSError, ValueError):
        return False


def _versionados():
    return {l.strip() for l in _git(['ls-files'])[1].splitlines() if l.strip()}


def _tam(n):
    if n >= 1048576:
        return f"{n/1048576:.1f} MB"
    if n >= 1024:
        return f"{n/1024:.0f} KB"
    return f"{n} B"


def _carpetas_sin_marca(ficheros, ignorados, versionados):
    """Carpetas cuyo contenido esta todo ignorado y no tienen .gitkeep: no
    existiran al clonar en otro equipo."""
    con_marca, contenido = set(), {}
    for rel, _ in ficheros:
        carpeta = os.path.dirname(rel)
        if not carpeta:
            continue
        contenido.setdefault(carpeta, []).append(rel)
        if os.path.basename(rel) == '.gitkeep':
            con_marca.add(carpeta)
    return [c for c, dentro in sorted(contenido.items())
            if c not in con_marca
            and not any(r not in ignorados or r in versionados for r in dentro)]


def _analizar(max_bytes):
    ficheros = _recorrer()
    ignorados = _ignorados([r for r, _ in ficheros])
    versionados = _versionados()

    pesados_nuevos, pesados_dentro, ligeros_fuera, viajan = [], [], [], []

    for rel, tam in ficheros:
        nombre = os.path.basename(rel)
        ext = os.path.splitext(nombre)[1].lower()
        ignorado, versionado = rel in ignorados, rel in versionados

        if not ignorado or versionado:
            viajan.append((rel, tam))
            if tam > max_bytes:
                (pesados_dentro if versionado else pesados_nuevos).append((rel, tam))
        elif (tam <= max_bytes and ext in UTILES and ext not in SIEMPRE_FUERA
                and nombre not in NUNCA_SUBIR and _rescatable(rel)):
            ligeros_fuera.append((rel, tam, _regla(rel)[1]))

    return {
        'pesados_nuevos': sorted(pesados_nuevos, key=lambda x: -x[1]),
        'pesados_dentro': sorted(pesados_dentro, key=lambda x: -x[1]),
        'ligeros_fuera': ligeros_fuera,
        'viajan': viajan,
        'sin_marca': _carpetas_sin_marca(ficheros, ignorados, versionados),
    }


def _escribir_listado(a, max_bytes):
    """contenido_push.txt: el arbol de lo que SI viaja, para revisarlo."""
    por_carpeta = {}
    for rel, tam in a['viajan']:
        por_carpeta.setdefault(os.path.dirname(rel) or '.', []).append((rel, tam))

    peso = sum(t for _, t in a['viajan'])
    lineas = [
        "CONTENIDO DEL PUSH",
        "=" * 66,
        f"Generado por revisar_push.py el {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{len(a['viajan'])} fichero(s) en {len(por_carpeta)} carpeta(s), {_tam(peso)}",
        "",
        "Esto es lo que SI viaja en el push. Lo que no aparece aqui esta",
        "ignorado y hay que moverlo por FTP (velas, libro, historicos).",
        "Si algo sobra o falta, se ajusta el .gitignore a mano.",
        "=" * 66,
        "",
    ]

    for carpeta in sorted(por_carpeta):
        dentro = sorted(por_carpeta[carpeta])
        suma = sum(t for _, t in dentro)
        lineas.append(f"{carpeta if carpeta != '.' else '(raiz)'}/"
                      f"    [{len(dentro)} fichero(s), {_tam(suma)}]")
        for rel, tam in dentro:
            lineas.append(f"    {os.path.basename(rel):<34} {_tam(tam):>9}")
        lineas.append("")

    fuera = []
    if a['pesados_dentro']:
        fuera.append(f"  [!] {len(a['pesados_dentro'])} pesado(s) YA versionado(s) "
                     f"- hace falta git rm --cached")
    if a['pesados_nuevos']:
        fuera.append(f"  [!] {len(a['pesados_nuevos'])} pesado(s) que se subirian "
                     f"sin que ningun patron los atrape")
    if a['ligeros_fuera']:
        fuera.append(f"  [!] {len(a['ligeros_fuera'])} ligero(s) de config que NO "
                     f"viajan y probablemente deberian")
    if a['sin_marca']:
        fuera.append(f"  [ ] {len(a['sin_marca'])} carpeta(s) que no llegaran al "
                     f"clon (sin .gitkeep)")
    if fuera:
        lineas += ["=" * 66, f"AVISOS (umbral de pesado: {_tam(max_bytes)})", ""] + fuera + [""]

    LISTADO.write_text('\n'.join(lineas), encoding='utf-8')


def _informe(a, max_bytes):
    peso = sum(t for _, t in a['viajan'])
    print(f"\n{'='*66}")
    print("  QUE VIAJA EN EL PUSH")
    print(f"{'='*66}")
    print(f"  {len(a['viajan'])} fichero(s), {_tam(peso)} en total")
    print(f"  Umbral de 'pesado': {_tam(max_bytes)}")

    if a['pesados_dentro']:
        print(f"\n[!] PESADOS YA VERSIONADOS ({len(a['pesados_dentro'])})")
        print("    Estan en el repo: añadirlos al .gitignore NO los saca.")
        for rel, tam in a['pesados_dentro']:
            print(f"      {_tam(tam):>9}  {rel}")
        print(f"\n      git rm --cached {a['pesados_dentro'][0][0]}")

    if a['pesados_nuevos']:
        print(f"\n[!] PESADOS QUE SE SUBIRIAN ({len(a['pesados_nuevos'])})")
        print("    Ningun patron los atrapa. Si los subes se quedan en el")
        print("    historial para siempre, aunque los borres despues.")
        for rel, tam in a['pesados_nuevos']:
            print(f"      {_tam(tam):>9}  {rel}")

    if a['ligeros_fuera']:
        print(f"\n[!] LIGEROS QUE NO VIAJAN ({len(a['ligeros_fuera'])})")
        print("    Son config o fuente, pesan poco, y un patron se los lleva")
        print("    por delante. Al clonar en otro equipo no estaran.")
        for rel, tam, regla in a['ligeros_fuera']:
            print(f"      {_tam(tam):>9}  {rel}")
            print(f"                 por: {regla}")

    if a['sin_marca']:
        print(f"\n[ ] CARPETAS QUE NO LLEGARAN AL CLON ({len(a['sin_marca'])})")
        print("    Todo su contenido esta ignorado y no tienen .gitkeep.")
        for carpeta in a['sin_marca']:
            print(f"      {carpeta}/")

    if not (a['pesados_nuevos'] or a['pesados_dentro'] or a['ligeros_fuera']
            or a['sin_marca']):
        print("\n  [OK] Nada que corregir: no hay pesados sueltos, no se pierde")
        print("       nada ligero, y todas las carpetas llegan al clon.")


def _subir(a, mensaje, sin_push, forzar):
    """add + commit + push. Devuelve True si termino bien.

    Si el analisis detecto pesados sueltos se PARA aqui: subirlos es lo unico
    de todo esto que no tiene arreglo despues (quedan en el historial aunque
    los borres en el commit siguiente). Con --forzar se sube igualmente.
    """
    print(f"\n{'='*66}")
    print("  COMMIT Y PUSH")
    print(f"{'='*66}")

    if a['pesados_nuevos'] and not forzar:
        print("  [ABORTADO] hay ficheros pesados que se colarian en el repo.")
        print("  Ajusta el .gitignore y vuelve a correrlo, o usa --forzar si")
        print("  de verdad quieres subirlos:")
        for rel, tam in a['pesados_nuevos']:
            print(f"      {_tam(tam):>9}  {rel}")
        return False

    codigo, salida = _git_full(['add', '-A'])
    if codigo != 0:
        print(f"  [ERROR] git add fallo:\n{salida}")
        return False

    # rc 1 = hay algo preparado; rc 0 = el indice esta igual que HEAD
    if _git(['diff', '--cached', '--quiet'])[0] == 0:
        print("  No hay cambios que commitear: el repo ya esta como toca.")
    else:
        _, resumen = _git(['diff', '--cached', '--stat'])
        for linea in resumen.strip().splitlines()[-6:]:
            print(f"    {linea}")
        codigo, salida = _git_full(['commit', '-m', mensaje])
        if codigo != 0:
            print(f"  [ERROR] git commit fallo:\n{salida}")
            return False
        print(f"\n  Commit hecho: {mensaje}")

    if sin_push:
        print("  --sin-push: queda commiteado en local, sin subir.")
        return True

    codigo, salida = _git_full(['push'])
    if codigo != 0:
        print(f"  [ERROR] git push fallo:\n{salida}")
        if 'rejected' in salida or 'behind' in salida:
            print("  El remoto va por delante. Baja primero con: git pull --rebase")
        return False

    _, rama = _git(['rev-parse', '--abbrev-ref', 'HEAD'])
    print(f"  Push a origin/{rama.strip()} completado.")
    return True


def main():
    if not _es_repo():
        print("[ERROR] Esto no es un repositorio git.")
        sys.exit(1)

    argv = sys.argv[1:]
    max_kb = MAX_KB_DEFECTO
    if '--max' in argv:
        try:
            max_kb = int(argv[argv.index('--max') + 1])
        except (IndexError, ValueError):
            print("[ERROR] --max necesita un numero de KB. Ej: --max 512")
            sys.exit(1)

    mensaje = f"Actualiza el proyecto - {datetime.now():%Y-%m-%d %H:%M}"
    if '--m' in argv:
        try:
            mensaje = argv[argv.index('--m') + 1]
        except IndexError:
            print("[ERROR] --m necesita un mensaje entre comillas.")
            sys.exit(1)

    a = _analizar(max_kb * 1024)
    _informe(a, max_kb * 1024)
    _escribir_listado(a, max_kb * 1024)
    print(f"\n  Listado escrito en: {LISTADO.name}")

    if '--solo-listado' in argv:
        print("  --solo-listado: no se toca el repo.\n")
        return

    if not _subir(a, mensaje, '--sin-push' in argv, '--forzar' in argv):
        sys.exit(1)

    print(f"\n{'='*66}")
    print("  [OK] Terminado")
    print(f"{'='*66}\n")


if __name__ == "__main__":
    main()
