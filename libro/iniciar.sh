#!/bin/bash
# iniciar.sh - Lanza libro.py si no hay ya una instancia corriendo.
#
# Idempotente a proposito: libro.py bloquea su propio CSV de snapshot con
# flock (_aplicar_lock en libro.py) y ese lock lo libera el propio SO en
# cuanto el proceso muere, incluso si muere mal (kill -9, caida del NAS,
# corte de luz). Por eso este script puede relanzarse sin comprobar nada:
# si ya hay un libro.py vivo, la nueva instancia se topa con el lock, loguea
# "Otra instancia en ejecucion" y sale sola sin tocar la que sigue corriendo;
# si no hay ninguna viva, arranca una nueva.
#
# Pensado para una tarea programada de DSM (Panel de control > Programador
# de tareas) que llame a este script cada pocos minutos: cubre a la vez el
# arranque tras un reinicio (corte de luz) y la recuperacion tras un crash,
# sin necesitar dos mecanismos distintos.

cd /volume1/homes/Fran/neo || exit 1
nohup venv/bin/python -u libro/libro.py eth --cada 900 --mercado futuros \
    >> libro/logs/libro_stdout.log 2>&1 &
