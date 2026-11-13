from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

from .common import *
from .metadata import is_footer_line, is_repeated_footer

def estimate_body_left(lines: list[LineItem], body_size: float) -> float:
    """Estima el margen izquierdo habitual del cuerpo de texto."""
    candidates = [
        line.x0
        for line in lines
        if not is_page_number(line)
        and not is_footer_line(line)
        and (body_size <= 0 or line.size <= body_size * 1.2)
    ]
    if not candidates:
        return 0.0
    candidates.sort()
    return candidates[min(len(candidates) - 1, len(candidates) // 4)]

def estimate_style_left(
    lines: list[LineItem],
    body_size: float,
    *,
    italic: bool,
    fallback: float,
) -> float:
    """Estima el margen de continuación propio de texto normal o cursivo."""
    candidates = [
        line.x0
        for line in lines
        if line.italic is italic
        and not is_page_number(line)
        and not is_footer_line(line)
        and (body_size <= 0 or line.size <= body_size * 1.2)
    ]
    return min(candidates) if candidates else fallback

def estimate_typical_line_gap(lines: list[LineItem]) -> float:
    """Calcula el hueco vertical habitual entre líneas consecutivas."""
    gaps = [
        current.y0 - previous.y1
        for previous, current in zip(lines, lines[1:])
        if 0 <= current.y0 - previous.y1 <= max(previous.size, current.size, 1.0)
    ]
    return statistics.median(gaps) if gaps else 0.0

def starts_new_text_block(previous: LineItem, current: LineItem) -> bool:
    """Indica si PyMuPDF sitúa la línea en un bloque de texto diferente."""
    return current.block_id != previous.block_id

def starts_indented_paragraph(
    current: LineItem,
    body_left: float,
    body_size: float,
) -> bool:
    """Detecta la sangría de primera línea usada para iniciar un párrafo.

    Muchos PDF agrupan varios párrafos en un mismo bloque. En novelas, la
    sangría respecto al margen del cuerpo conserva el límite real, incluidos
    los turnos de diálogo consecutivos.
    """
    indent = current.x0 - body_left
    return indent >= max(8.0, body_size * 0.8)

def has_scene_break_before(
    previous: LineItem,
    current: LineItem,
    typical_gap: float,
    body_size: float,
) -> bool:
    """Detecta un espacio vertical deliberado entre dos párrafos de novela."""
    gap = current.y0 - previous.y1
    threshold = max(typical_gap * 2.5, body_size * 0.8, 4.0)
    return gap >= threshold

def is_page_number(line: LineItem) -> bool:
    """Comprueba si una línea parece ser solo un número de página.

    :param line: Línea extraída del PDF.
    :returns: ``True`` si la línea es un número puro.
    """
    return bool(PAGE_NUMBER_RE.fullmatch(line.text))

def heading_level(line: LineItem, body_size: float) -> int | None:
    """Intenta inferir el nivel de encabezado de una línea.

    :param line: Línea a evaluar.
    :param body_size: Tamaño de fuente típico del cuerpo del texto.
    :returns: Nivel de encabezado de Markdown o ``None`` si no parece título.
    """
    if body_size <= 0 or line.size <= 0:
        return None

    if len(line.text.split()) > 14:
        return None

    if line.size >= body_size * HEADING_SIZE_RATIO[0]:
        return 1
    if line.size >= body_size * HEADING_SIZE_RATIO[1]:
        return 2
    if line.size >= body_size * HEADING_SIZE_RATIO[2] and line.bold:
        return 3
    return None

def is_centered_subheading(line: LineItem, body_left: float, body_size: float) -> bool:
    """Detecta subtítulos centrados y en negrita aunque usen tamaño de cuerpo."""
    if not line.bold or len(line.text.split()) > 12:
        return False
    return line.x0 - body_left >= max(20.0, body_size * 3.0)

def extract_part_page_headings(lines: list[LineItem]) -> dict[int, tuple[int, str]]:
    """Reconoce una portada interna de parte y devuelve sus bloques Markdown.

    Estas páginas suelen contener solo ``PRIMERA PARTE`` (o equivalente), un
    subtítulo en negrita y el pie de página. Al no haber texto de cuerpo, el
    tamaño relativo de fuente no sirve para detectar los encabezados.
    """
    content = [
        line
        for line in lines
        if not is_page_number(line) and not is_footer_line(line)
    ]
    if not 2 <= len(content) <= 4 or not PART_HEADING_RE.fullmatch(content[0].text):
        return {}
    if not all(line.bold for line in content):
        return {}

    grouped: dict[int, list[str]] = {}
    block_order: list[int] = []
    for line in content:
        if line.block_id not in grouped:
            grouped[line.block_id] = []
            block_order.append(line.block_id)
        grouped[line.block_id].append(line.text)
    if len(block_order) < 2:
        return {}

    headings: dict[int, tuple[int, str]] = {}
    for index, block_id in enumerate(block_order):
        headings[block_id] = (2 if index == 0 else 3, normalize_text(" ".join(grouped[block_id])))
    return headings

def is_chapter_heading(text: str) -> bool:
    """Comprueba si una línea parece un encabezado de capítulo.

    :param text: Texto de la línea.
    :returns: ``True`` si parece el inicio real de una sección/capítulo.
    """
    cleaned = normalize_text(text)
    if not cleaned:
        return False
    return bool(CHAPTER_HEADING_RE.fullmatch(cleaned))

def append_to_paragraph(paragraph: list[str], text: str) -> None:
    """Añade texto al párrafo actual y recompone palabras cortadas.

    :param paragraph: Acumulador de líneas del párrafo actual.
    :param text: Texto nuevo a añadir.
    """
    if not paragraph:
        paragraph.append(text)
        return

    previous = paragraph[-1]
    if previous.endswith("-*") and text.startswith("*") and len(text) > 1 and text[1].islower():
        paragraph[-1] = previous[:-2] + text[1:]
    elif previous.endswith("-") and text and text[0].islower():
        paragraph[-1] = previous[:-1] + text
    else:
        paragraph.append(text)

def flush_paragraph(paragraph: list[str], output: list[str]) -> None:
    """Vacía el párrafo actual y lo añade a la salida si contiene texto.

    :param paragraph: Acumulador del párrafo actual.
    :param output: Lista final de bloques Markdown.
    """
    if not paragraph:
        return
    merged = normalize_text(" ".join(paragraph)).replace("* *", " ")
    if merged:
        output.append(merged)
    paragraph.clear()

def is_structural_markdown_block(text: str) -> bool:
    """Indica si un bloque Markdown no debe unirse con un párrafo vecino.

    :param text: Bloque Markdown normalizado.
    :returns: ``True`` para títulos, listas, imágenes o separadores.
    """
    stripped = text.lstrip()
    return (
        stripped.startswith("#")
        or stripped.startswith("![[")
        or stripped == SCENE_BREAK_MARKER
        or stripped == "---"
        or bool(LIST_ITEM_RE.match(stripped))
    )

def merge_page_boundary(previous_page: str, current_page: str) -> str:
    """Une un párrafo que continúa a través de un salto de página físico.

    :param previous_page: Markdown generado para la página anterior.
    :param current_page: Markdown generado para la página actual.
    :returns: Las dos páginas unidas, conservando los límites semánticos.
    """
    previous_blocks = previous_page.split("\n\n")
    current_blocks = current_page.split("\n\n")
    if not previous_blocks or not current_blocks:
        return "\n\n".join(previous_blocks + current_blocks)

    previous_text = previous_blocks[-1].strip()
    current_text = current_blocks[0].strip()
    if (
        not previous_text
        or not current_text
        or is_structural_markdown_block(previous_text)
        or is_structural_markdown_block(current_text)
        or SENTENCE_END_RE.search(previous_text)
    ):
        return "\n\n".join(previous_blocks + current_blocks)

    joined: list[str] = previous_blocks[:-1]
    if previous_text.endswith("-") and current_text[0].islower():
        joined.append(previous_text[:-1] + current_text)
    else:
        joined.append(f"{previous_text} {current_text}")
    joined.extend(current_blocks[1:])
    return "\n\n".join(joined)

def safe_image_extension(ext: str | None) -> str:
    """Normaliza la extensión de una imagen extraída.

    :param ext: Extensión original devuelta por PyMuPDF.
    :returns: Extensión segura para nombre de archivo.
    """
    if not ext:
        return "png"
    cleaned = str(ext).lower().lstrip(".")
    return cleaned or "png"

def is_decorative_preliminary_image(image: dict[str, Any]) -> bool:
    """Detecta banners decorativos pequeños en las páginas preliminares.

    La regla es deliberadamente conservadora y no se aplica a imágenes del cuerpo.

    :param image: Datos de la imagen devueltos por PyMuPDF.
    :returns: ``True`` para una imagen baja con formato de banner.
    """
    width = int(image.get("width") or 0)
    height = int(image.get("height") or 0)
    return height > 0 and height <= 200 and width / height >= 3

def extract_page_images(
    pdf: PdfDocument,
    page: PdfPage,
    page_number: int,
    assets_dir: Path,
    image_prefix: str,
    saved_images: dict[int, str],
    body_started: bool,
) -> list[str]:
    """Extrae las imágenes de una página y devuelve sus enlaces de Obsidian.

    :param pdf: Documento PDF abierto.
    :param page: Página actual.
    :param page_number: Número de página a procesar.
    :param assets_dir: Carpeta donde guardar las imágenes.
    :param saved_images: Caché de imágenes ya extraídas por xref.
    :returns: Lista de enlaces ``![[...]]``.
    """
    image_blocks: list[str] = []

    for image_index, image_info in enumerate(page.get_images(full=True), start=1):
        xref = int(image_info[0])
        filename = saved_images.get(xref)
        if filename is None:
            try:
                image = pdf.extract_image(xref)
            except Exception:
                continue

            image_bytes = image.get("image")
            if not image_bytes:
                continue
            if not body_started and is_decorative_preliminary_image(image):
                continue

            ext = safe_image_extension(image.get("ext"))
            filename = f"{image_prefix}-xref-{xref}.{ext}"
            image_path = assets_dir / filename
            image_path.write_bytes(image_bytes)
            saved_images[xref] = filename

        relative_link = f"{assets_dir.name}/{filename}"
        image_blocks.append(f"![[{relative_link}]]")

    return image_blocks

def is_cover_page(page_number: int, image_blocks: list[str], text_blocks: list[str]) -> bool:
    """Decide si una página parece ser una portada.

    :param page_number: Número de página.
    :param image_blocks: Imágenes ya detectadas en la página.
    :param text_blocks: Bloques de texto detectados en la página.
    :returns: ``True`` si la página parece portada.
    """
    return page_number == 1 and bool(image_blocks) and len(text_blocks) <= 2

def convert_page(
    pdf: PdfDocument,
    page: PdfPage,
    page_number: int,
    body_started: bool,
    keep_page_numbers: bool,
    assets_dir: Path,
    image_prefix: str,
    saved_images: dict[int, str],
) -> tuple[str, bool, bool]:
    """Convierte una página individual a Markdown.

    :param pdf: Documento PDF abierto.
    :param page: Página actual.
    :param page_number: Número de página.
    :param body_started: Indica si el cuerpo principal ya ha empezado en páginas anteriores.
    :param keep_page_numbers: Indica si hay que conservar números de página.
    :param assets_dir: Carpeta de imágenes del resultado.
    :param saved_images: Caché de imágenes ya extraídas.
    :returns: Tupla con el bloque Markdown de la página, un indicador de si ya empezó el cuerpo
        y una bandera que marca si la página pertenece a preliminares.
    """
    lines, body_size = extract_lines(page)
    body_left = estimate_body_left(lines, body_size)
    normal_left = estimate_style_left(
        lines, body_size, italic=False, fallback=body_left
    )
    italic_left = estimate_style_left(
        lines, body_size, italic=True, fallback=body_left
    )
    typical_gap = estimate_typical_line_gap(lines)
    part_headings = extract_part_page_headings(lines)
    emitted_part_blocks: set[int] = set()
    output: list[str] = []
    paragraph: list[str] = []
    previous_line: LineItem | None = None
    distribution_page = not body_started and any(
        DISTRIBUTION_CREDIT_RE.search(
            strip_footer_noise(line.text, None, len(pdf))
        )
        for line in lines
    )
    for line in lines:
        text = strip_footer_noise(line.text, None, len(pdf))
        if not text:
            continue

        chapter_heading = is_chapter_heading(text)
        if is_page_number(line) and not keep_page_numbers and not chapter_heading:
            continue
        if distribution_page:
            continue

        if line.block_id in part_headings:
            body_started = True
            if line.block_id not in emitted_part_blocks:
                flush_paragraph(paragraph, output)
                level, heading_text = part_headings[line.block_id]
                output.append(f"{'#' * level} {heading_text}")
                emitted_part_blocks.add(line.block_id)
            previous_line = line
            continue

        if not body_started and chapter_heading:
            body_started = True

        if body_started:
            if chapter_heading:
                flush_paragraph(paragraph, output)
                output.append(f"### {text}")
                previous_line = line
                continue

            level = heading_level(line, body_size)
            if level is not None:
                flush_paragraph(paragraph, output)
                output.append(f"{'#' * level} {text}")
                previous_line = line
                continue

            if is_centered_subheading(line, body_left, body_size):
                flush_paragraph(paragraph, output)
                output.append(f"#### {text}")
                previous_line = line
                continue

            if LIST_ITEM_RE.match(text):
                flush_paragraph(paragraph, output)
                output.append(text)
                previous_line = line
                continue

            if paragraph and previous_line is not None:
                if has_scene_break_before(previous_line, line, typical_gap, body_size):
                    flush_paragraph(paragraph, output)
                    if not output or output[-1] != SCENE_BREAK_MARKER:
                        output.append(SCENE_BREAK_MARKER)
                elif starts_new_text_block(previous_line, line) or starts_indented_paragraph(
                    line,
                    italic_left if line.italic else normal_left,
                    body_size,
                ):
                    flush_paragraph(paragraph, output)

        elif previous_line is not None and SENTENCE_END_RE.search(previous_line.text):
            flush_paragraph(paragraph, output)

        formatted_text = f"*{text}*" if line.italic else text
        append_to_paragraph(paragraph, formatted_text)
        previous_line = line

    flush_paragraph(paragraph, output)

    image_blocks = extract_page_images(
        pdf,
        page,
        page_number,
        assets_dir,
        image_prefix,
        saved_images,
        body_started,
    )
    if image_blocks:
        if is_cover_page(page_number, image_blocks, output):
            output = image_blocks + ([""] + output if output else [])
        else:
            if output:
                output.append("")
            output.extend(image_blocks)

    if not output:
        return ("", body_started, not body_started)

    return ("\n\n".join(output), body_started, not body_started)

def pop_first_image(block_groups: list[list[str]]) -> str:
    """Extrae la primera imagen de varios grupos Markdown.

    :param block_groups: Grupos de bloques que se modificarán in situ.
    :returns: Primer enlace de imagen encontrado o una cadena vacía.
    """
    for group in block_groups:
        for index, block in enumerate(group):
            parts = block.split("\n\n")
            for part_index, part in enumerate(parts):
                candidate = part.strip()
                if not OBSIDIAN_IMAGE_RE.fullmatch(candidate):
                    continue
                del parts[part_index]
                cleaned = [part for part in parts if part.strip()]
                if cleaned:
                    group[index] = "\n\n".join(cleaned)
                else:
                    del group[index]
                return candidate
    return ""

def obsidian_image_path(image_block: str) -> str:
    """Obtiene la ruta de un enlace de imagen de Obsidian.

    :param image_block: Enlace con formato ``![[ruta]]``.
    :returns: Ruta de la imagen o una cadena vacía si el formato no es válido.
    """
    candidate = image_block.strip()
    if not OBSIDIAN_IMAGE_RE.fullmatch(candidate):
        return ""
    return candidate[3:-2]
