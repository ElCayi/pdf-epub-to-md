from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .converter import convert_to_markdown

def parse_args() -> argparse.Namespace:
    """Parsea los argumentos de línea de comandos.

    :returns: Namespace con la ruta de entrada, salida y banderas opcionales.
    """
    parser = argparse.ArgumentParser(
        description="Convierte un archivo PDF o EPUB en un documento Markdown para Obsidian."
    )
    parser.add_argument("input", type=Path, help="Ruta del PDF o EPUB de entrada")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Ruta del .md de salida. Si solo pones un nombre, se guardará junto al libro.",
    )
    parser.add_argument(
        "--keep-page-breaks",
        action="store_true",
        help="Inserta separadores entre páginas del PDF o documentos internos del EPUB.",
    )
    parser.add_argument(
        "--keep-page-numbers",
        action="store_true",
        help="Mantiene números de página del PDF y marcadores de página del EPUB.",
    )
    parser.add_argument(
        "--genre",
        action="append",
        default=[],
        help="Fija un género manualmente. Se puede repetir y evita la consulta web.",
    )
    parser.add_argument(
        "--no-genre-lookup",
        action="store_true",
        help="No consulta servicios bibliográficos para detectar el género.",
    )
    return parser.parse_args()

def default_output_path(input_path: Path) -> Path:
    """Calcula la ruta de salida por defecto junto al libro original.

    :param input_path: Ruta del archivo PDF o EPUB original.
    :returns: Ruta del Markdown resultante en la misma carpeta.
    """
    return input_path.with_suffix(".md")

def resolve_output_path(input_path: Path, requested_output: Path | None) -> Path:
    """Resuelve la ruta final de salida, junto al libro si no hay carpeta.

    :param input_path: Ruta del PDF o EPUB original.
    :param requested_output: Ruta pedida por consola o ``None``.
    :returns: Ruta final del Markdown.
    """
    if requested_output is None:
        return default_output_path(input_path)
    if requested_output.is_absolute() or requested_output.parent != Path("."):
        return requested_output
    return input_path.parent / requested_output

def main() -> int:
    """Punto de entrada del script.

    :returns: Código de salida del proceso.
    """
    args = parse_args()
    input_path: Path = args.input
    output_path: Path = resolve_output_path(input_path, args.output)

    try:
        convert_to_markdown(
            input_path=input_path,
            output_path=output_path,
            keep_page_breaks=args.keep_page_breaks,
            keep_page_numbers=args.keep_page_numbers,
            genre_lookup=not args.no_genre_lookup,
            manual_genres=args.genre,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Escrito en {output_path}")
    return 0
