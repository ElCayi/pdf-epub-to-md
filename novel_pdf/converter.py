from __future__ import annotations

from pathlib import Path

from .common import BookMetadata, load_pdf, slugify
from .layout import convert_page, merge_page_boundary, obsidian_image_path, pop_first_image
from .metadata import extract_metadata

def convert_to_markdown(
    input_path: Path,
    output_path: Path,
    keep_page_breaks: bool = False,
    keep_page_numbers: bool = False,
    genre_lookup: bool = True,
    manual_genres: list[str] | None = None,
) -> None:
    """Selecciona el conversor según la extensión del libro.

    :param input_path: Ruta del PDF o EPUB original.
    :param output_path: Ruta del Markdown de salida.
    :param keep_page_breaks: Conserva separadores entre páginas o documentos.
    :param keep_page_numbers: Conserva números o marcadores de página.
    :param genre_lookup: Permite consultar géneros en Internet.
    :param manual_genres: Géneros que sustituyen la consulta bibliográfica.
    :raises ValueError: Si la extensión no está admitida.
    """
    if input_path.suffix.lower() == ".pdf":
        converter = convert_pdf_to_markdown
    elif input_path.suffix.lower() == ".epub":
        from .epub import convert_epub_to_markdown

        converter = convert_epub_to_markdown
    else:
        raise ValueError(f"Se esperaba un archivo PDF o EPUB, pero llegó: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("La salida no puede sobrescribir el libro de entrada.")
    converter(input_path, output_path, keep_page_breaks, keep_page_numbers,
              genre_lookup, manual_genres)

def yaml_quote(value: str) -> str:
    """Escapa una cadena para usarla de forma segura en YAML.

    :param value: Valor textual a citar.
    :returns: Cadena lista para escribir en frontmatter.
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

def build_frontmatter(
    metadata: BookMetadata, cover_path: str = "", page_count: int = 0
) -> str:
    """Construye el frontmatter de Obsidian.

    :param metadata: Metadatos del libro.
    :param cover_path: Ruta de la imagen de portada relativa a la nota.
    :param page_count: Número total de páginas del PDF.
    :returns: Bloque YAML listo para colocarse al inicio de la nota.
    """
    lines = ["---"]
    if metadata.title:
        lines.append(f"title: {yaml_quote(metadata.title)}")
    if metadata.saga:
        lines.append(f"saga: {yaml_quote(metadata.saga)}")
    if metadata.author:
        lines.append(f"author: {yaml_quote(metadata.author)}")
    if metadata.translator:
        lines.append(f"translator: {yaml_quote(metadata.translator)}")
    if metadata.publisher:
        lines.append(f"publisher: {yaml_quote(metadata.publisher)}")
    if metadata.genres:
        lines.append("genre:")
        for genre in metadata.genres:
            lines.append(f"  - {genre}")
    lines.append("cssclasses: novela")
    if cover_path:
        lines.append(f"cover: {yaml_quote(cover_path)}")
    if page_count > 0:
        lines.append(f"pages: {page_count}")
    if metadata.tags:
        lines.append("tags:")
        for tag in sorted(dict.fromkeys(metadata.tags)):
            lines.append(f"  - {tag}")
    lines.append("---")
    return "\n".join(lines)

def convert_pdf_to_markdown(
    input_path: Path,
    output_path: Path,
    keep_page_breaks: bool,
    keep_page_numbers: bool,
    genre_lookup: bool = True,
    manual_genres: list[str] | None = None,
) -> None:
    """Convierte un PDF completo en Markdown y escribe el resultado.

    :param input_path: Ruta del PDF original.
    :param output_path: Ruta del archivo Markdown de salida.
    :param keep_page_breaks: Indica si se añaden separadores entre páginas.
    :param keep_page_numbers: Indica si se conservan los números de página.
    :param genre_lookup: Indica si se buscan géneros en Internet por ISBN.
    :param manual_genres: Géneros manuales que sustituyen la consulta web.
    """
    pdf = load_pdf(input_path)
    page_count = len(pdf)
    metadata = extract_metadata(pdf, input_path, genre_lookup, manual_genres)
    blocks: list[str] = []
    preliminaries: list[str] = []
    body_blocks: list[str] = []
    assets_dir = output_path.parent / "img"
    image_prefix = slugify(output_path.stem)
    saved_images: dict[int, str] = {}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    try:
        body_started = False
        for index, page in enumerate(pdf, start=1):
            page_md, body_started, page_is_preliminary = convert_page(
                pdf,
                page,
                index,
                body_started,
                keep_page_numbers,
                assets_dir,
                image_prefix,
                saved_images,
            )
            if not page_md:
                continue
            target_blocks = preliminaries if page_is_preliminary and not body_started else body_blocks
            if (
                target_blocks is body_blocks
                and target_blocks
                and not keep_page_breaks
                and target_blocks[-1] != "---"
            ):
                target_blocks[-1] = merge_page_boundary(target_blocks[-1], page_md)
            else:
                target_blocks.append(page_md)
            if keep_page_breaks and index != len(pdf):
                target_blocks.append("---")
    finally:
        pdf.close()

    cover_image = pop_first_image([preliminaries, body_blocks])
    cover_path = obsidian_image_path(cover_image)
    blocks.append(build_frontmatter(metadata, cover_path, page_count))
    if cover_image:
        blocks.append(cover_image)
    if preliminaries:
        blocks.append("## Preliminares")
        blocks.extend(preliminaries)
    blocks.extend(body_blocks)
    output_path.write_text("\n\n".join(blocks).strip() + "\n", encoding="utf-8")
