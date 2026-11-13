from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None

HEADING_SIZE_RATIO = (2.0, 1.6, 1.3)

SCENE_BREAK_MARKER = '<div class="scene-break"></div>'

PAGE_NUMBER_RE = re.compile(r"^\d+$")

LIST_ITEM_RE = re.compile(r"^([-*•]|\d+[.)])\s+")

WHITESPACE_RE = re.compile(r"[ \t]+")

FOOTER_NOISE_RE = re.compile(
    r"(?:\bwww\.[\w.-]+\.[a-z]{2,}\b(?:\s*-\s*Página\s*\d+)?)|(?:\bhttps?://\S+\b)",
    re.IGNORECASE,
)

DISTRIBUTION_CREDIT_RE = re.compile(
    r"(?:\bepub\b|\beditor\s+digital\b|\blectulandia\b)", re.IGNORECASE
)

OBSIDIAN_IMAGE_RE = re.compile(r"^!\[\[[^\n]+\]\]$")

FILENAME_TITLE_AUTHOR_RE = re.compile(r"^(?P<title>.+?)\s*-\s*(?P<author>.+)$")

SENTENCE_END_RE = re.compile(r'[.!?…]["\')\]]*$')

CHAPTER_HEADING_RE = re.compile(
    r"^(?:\d{3}[A-Z]?(?:\s*[:.-]\s*.+)?|\d{1,2}\s*[:.-]\s*.+|cap[ií]tulo\s+\d+\b)",
    re.I,
)

PART_HEADING_RE = re.compile(
    r"^(?:(?:PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|OCTAVA|NOVENA|DÉCIMA)\s+PARTE|PARTE\s+(?:[IVXLCDM]+|\d+))$",
    re.I,
)

TRILOGY_RE = re.compile(
    r"trilog[ií]a\s+(?P<series>[^.!?\n]{1,80}?)\s*[-–—:]?\s*(?P<index>\d+)\b",
    re.I,
)

SAGA_RE = re.compile(
    r"\bsaga\b\s+(?P<series>[^.!?\n]{1,80}?)\s*[-–—:]?\s*(?P<index>\d+)\b",
    re.I,
)

SERIES_RE = re.compile(
    r"\bserie\b\s+(?P<series>[^.!?\n]{1,80}?)\s*[-–—:]?\s*(?P<index>\d+)\b",
    re.I,
)

VOLUME_RE = re.compile(r"\b(volumen|libro|parte)\s+(?P<index>\d+)\b", re.I)

SEQUEL_RE = re.compile(r"\b(secuela de|continuaci[oó]n de|segunda parte de)\b\s*(?P<series>.+)?$", re.I)

TRANSLATOR_RE = re.compile(
    r"traducci[oó]n(?: del inglés)?(?: de)?\s*[:,]?\s*(?P<name>.+)", re.I
)

PUBLISHER_RE = re.compile(r"(Ediciones\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑüÜ.-]*(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑüÜ.-]*)?)")

ORIGINAL_TITLE_RE = re.compile(r"t[ií]tulo original\s*:\s*(?P<title>.+)", re.I)

ISBN_RE = re.compile(
    r"\b(?:ISBN(?:-1[03])?\s*:?[\s-]*)?((?:97[89][\s-]*)?\d[\d\s-]{7,15}[\dXx])\b",
    re.I,
)

GENRE_ALIASES = {
    "science fiction": "ciencia-ficcion",
    "science-fiction": "ciencia-ficcion",
    "ciencia ficcion": "ciencia-ficcion",
    "speculative fiction": "ciencia-ficcion",
    "horror": "terror",
    "terror": "terror",
    "fantasy": "fantasia",
    "fantasia": "fantasia",
    "thriller": "thriller",
    "suspense": "thriller",
    "mystery": "misterio",
    "detective": "misterio",
    "romance": "romance",
    "historical": "historica",
    "literary fiction": "ficcion-literaria",
    "fiction literary": "ficcion-literaria",
    "contemporary": "contemporanea",
    "dystopian": "distopia",
    "dystopia": "distopia",
    "crime": "policiaca",
}

class PdfPage(Protocol):
    """Interfaz mínima de una página de PDF usada por el convertidor."""

    def get_text(self, option: str) -> dict[str, Any]:
        """Devuelve el contenido de la página en formato estructurado."""

    def get_images(self, full: bool = ...) -> list[tuple[Any, ...]]:
        """Devuelve la lista de imágenes embebidas en la página."""

class PdfDocument(Protocol):
    """Interfaz mínima del documento PDF usada por el convertidor."""

    def __len__(self) -> int:
        """Devuelve el número de páginas del documento."""

    def __getitem__(self, index: int) -> PdfPage:
        """Devuelve una página concreta por índice."""

    def __iter__(self) -> Iterator[PdfPage]:
        """Itera sobre las páginas del documento."""

    def close(self) -> None:
        """Cierra el documento PDF."""

    def extract_image(self, xref: int) -> dict[str, Any]:
        """Extrae una imagen embebida a partir de su referencia interna."""

@dataclass
class LineItem:
    """Representa una línea de texto extraída del PDF.

    :ivar text: Texto normalizado de la línea.
    :ivar size: Tamaño de fuente mediano estimado para la línea.
    :ivar bold: Indica si la línea parece estar en negrita.
    :ivar italic: Indica si toda la línea está compuesta en cursiva.
    :ivar block_id: Identificador del bloque de texto original del PDF.
    :ivar x0: Posición horizontal de inicio de la línea dentro de la página.
    :ivar y0: Posición vertical de inicio de la línea dentro de la página.
    :ivar y1: Posición vertical de fin de la línea dentro de la página.
    :ivar page_height: Alto total de la página.
    """

    text: str
    size: float
    bold: bool
    italic: bool
    block_id: int
    x0: float
    y0: float
    y1: float
    page_height: float

@dataclass
class BookMetadata:
    """Metadatos básicos del libro que se escriben en el frontmatter.

    :ivar title: Título principal del libro.
    :ivar original_title: Título original detectado en los créditos.
    :ivar saga: Pista de saga, trilogía o secuela.
    :ivar author: Autor principal.
    :ivar translator: Traductor o traductora.
    :ivar publisher: Editorial.
    :ivar isbn: ISBN-10 o ISBN-13 encontrado en el PDF.
    :ivar genres: Géneros normalizados obtenidos de fuentes bibliográficas.
    :ivar tags: Etiquetas de Obsidian generadas a partir de los metadatos.
    """

    title: str = ""
    original_title: str = ""
    saga: str = ""
    author: str = ""
    translator: str = ""
    publisher: str = ""
    isbn: str = ""
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
def load_pdf(path: Path) -> PdfDocument:
    """Abre y valida un archivo PDF.

    :param path: Ruta del archivo PDF de entrada.
    :returns: Documento PDF abierto.
    :raises RuntimeError: Si PyMuPDF no está instalado.
    :raises FileNotFoundError: Si el archivo no existe.
    :raises ValueError: Si la ruta no apunta a un PDF.
    """
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF no está instalado. Instálalo con: pip install pymupdf"
        )
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de entrada: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Se esperaba un archivo PDF, pero llegó: {path}")
    return fitz.open(path)

def slugify(text: str) -> str:
    """Convierte texto libre en una etiqueta simple para Obsidian.

    :param text: Texto original.
    :returns: Texto normalizado en minúsculas y con separadores seguros.
    """
    normalized = normalize_text(text).lower()
    normalized = normalized.replace("á", "a").replace("é", "e").replace("í", "i")
    normalized = normalized.replace("ó", "o").replace("ú", "u").replace("ü", "u")
    normalized = normalized.replace("ñ", "n")
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")

def split_filename_metadata(input_path: Path) -> tuple[str, str]:
    """Intenta deducir título y autor a partir del nombre del archivo.

    :param input_path: Ruta del PDF original.
    :returns: Tupla con título y autor, ambos opcionales.
    """
    match = FILENAME_TITLE_AUTHOR_RE.match(input_path.stem)
    if not match:
        return normalize_text(input_path.stem), ""
    return normalize_text(match.group("title")), normalize_text(match.group("author"))

def extract_lines(page) -> tuple[list[LineItem], float]:
    """Extrae las líneas de texto y estima el tamaño de cuerpo de una página.

    :param page: Página del PDF.
    :returns: Tupla con las líneas extraídas y el tamaño medio del cuerpo.
    """
    data = page.get_text("dict")
    lines: list[LineItem] = []
    font_sizes: list[float] = []
    page_height = float(page.rect.height)

    for block_id, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue

            sizes = [float(span.get("size", 0.0)) for span in spans if span.get("size")]
            size = statistics.median(sizes) if sizes else 0.0
            flags = [int(span.get("flags", 0)) for span in spans]
            font_names = [str(span.get("font", "")).lower() for span in spans]
            bold = any((flag & 16) != 0 for flag in flags) or any(
                "bold" in name for name in font_names
            )
            italic = bool(flags) and all((flag & 2) != 0 for flag in flags)
            bbox = line.get("bbox", [0.0, 0.0, 0.0, 0.0])
            x0 = float(bbox[0])
            y0 = float(bbox[1])
            y1 = float(bbox[3])
            lines.append(
                LineItem(
                    text=text,
                    size=size,
                    bold=bold,
                    italic=italic,
                    block_id=block_id,
                    x0=x0,
                    y0=y0,
                    y1=y1,
                    page_height=page_height,
                )
            )
            if size:
                font_sizes.append(size)

    body_size = statistics.median(font_sizes) if font_sizes else 0.0
    return lines, body_size

def normalize_text(text: str) -> str:
    """Normaliza espacios y caracteres especiales de una línea.

    :param text: Texto original.
    :returns: Texto limpio y recortado.
    """
    text = text.replace("\u00ad", "")
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()

def strip_footer_noise(text: str, footer_counts: dict[str, int] | None, page_count: int) -> str:
    """Elimina ruido típico de pie de página cuando viene pegado al texto útil.

    :param text: Texto original.
    :param footer_counts: Índice de pies repetidos detectados en el PDF.
    :param page_count: Número total de páginas del documento.
    :returns: Texto sin fragmentos típicos de footer.
    """
    cleaned = normalize_text(text)
    if not cleaned:
        return ""

    cleaned = FOOTER_NOISE_RE.sub(" ", cleaned)

    if footer_counts:
        threshold = max(3, int(page_count * 0.4))
        for footer_text, count in footer_counts.items():
            if count < threshold:
                continue
            if footer_text and footer_text in cleaned:
                cleaned = cleaned.replace(footer_text, " ")

    return normalize_text(cleaned)
