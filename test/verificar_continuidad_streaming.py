#!/usr/bin/env python3
"""Verifica continuidad de velas sin cargar nada en memoria.
Streaming puro: procesa línea por línea, escribe gaps directamente a archivo.
"""

import sys
from pathlib import Path
from datetime import datetime

def verificar_continuidad(archivo_csv, archivo_reporte=None, tf_ms=60_000):
    """
    Verifica continuidad de velas. Streaming: no carga nada en memoria.

    Args:
        archivo_csv: ruta al CSV de velas
        archivo_reporte: ruta del reporte (default: <coin>_gaps.txt)
        tf_ms: intervalo en ms (default: 60000 = 1m)
    """
    archivo_csv = Path(archivo_csv)
    if not archivo_csv.exists():
        print(f"ERROR: {archivo_csv} no existe")
        sys.exit(1)

    if archivo_reporte is None:
        coin = archivo_csv.stem.split('_')[1]  # historico_ETH_1m -> ETH
        archivo_reporte = archivo_csv.parent / f"{coin}_gaps.txt"

    archivo_reporte = Path(archivo_reporte)

    print(f"Archivo: {archivo_csv.name}")
    print(f"Reporte: {archivo_reporte.name}")
    print(f"TF esperado: {tf_ms}ms ({tf_ms//1000//60 if tf_ms >= 60000 else tf_ms//1000}m)")
    print()

    total_velas = 0
    total_gaps = 0
    total_faltantes = 0
    ts_prev = None
    fecha_prev = None
    linea_num = 0

    # Abrir CSV para leer, reporte para escribir
    with open(archivo_csv, 'r', encoding='utf-8') as f_in, \
         open(archivo_reporte, 'w', encoding='utf-8') as f_out:

        cabecera = f_in.readline()
        f_out.write("SALTOS DETECTADOS EN VELAS\n")
        f_out.write("="*70 + "\n\n")

        for linea_num, linea in enumerate(f_in, start=2):
            linea = linea.strip()
            if not linea:
                continue

            partes = linea.split(',')
            ts = int(partes[0])
            fecha = partes[1]

            if ts_prev is not None:
                diff = ts - ts_prev
                if diff != tf_ms:
                    velas_faltantes = (diff // tf_ms) - 1
                    total_gaps += 1
                    total_faltantes += velas_faltantes

                    # Escribir gap directamente (streaming, sin acumular)
                    f_out.write(f"Linea {linea_num - 1:,}\n")
                    f_out.write(f"  Faltantes: {int(velas_faltantes)} vela(s)\n")
                    f_out.write(f"  {fecha_prev} -> {fecha}\n")
                    f_out.write(f"  Diferencia: {diff // 1000 // 60} min ({diff}ms)\n\n")

            ts_prev = ts
            fecha_prev = fecha
            total_velas += 1

            # Progreso cada 500k velas
            if total_velas % 500_000 == 0:
                print(f"Procesadas: {total_velas:,} velas...")

    # Resumen al inicio del reporte
    resumen = (
        f"RESUMEN\n"
        f"{'='*70}\n"
        f"Total velas: {total_velas:,}\n"
        f"Saltos detectados: {total_gaps}\n"
        f"Velas faltantes: {total_faltantes}\n"
        f"Porcentaje pérdida: {100*total_faltantes/total_velas:.4f}%\n"
        f"{'='*70}\n\n"
    )

    # Prepend resumen al archivo
    contenido = resumen + open(archivo_reporte).read()
    with open(archivo_reporte, 'w', encoding='utf-8') as f:
        f.write(contenido)

    # Resumen en pantalla
    print(f"\n{'='*70}")
    print(resumen.strip())
    print(f"{'='*70}")
    print(f"\nDetalle guardado en: {archivo_reporte}")

    return {
        'total_velas': total_velas,
        'total_gaps': total_gaps,
        'total_faltantes': total_faltantes,
        'porcentaje_perdida': 100*total_faltantes/total_velas
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python verificar_continuidad_streaming.py <archivo_csv> [reporte_salida]")
        print("\nEjemplos:")
        print("  python verificar_continuidad_streaming.py historico_ETH_1m_bitget.csv")
        print("  python verificar_continuidad_streaming.py historico_BTC_1h_bitget.csv reporte_btc.txt")
        sys.exit(0)

    archivo_csv = sys.argv[1]
    archivo_reporte = sys.argv[2] if len(sys.argv) > 2 else None

    verificar_continuidad(archivo_csv, archivo_reporte)
