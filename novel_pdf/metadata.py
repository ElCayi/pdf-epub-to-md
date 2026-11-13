from __future__ import annotations

import re
import statistics
from pathlib import Path

from .common import *
from .genres import *

def looks_generic_title(title: str) -> bool:
    """Detecta nombres de archivo poco fiables para usarlos como título.

    :param title: Título candidato.
    :returns: ``True`` si parece un nombre automático o poco descriptivo.
    """
    cleaned = normalize_text(title)
    if not cleaned:
        return True
    if any(char.isdigit() for char in cleaned):
        return True
    if len(cleaned.split()) <= 1 and len(cleaned) > 18:
        return True
    generic_words = {"scan", "scanned", "documento", "archivo", "pdf", "ebook", "book"}
    return cleaned.lower() in generic_words

def infer_author_from_candidates(candidates: list[str], title: str) -> str:
    """Intenta deducir el autor a partir de líneas tempranas del PDF.

    :param candidates: Lista de líneas candidatas extraídas del documento.
    :param title: Título ya inferido para evitar confusiones.
    :returns: Nombre del autor o cadena vacía si no se puede inferir.
    """
    blocked_words = {
        "trilogía",
        "saga",
        "serie",
        "ediciones",
        "editorial",
        "traducción",
        "traductor",
        "copyright",
        "capítulo",
        "parte",
        "volumen",
        "prólogo",
        "agradecimientos",
    }
    normalized_title = normalize_text(title).lower()

    for text in candidates:
        cleaned = normalize_text(text)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if any(word in lowered for word in blocked_words):
            continue
        if any(char.isdigit() for char in cleaned):
            continue

        candidate = cleaned
        if normalized_title and normalized_title in lowered:
            candidate = normalize_text(re.sub(re.escape(title), " ", cleaned, flags=re.I))
            lowered = candidate.lower()

        if candidate.lower() == normalized_title:
            continue

        words = candidate.split()
        if not (1 <= len(words) <= 4):
            continue
        if all(word[:1].isupper() or word.isupper() for word in words):
            return candidate

    return ""

def extract_text_candidates(
    pdf: PdfDocument, max_pages: int = 4, footer_counts: dict[str, int] | None = None
) -> list[str]:
    """Recoge líneas útiles del inicio del PDF para inferir metadatos.

    :param pdf: Documento PDF abierto.
    :param max_pages: Número máximo de páginas iniciales a inspeccionar.
    :returns: Lista de líneas candidatas a contener metadatos.
    """
    candidates: list[str] = []
    for page_index in range(min(len(pdf), max_pages)):
        lines, _ = extract_lines(pdf[page_index])
        for line in lines:
            text = strip_footer_noise(line.text, footer_counts, len(pdf))
            if text and not is_footer_line(line):
                if footer_counts and is_repeated_footer(text, footer_counts, len(pdf)):
                    continue
                candidates.append(text)
    return candidates

def extract_page_candidates(
    pdf: PdfDocument, page_index: int, footer_counts: dict[str, int]
) -> list[str]:
    """Recoge las líneas de texto útiles de una página concreta.

    :param pdf: Documento PDF abierto.
    :param page_index: Índice de la página a inspeccionar.
    :returns: Lista de líneas normalizadas no vacías.
    """
    if page_index < 0 or page_index >= len(pdf):
        return []

    page_candidates: list[str] = []
    lines, _ = extract_lines(pdf[page_index])
    for line in lines:
        text = strip_footer_noise(line.text, footer_counts, len(pdf))
        if text and not is_footer_line(line) and not is_repeated_footer(text, footer_counts, len(pdf)):
            page_candidates.append(text)
    return page_candidates

def is_footer_line(line: LineItem) -> bool:
    """Determina si una línea está en la zona inferior de la página.

    :param line: Línea a evaluar.
    :returns: ``True`` si la línea cae en el área habitual de pie de página.
    """
    if line.page_height <= 0:
        return False
    return line.y0 >= line.page_height * 0.9

def build_repeated_footer_index(pdf: PdfDocument) -> dict[str, int]:
    """Cuenta textos que aparecen repetidos en la zona inferior de varias páginas.

    :param pdf: Documento PDF abierto.
    :returns: Diccionario con el texto y el número de páginas en las que aparece.
    """
    counts: dict[str, int] = {}
    for page_index in range(len(pdf)):
        lines, _ = extract_lines(pdf[page_index])
        seen_on_page: set[str] = set()
        for line in lines:
            text = normalize_text(line.text)
            if not text or len(text) > 80:
                continue
            if not is_footer_line(line):
                continue
            seen_on_page.add(text)
        for text in seen_on_page:
            counts[text] = counts.get(text, 0) + 1
    return counts

def is_repeated_footer(text: str, footer_counts: dict[str, int], page_count: int) -> bool:
    """Comprueba si un texto aparece como pie de página repetido.

    :param text: Texto a evaluar.
    :param footer_counts: Índice de textos repetidos en la zona inferior.
    :param page_count: Número total de páginas del PDF.
    :returns: ``True`` si el texto parece un footer repetido.
    """
    if page_count <= 0:
        return False
    normalized = normalize_text(text)
    count = footer_counts.get(normalized, 0)
    return count >= max(3, int(page_count * 0.4))

def extract_page_paragraph_candidates(
    pdf: PdfDocument, page_index: int, footer_counts: dict[str, int]
) -> list[str]:
    """Recoge bloques de texto como párrafos desde una página concreta.

    :param pdf: Documento PDF abierto.
    :param page_index: Índice de la página a inspeccionar.
    :returns: Lista de párrafos normalizados no vacíos.
    """
    if page_index < 0 or page_index >= len(pdf):
        return []

    data = pdf[page_index].get_text("dict")
    paragraphs: list[str] = []

    for block_id, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue

        block_lines: list[str] = []
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = strip_footer_noise(
                "".join(span.get("text", "") for span in spans),
                footer_counts,
                len(pdf),
            )
            bbox = line.get("bbox", [0.0, 0.0, 0.0, 0.0])
            y0 = float(bbox[1]) if len(bbox) > 1 else 0.0
            line_item = LineItem(
                text=text,
                size=0.0,
                bold=False,
                italic=False,
                block_id=block_id,
                x0=float(bbox[0]) if bbox else 0.0,
                y0=y0,
                y1=float(bbox[3]) if len(bbox) > 3 else y0,
                page_height=float(pdf[page_index].rect.height),
            )
            if text and not is_footer_line(line_item) and not is_repeated_footer(text, footer_counts, len(pdf)):
                block_lines.append(text)

        paragraph = normalize_text(" ".join(block_lines))
        if paragraph:
            paragraphs.append(paragraph)

    return paragraphs

def extract_pdf_title(pdf: PdfDocument) -> str:
    """Intenta leer el título incrustado en los metadatos del PDF.

    :param pdf: Documento PDF abierto.
    :returns: Título limpio o cadena vacía si no existe.
    """
    metadata = getattr(pdf, "metadata", None)
    if not isinstance(metadata, dict):
        return ""

    title = metadata.get("title") or metadata.get("Title")
    if not isinstance(title, str):
        return ""

    cleaned = normalize_text(title)
    if not cleaned:
        return ""

    if cleaned.lower() in {"untitled", "unknown"}:
        return ""

    return cleaned

def extract_largest_line_from_page(pdf: PdfDocument, page_index: int) -> str:
    """Obtiene la línea con mayor tamaño de fuente de una página.

    :param pdf: Documento PDF abierto.
    :param page_index: Índice de la página a inspeccionar.
    :returns: Texto de la línea más grande o cadena vacía.
    """
    if page_index < 0 or page_index >= len(pdf):
        return ""

    lines, _ = extract_lines(pdf[page_index])
    if not lines:
        return ""

    candidates = [line for line in lines if normalize_text(line.text)]
    if not candidates:
        return ""

    largest_size = max(line.size for line in candidates)
    largest_lines = [line for line in candidates if line.size == largest_size]
    best_line = min(largest_lines, key=lambda line: len(normalize_text(line.text)))
    return normalize_text(best_line.text)

def infer_series_from_line(text: str) -> tuple[str, int | None, str | None]:
    """Intenta deducir serie, índice y tipo de pertenencia a saga desde una línea.

    :param text: Línea de texto candidata.
    :returns: Tupla con serie, índice y etiqueta de relación si se reconoce algo.
    """
    trilogy_match = TRILOGY_RE.search(text)
    if trilogy_match:
        return (
            normalize_text(trilogy_match.group("series")),
            int(trilogy_match.group("index")),
            "trilogia",
        )

    saga_match = SAGA_RE.search(text)
    if saga_match:
        return (
            normalize_text(saga_match.group("series")),
            int(saga_match.group("index")),
            "saga",
        )

    series_match = SERIES_RE.search(text)
    if series_match:
        return (
            normalize_text(series_match.group("series")),
            int(series_match.group("index")),
            "serie",
        )

    sequel_match = SEQUEL_RE.search(text)
    if sequel_match:
        series_name = sequel_match.group("series") or ""
        return (normalize_text(series_name), None, "secuela")

    volume_match = VOLUME_RE.search(text)
    if volume_match:
        return ("", int(volume_match.group("index")), "volumen")

    return ("", None, None)

def infer_saga_label(text: str) -> str:
    """Devuelve el texto de saga si la línea contiene una pista clara.

    :param text: Línea candidata.
    :returns: Texto original cuando parece referirse a saga, trilogía o serie.
    """
    if len(text.split()) > 14 or SENTENCE_END_RE.search(text):
        return ""
    lower = text.lower()
    if re.match(r"^(?:trilog[ií]a|saga|serie)\b", lower):
        return text
    return ""

def prettify_uppercase_text(text: str) -> str:
    """Convierte texto en una versión más legible para mostrar en Obsidian.

    :param text: Texto original.
    :returns: Texto con capitalización de título.
    """
    return normalize_text(text).title()

def derive_saga_slug(text: str) -> str:
    """Obtiene un slug de saga sin índice a partir del texto de saga.

    :param text: Texto de saga, trilogía o serie.
    :returns: Slug limpio para usar en tags de Obsidian.
    """
    normalized = normalize_text(text).lower()
    normalized = re.sub(r"^(trilog[ií]a|saga|serie)\s+", "", normalized)
    normalized = re.sub(r"\s+\d+\s*$", "", normalized)
    return slugify(normalized)

def extract_metadata(
    pdf: PdfDocument,
    input_path: Path,
    genre_lookup: bool = True,
    manual_genres: list[str] | None = None,
) -> BookMetadata:
    """Obtiene metadatos básicos del libro.

    :param pdf: Documento PDF abierto.
    :param input_path: Ruta del archivo original.
    :param genre_lookup: Indica si se consultan fuentes bibliográficas por ISBN.
    :param manual_genres: Géneros indicados por la persona usuaria.
    :returns: Metadatos inferidos para el frontmatter de Obsidian.
    """
    _, author = split_filename_metadata(input_path)

    title = extract_pdf_title(pdf)
    if not title:
        title = extract_largest_line_from_page(pdf, 0)

    metadata = BookMetadata(title=title, author=author)
    footer_counts = build_repeated_footer_index(pdf)
    first_page_paragraphs = extract_page_paragraph_candidates(pdf, 0, footer_counts)
    second_page_paragraphs = extract_page_paragraph_candidates(pdf, 1, footer_counts)
    first_page_candidates = extract_page_candidates(pdf, 0, footer_counts)
    second_page_candidates = extract_page_candidates(pdf, 1, footer_counts)
    other_candidates = extract_text_candidates(pdf, max_pages=4, footer_counts=footer_counts)
    all_candidates = second_page_paragraphs + first_page_paragraphs + second_page_candidates + first_page_candidates + other_candidates
    metadata.isbn = extract_isbn(all_candidates)
    for text in all_candidates:
        original_title_match = ORIGINAL_TITLE_RE.search(text)
        if original_title_match:
            metadata.original_title = normalize_text(original_title_match.group("title"))
            break
    author_candidates = second_page_paragraphs + first_page_paragraphs + second_page_candidates + first_page_candidates + other_candidates
    if not metadata.author:
        metadata.author = infer_author_from_candidates(author_candidates, metadata.title)

    # Priorizamos la segunda página porque ahí suelen venir los datos editoriales.
    for text in second_page_paragraphs + first_page_paragraphs + second_page_candidates + first_page_candidates + other_candidates:
        lower = text.lower()

        if not metadata.saga:
            saga_label = infer_saga_label(text)
            if saga_label:
                metadata.saga = prettify_uppercase_text(saga_label)

            series_name, series_index, series_kind = infer_series_from_line(text)
            if series_kind in {"trilogia", "saga", "serie"} and series_name:
                kind_label = {
                    "trilogia": "Trilogía",
                    "saga": "Saga",
                    "serie": "Serie",
                }[series_kind]
                metadata.saga = f"{kind_label} {prettify_uppercase_text(series_name)}"
                if series_index is not None:
                    metadata.saga += f" - {series_index}"
                continue

            if series_kind == "secuela" and series_name:
                metadata.saga = prettify_uppercase_text(text)
                continue

        if not metadata.translator:
            translator_match = TRANSLATOR_RE.search(text)
            if translator_match:
                translator = re.split(
                    r"\b(?:Ediciones|Editorial|Colección|Volumen|©|Título original|Primera edición)\b",
                    translator_match.group("name"),
                    maxsplit=1,
                )[0]
                metadata.translator = normalize_text(translator)
                continue

        if not metadata.publisher:
            publisher_match = PUBLISHER_RE.search(text)
            if publisher_match:
                publisher = normalize_text(publisher_match.group(1))
                metadata.publisher = re.sub(
                    r"\b(Colección|Volumen|Página)\b.*$",
                    "",
                    publisher,
                    flags=re.I,
                ).strip(" ,.-")
                continue

        if not metadata.title and len(text.split()) <= 8 and len(text) >= 3:
            if not any(word in lower for word in ("ediciones", "editorial", "traducción", "copyright")):
                metadata.title = text

    if metadata.saga:
        saga_slug = derive_saga_slug(metadata.saga)
        if saga_slug:
            metadata.tags.append(f"saga/{saga_slug}")
        if metadata.title:
            metadata.tags.append(f"novela/{slugify(metadata.title)}")

    if metadata.saga and "secuela" in metadata.saga.lower():
        metadata.tags.append("secuela")

    if metadata.author:
        metadata.tags.append(f"autor/{slugify(metadata.author)}")

    if manual_genres:
        metadata.genres = normalize_manual_genres(manual_genres)
    elif genre_lookup:
        metadata.genres = lookup_genres(
            metadata.isbn,
            metadata.title,
            metadata.author,
            metadata.original_title,
        )

    for genre in metadata.genres:
        metadata.tags.append(f"genero/{genre}")

    metadata.tags.append("novela")
    return metadata
