"""Conversión de EPUB 2 y 3 a notas de Obsidian sin dependencias adicionales."""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from .common import BookMetadata, slugify, split_filename_metadata
from .converter import build_frontmatter
from .epub_html import (
    BLOCK_TAGS, HtmlNode, MarkdownRenderer, apply_text_styles, clean_markdown,
    epub_types, parse_xhtml, reference_id,
)
from .genres import canonical_genres, extract_isbn, lookup_genres, normalize_manual_genres
from .metadata import derive_saga_slug

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
HTML_MEDIA = {"application/xhtml+xml", "text/html"}
IMAGE_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                    "image/svg+xml": ".svg", "image/webp": ".webp", "image/avif": ".avif"}


def resource_path(base: str, href: str) -> str:
    """Resuelve una referencia dentro del ZIP sin acceder al sistema de archivos.

    :param base: Ruta del documento que contiene la referencia.
    :param href: URI relativa, posiblemente con escapes o fragmentos.
    :returns: Ruta normalizada dentro del EPUB.
    :raises ValueError: Si la referencia sale del contenedor o es remota.
    """
    uri = urlsplit(href)
    if uri.scheme or uri.netloc:
        raise ValueError(f"El EPUB requiere un recurso externo no admitido: {href}")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(uri.path))) if uri.path else base
    if path.startswith("/") or path == ".." or path.startswith("../") or "\\" in path:
        raise ValueError(f"Ruta interna del EPUB no válida: {href}")
    return path


def read_xml(archive: ZipFile, path: str) -> ET.Element:
    """Lee un documento XML del EPUB.

    :param archive: Contenedor abierto.
    :param path: Ruta interna del XML.
    :returns: Elemento raíz.
    """
    return ET.fromstring(archive.read(path))


def extract_epub_metadata(
    package: ET.Element,
    input_path: Path,
    genre_lookup: bool,
    manual_genres: list[str] | None,
) -> BookMetadata:
    """Obtiene propiedades bibliográficas de los metadatos OPF.

    :param package: Documento de paquete EPUB.
    :param input_path: Ruta original para títulos o autores ausentes.
    :param genre_lookup: Permite buscar géneros si no constan en el libro.
    :param manual_genres: Géneros que prevalecen sobre los metadatos.
    :returns: Metadatos compatibles con el frontmatter de los PDF.
    """
    root = package.find("opf:metadata", NS)
    if root is None:
        raise ValueError("El EPUB no contiene metadatos OPF.")

    def values(name: str) -> list[str]:
        """Lee valores Dublin Core normalizando espacios.

        :param name: Nombre local del campo.
        :returns: Valores textuales no vacíos.
        """
        return [text for node in root.findall(f"dc:{name}", NS)
                if (text := " ".join("".join(node.itertext()).split()))]

    metas = root.findall("opf:meta", NS)
    refinements = {(node.get("refines", "").lstrip("#"), node.get("property")): (node.text or "").strip()
                   for node in metas}
    authors, translators = [], []
    for node in root.findall("dc:creator", NS) + root.findall("dc:contributor", NS):
        role = node.get(f"{{{NS['opf']}}}role") or refinements.get((node.get("id", ""), "role"), "")
        name = " ".join("".join(node.itertext()).split())
        if name and role == "trl":
            translators.append(name)
        elif name and (role == "aut" or (node.tag == f"{{{NS['dc']}}}creator" and not role)):
            authors.append(name)
    fallback_title, fallback_author = split_filename_metadata(input_path)
    metadata = BookMetadata(
        title=next(iter(values("title")), fallback_title),
        author=", ".join(authors) or fallback_author,
        translator=", ".join(translators),
        publisher=", ".join(values("publisher")),
        isbn=extract_isbn(values("identifier")),
    )
    for node in metas:
        if node.get("name") == "calibre:series":
            metadata.saga = " ".join(node.get("content", "").split())
        if node.get("property") == "belongs-to-collection":
            kind = refinements.get((node.get("id", ""), "collection-type"), "")
            if kind == "series" or not kind:
                metadata.saga = " ".join((node.text or "").split())
    index = next((node.get("content", "") for node in metas
                  if node.get("name") == "calibre:series_index"), "")
    if metadata.saga and not index:
        index = next((refinements.get((node.get("id", ""), "group-position"), "")
                      for node in metas if node.get("property") == "belongs-to-collection"
                      and " ".join((node.text or "").split()) == metadata.saga), "")
    if metadata.saga:
        metadata.tags += [f"saga/{derive_saga_slug(metadata.saga)}", f"novela/{slugify(metadata.title)}"]
        if index:
            metadata.saga += " - " + " ".join(index.split())
    if metadata.author:
        metadata.tags.append(f"autor/{slugify(metadata.author)}")
    if manual_genres:
        metadata.genres = normalize_manual_genres(manual_genres)
    else:
        metadata.genres = canonical_genres(values("subject"))
        if not metadata.genres and genre_lookup:
            metadata.genres = lookup_genres(metadata.isbn, metadata.title, metadata.author)
    metadata.tags += [f"genero/{genre}" for genre in metadata.genres] + ["novela"]
    return metadata


def collect_references(
    documents: dict[str, HtmlNode],
) -> tuple[dict[HtmlNode, str], dict[tuple[str, str], str], dict[HtmlNode, str]]:
    """Indexa destinos de enlaces y notas antes de representar el libro.

    :param documents: Árboles de los documentos en orden de lectura.
    :returns: Notas, destinos Markdown y anclas de bloque de Obsidian.
    """
    owners: dict[tuple[str, str], HtmlNode] = {}
    notes: dict[HtmlNode, str] = {}
    note_targets: dict[tuple[str, str], str] = {}
    for path, root in documents.items():
        owners[path, ""] = next((node for node in root.walk()
                                  if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}
                                  and node.text().strip()), root)

        def visit(node: HtmlNode, block: HtmlNode) -> None:
            """Asocia identificadores a su bloque de texto más cercano.

            :param node: Nodo actual.
            :param block: Contenedor de bloque más próximo.
            """
            if node.tag in BLOCK_TAGS:
                block = node
            fragment = node.attrs.get("id") or node.attrs.get("name", "")
            if fragment:
                owners[path, fragment] = block
                if epub_types(node) & {"footnote", "endnote", "doc-footnote", "doc-endnote"}:
                    notes[node] = reference_id(path, fragment)
                    note_targets[path, fragment] = notes[node]
            for child in node.children:
                if isinstance(child, HtmlNode):
                    visit(child, block)

        visit(root, root)

    # EPUB 2 no dispone siempre de tipos semánticos: una referencia explícita
    # a nota basta para convertir su bloque de destino en una nota Markdown.
    for path, root in documents.items():
        for node in root.walk():
            if node.tag != "a" or not (epub_types(node) & {"noteref", "doc-noteref"}):
                continue
            href = node.attrs.get("href", "")
            uri = urlsplit(href)
            if uri.scheme or uri.netloc:
                continue
            key = resource_path(path, href), unquote(uri.fragment)
            if key in owners and key[1] and key not in note_targets:
                owner = owners[key]
                notes.setdefault(owner, reference_id(*key))
                note_targets[key] = notes[owner]

    targets: dict[tuple[str, str], str] = {key: f"[^{value}]" for key, value in note_targets.items()}
    anchors: dict[HtmlNode, str] = {}
    for path, root in documents.items():
        for node in root.walk():
            href = node.attrs.get("href", "")
            uri = urlsplit(href)
            if node.tag != "a" or not href or uri.scheme or uri.netloc:
                continue
            key = resource_path(path, href), unquote(uri.fragment)
            if key in owners and key not in targets:
                owner = owners[key]
                anchors.setdefault(owner, reference_id(*key))
                targets[key] = f"#^{anchors[owner]}"
    return notes, targets, anchors


def convert_epub_to_markdown(
    input_path: Path,
    output_path: Path,
    keep_page_breaks: bool = False,
    keep_page_numbers: bool = False,
    genre_lookup: bool = True,
    manual_genres: list[str] | None = None,
) -> None:
    """Convierte un EPUB completo en una nota con imágenes y notas al pie.

    :param input_path: Ruta del archivo EPUB original.
    :param output_path: Ruta del Markdown resultante.
    :param keep_page_breaks: Separa los documentos internos con una línea horizontal.
    :param keep_page_numbers: Conserva marcadores de página presentes en el EPUB.
    :param genre_lookup: Permite la consulta bibliográfica de géneros.
    :param manual_genres: Géneros que sustituyen los del libro y la consulta web.
    :raises ValueError: Si el contenedor es inválido, está cifrado o faltan recursos.
    """
    if input_path.suffix.lower() != ".epub":
        raise ValueError(f"Se esperaba un archivo EPUB, pero llegó: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("La salida no puede sobrescribir el libro de entrada.")
    try:
        with ZipFile(input_path) as archive:
            _convert_archive(archive, input_path, output_path, keep_page_breaks,
                             keep_page_numbers, genre_lookup, manual_genres)
    except (BadZipFile, ET.ParseError, KeyError, UnicodeError, LookupError) as exc:
        raise ValueError(f"EPUB inválido o incompleto: {exc}") from exc


def _convert_archive(
    archive: ZipFile,
    input_path: Path,
    output_path: Path,
    keep_page_breaks: bool,
    keep_page_numbers: bool,
    genre_lookup: bool,
    manual_genres: list[str] | None,
) -> None:
    """Coordina la lectura del contenedor y la escritura de la nota.

    :param archive: ZIP del EPUB abierto.
    :param input_path: Ruta original del libro.
    :param output_path: Ruta de salida solicitada.
    :param keep_page_breaks: Conserva separadores de documentos.
    :param keep_page_numbers: Conserva marcadores de página.
    :param genre_lookup: Permite consultas bibliográficas.
    :param manual_genres: Géneros manuales opcionales.
    """
    container = read_xml(archive, "META-INF/container.xml")
    rootfiles = container.findall("{*}rootfiles/{*}rootfile")
    rootfile = next((node for node in rootfiles
                     if node.get("media-type") == "application/oebps-package+xml"),
                    rootfiles[0] if rootfiles else None)
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("El EPUB no indica dónde está su paquete OPF.")
    package_path = resource_path("", rootfile.attrib["full-path"])
    package = read_xml(archive, package_path)
    manifest = {node.attrib["id"]: node for node in package.findall("opf:manifest/opf:item", NS)}
    paths = {key: resource_path(package_path, node.attrib["href"]) for key, node in manifest.items()}
    media = {paths[key]: node.get("media-type", "") for key, node in manifest.items()}
    spine = [paths[node.attrib["idref"]] for node in package.findall("opf:spine/opf:itemref", NS)]
    if not spine:
        raise ValueError("El EPUB no contiene un orden de lectura (spine).")
    if "META-INF/encryption.xml" in archive.namelist():
        encrypted = {resource_path("", node.attrib["URI"]) for node in
                     read_xml(archive, "META-INF/encryption.xml").iter()
                     if node.tag.endswith("}CipherReference") and node.get("URI")}
        if any(path in encrypted and not kind.startswith("font/")
               and "font" not in kind for path, kind in media.items()):
            raise ValueError("El EPUB contiene texto o imágenes cifrados; no se admite DRM.")

    documents: dict[str, HtmlNode] = {}
    pending = list(spine)
    for path in pending:
        if path in documents:
            continue
        if media.get(path) not in HTML_MEDIA:
            raise ValueError(f"Documento del EPUB no admitido: {path}")
        full_document = parse_xhtml(archive.read(path), body_only=False)
        root = documents[path] = next((node for node in full_document.walk()
                                       if node.tag == "body"), full_document)
        styles = []
        for node in full_document.walk():
            if node.tag == "style":
                styles.append(node.text())
            if node.tag == "link" and "stylesheet" in node.attrs.get("rel", "").split():
                href = node.attrs.get("href", "")
                uri = urlsplit(href)
                if href and not uri.scheme and not uri.netloc:
                    styles.append(archive.read(resource_path(path, href)).decode("utf-8-sig"))
        apply_text_styles(root, "\n".join(styles))
        # Incluye notas y anexos enlazados aunque no figuren en el spine.
        for node in root.walk():
            href = node.attrs.get("href", "")
            uri = urlsplit(href)
            if node.tag == "a" and href and not uri.scheme and not uri.netloc:
                target = resource_path(path, href)
                if media.get(target) in HTML_MEDIA and target not in pending:
                    pending.append(target)

    metadata = extract_epub_metadata(package, input_path, genre_lookup, manual_genres)
    notes, targets, anchors = collect_references(documents)
    assets_dir = output_path.parent / "img"
    prefix = slugify(output_path.stem) or "libro"
    saved: dict[str, str] = {}

    def image_link(base: str, source: str) -> str:
        """Guarda una imagen local una sola vez y crea su enlace Obsidian.

        :param base: Documento que contiene la imagen.
        :param source: URI de la imagen.
        :returns: Enlace de inclusión para la nota.
        """
        uri = urlsplit(source)
        if uri.scheme in {"http", "https"} or uri.netloc:
            return f"![]({quote(source, safe=':/?=&%#')})"
        path = resource_path(base, source)
        if path not in saved:
            ext = IMAGE_EXTENSIONS.get(media.get(path, ""))
            if ext is None:
                raise ValueError(f"Formato de imagen del EPUB no admitido: {path}")
            filename = f"{prefix}-{reference_id(path)}{ext}"
            data = archive.read(path)
            assets_dir.mkdir(parents=True, exist_ok=True)
            (assets_dir / filename).write_bytes(data)
            saved[path] = f"img/{filename}"
        return f"![[{saved[path]}]]"

    cover_id = next((key for key, node in manifest.items()
                     if "cover-image" in node.get("properties", "").split()), "")
    if not cover_id:
        cover_id = next((node.get("content", "") for node in package.findall("opf:metadata/opf:meta", NS)
                         if node.get("name") == "cover"), "")
    cover = image_link(package_path, manifest[cover_id].attrib["href"]) if cover_id in manifest else ""
    if not cover:
        cover_doc = next((resource_path(package_path, node.get("href", "")) for node in
                          package.findall("opf:guide/opf:reference", NS) if node.get("type") == "cover"), "")
        root = documents.get(cover_doc)
        if root is not None:
            image = next((node for node in root.walk() if node.tag in {"img", "image"}), None)
            if image is not None:
                source = image.attrs.get("src") or image.attrs.get("href") or image.attrs.get("xlink:href", "")
                if source:
                    cover = image_link(cover_doc, source)

    blocks, definitions = [], {}
    for path, root in documents.items():
        def resolve_link(href: str) -> str:
            """Reescribe referencias a la nota única sin perder enlaces web.

            :param href: Destino original del enlace.
            :returns: Referencia Markdown a nota al pie, bloque o URL.
            """
            uri = urlsplit(href)
            if not href or uri.scheme or uri.netloc:
                if uri.scheme and uri.scheme not in {"http", "https", "mailto", "tel"}:
                    return ""
                return quote(href, safe=":/?=&%#@+")
            key = resource_path(path, href), unquote(uri.fragment)
            return targets.get(key, "")

        renderer = MarkdownRenderer(lambda src: image_link(path, src), resolve_link,
                                    notes, anchors, keep_page_numbers)
        content = renderer.render(root).strip()
        # La portada se incluye una vez al principio y no en su página original.
        if cover:
            content = content.replace(cover, "")
        content = clean_markdown(content)
        if content:
            blocks.append(content)
        definitions.update(renderer.definitions)
    note_blocks = []
    for key, value in definitions.items():
        lines = clean_markdown(value).splitlines()
        if lines:
            note_blocks.append(f"[^{key}]: " + lines[0] + "\n" + "\n".join("    " + line for line in lines[1:]))
    frontmatter = build_frontmatter(metadata, cover[3:-2] if cover.startswith("![[") else "")
    separator = "\n\n---\n\n" if keep_page_breaks else "\n\n"
    body = "\n\n".join(block for block in [separator.join(blocks), "\n\n".join(note_blocks)] if block)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(block for block in [frontmatter, cover, body] if block).rstrip() + "\n",
                           encoding="utf-8")
