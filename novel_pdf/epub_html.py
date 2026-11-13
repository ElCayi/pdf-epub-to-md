"""Lectura de XHTML y representación Markdown compatible con Obsidian."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Iterator

from .common import SCENE_BREAK_MARKER

BLOCK_TAGS = {"body", "section", "article", "div", "p", "blockquote", "li",
              "h1", "h2", "h3", "h4", "h5", "h6", "pre", "figure", "aside"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr", "image"}


@dataclass(eq=False)
class HtmlNode:
    """Elemento XHTML con atributos e hijos en su orden original.

    :ivar tag: Nombre local de la etiqueta.
    :ivar attrs: Atributos del elemento, incluidos los prefijos EPUB.
    :ivar children: Texto y elementos anidados.
    """

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[str | HtmlNode] = field(default_factory=list)

    def walk(self) -> Iterator[HtmlNode]:
        """Recorre el elemento y sus descendientes.

        :yields: Nodos en orden de lectura.
        """
        yield self
        for child in self.children:
            if isinstance(child, HtmlNode):
                yield from child.walk()

    def text(self) -> str:
        """Obtiene texto sin marcas.

        :returns: Concatenación del texto del elemento.
        """
        return "".join(child if isinstance(child, str) else child.text()
                       for child in self.children)


class XhtmlParser(HTMLParser):
    """Lee XHTML sin descargar recursos ni ejecutar contenido del libro."""

    def __init__(self) -> None:
        """Inicializa un árbol vacío y la pila de elementos abiertos."""
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Añade un elemento al árbol.

        :param tag: Nombre de etiqueta recibido por el analizador.
        :param attrs: Pares de atributos del elemento.
        """
        node = HtmlNode(tag.split(":")[-1], dict((k, v or "") for k, v in attrs))
        self.stack[-1].children.append(node)
        if node.tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        """Cierra el elemento correspondiente si sigue abierto.

        :param tag: Nombre de la etiqueta de cierre.
        """
        tag = tag.split(":")[-1]
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Lee elementos autocerrados, incluidos los SVG.

        :param tag: Nombre de etiqueta.
        :param attrs: Atributos del elemento.
        """
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        """Conserva texto y espacios entre elementos.

        :param data: Fragmento textual recibido.
        """
        self.stack[-1].children.append(data)


def parse_xhtml(data: bytes, body_only: bool = True) -> HtmlNode:
    """Lee un documento XHTML del contenedor EPUB.

    :param data: Contenido del documento, normalmente UTF-8.
    :param body_only: Devuelve únicamente el cuerpo si está presente.
    :returns: Cuerpo del documento o raíz si no contiene ``body``.
    """
    encoding = re.search(br'<\?xml[^>]*encoding=[\'"]([^\'"]+)', data[:200])
    codec = encoding.group(1).decode("ascii") if encoding else "utf-8-sig"
    parser = XhtmlParser()
    parser.feed(data.decode(codec))
    parser.close()
    if not body_only:
        return parser.root
    return next((node for node in parser.root.walk() if node.tag == "body"), parser.root)


def epub_types(node: HtmlNode) -> set[str]:
    """Obtiene los tipos semánticos EPUB y los roles de accesibilidad.

    :param node: Elemento del documento.
    :returns: Tipos declarados por el editor.
    """
    return set((node.attrs.get("epub:type", "") + " " + node.attrs.get("role", "")).split())


def reference_id(path: str, fragment: str = "") -> str:
    """Crea identificadores estables sin colisiones entre capítulos.

    :param path: Ruta interna del documento.
    :param fragment: Identificador local del elemento.
    :returns: Identificador válido para notas y bloques de Obsidian.
    """
    return "epub-" + hashlib.sha256(f"{path}#{fragment}".encode()).hexdigest()[:16]


def escape_text(text: str) -> str:
    """Protege texto literal frente a la sintaxis Markdown.

    :param text: Texto plano del XHTML.
    :returns: Texto normalizado con caracteres Markdown escapados.
    """
    text = re.sub(r"\s+", " ", text.replace("\u00ad", ""))
    return re.sub(r"([\\`*_[\]<>#|~=$^])", r"\\\1", text)


def clean_markdown(text: str) -> str:
    """Reduce separadores redundantes respetando bloques de código.

    :param text: Markdown recién representado.
    :returns: Texto con un único espacio en blanco entre bloques.
    """
    lines: list[str] = []
    fence = ""
    for line in text.splitlines():
        match = re.match(r"^(`{3,})(?:[^`].*)?$", line)
        if match:
            if not fence:
                fence = match[1]
            elif line.strip() == fence:
                fence = ""
        if not fence and not line.strip():
            if lines and lines[-1] != "":
                lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines).strip()


def apply_text_styles(root: HtmlNode, css: str) -> None:
    """Aplica énfasis de reglas CSS sencillas sin reproducir la maquetación.

    :param root: Árbol del documento XHTML.
    :param css: Hojas de estilo del documento.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        styles = ";".join(re.findall(r"font-(?:style|weight)\s*:[^;}]+", declarations, re.I))
        if not styles:
            continue
        for selector in selectors.split(","):
            selector = selector.strip()
            # Se admiten etiquetas, clases e identificadores simples. Selectores
            # con contexto, pseudoclases o media queries necesitan un motor CSS.
            if re.fullmatch(r"(?:[\w-]+|\*)?(?:[.#][\w-]+)*", selector) and selector:
                specificity = (selector.count("#"), selector.count("."), bool(re.match(r"^[\w-]", selector)))
                rules.append((specificity, selector, styles))
    rules.sort(key=lambda rule: rule[0])
    for node in root.walk():
        styles = []
        for _, selector, declarations in rules:
            tag = re.match(r"^[\w-]+", selector)
            classes = re.findall(r"\.([\w-]+)", selector)
            ids = re.findall(r"#([\w-]+)", selector)
            if (not tag or tag[0] == node.tag) and set(classes) <= set(node.attrs.get("class", "").split()) and all(
                value == node.attrs.get("id") for value in ids
            ):
                styles.append(declarations)
        # El estilo en línea tiene prioridad sobre las reglas de la hoja.
        node.attrs["style"] = ";".join(styles + [node.attrs.get("style", "")])


class MarkdownRenderer:
    """Representa el árbol XHTML sin aplicar el diseño de página del lector."""

    def __init__(
        self,
        image: Callable[[str], str],
        link: Callable[[str], str],
        footnotes: dict[HtmlNode, str],
        anchors: dict[HtmlNode, str],
        keep_page_numbers: bool = False,
    ) -> None:
        """Configura los recursos y referencias de un documento.

        :param image: Función que guarda una imagen y devuelve su enlace.
        :param link: Función que resuelve enlaces internos y externos.
        :param footnotes: Elementos que se representarán como notas al pie.
        :param anchors: Identificadores de bloques que reciben enlaces internos.
        :param keep_page_numbers: Conserva los marcadores de página EPUB.
        """
        self.image = image
        self.link = link
        self.footnotes = footnotes
        self.anchors = anchors
        self.keep_page_numbers = keep_page_numbers
        self.definitions: dict[str, str] = {}

    def children(self, node: HtmlNode) -> str:
        """Representa los hijos en su orden original.

        :param node: Elemento contenedor.
        :returns: Markdown de su contenido.
        """
        return "".join(escape_text(child) if isinstance(child, str) else self.render(child)
                       for child in node.children)

    def render(self, node: HtmlNode) -> str:
        """Convierte un elemento y conserva los destinos de enlaces.

        :param node: Elemento XHTML.
        :returns: Fragmento Markdown con separadores de bloque.
        """
        if node in self.footnotes:
            content = self.children(node).strip()
            self.definitions[self.footnotes[node]] = content
            return ""
        content = self.element(node)
        if content.strip() and node in self.anchors:
            content = content.rstrip() + f"\n\n^{self.anchors[node]}\n\n"
        return content

    def element(self, node: HtmlNode) -> str:
        """Representa la semántica de una etiqueta HTML.

        :param node: Elemento que se desea convertir.
        :returns: Fragmento Markdown para Obsidian.
        """
        tag, attrs, types = node.tag, node.attrs, epub_types(node)
        if tag in {"head", "script", "style", "template"} or types & {"backlink", "doc-backlink"}:
            return ""
        if "pagebreak" in types or "doc-pagebreak" in types:
            label = attrs.get("title") or attrs.get("aria-label") or node.text()
            return f"\n\n{escape_text(label)}\n\n" if self.keep_page_numbers else ""
        if tag in {"img", "image"}:
            source = attrs.get("src") or attrs.get("href") or attrs.get("xlink:href", "")
            return self.image(source) if source else ""
        if tag == "br":
            return "  \n"
        if tag == "hr":
            return f"\n\n{SCENE_BREAK_MARKER}\n\n"
        if tag == "pre":
            text = node.text().strip("\n")
            fence = "`" * max(3, max((len(m[0]) + 1 for m in re.finditer(r"`+", text)), default=3))
            return f"\n\n{fence}\n{text}\n{fence}\n\n"
        if tag == "code":
            text = node.text().replace("\n", " ")
            fence = "`" * max(1, max((len(m[0]) + 1 for m in re.finditer(r"`+", text)), default=1))
            return f"{fence} {text} {fence}"
        if tag in {"ul", "ol"}:
            return self.render_list(node)
        if tag == "table":
            return self.render_table(node)

        content = self.children(node)
        if tag == "a":
            destination = self.link(attrs.get("href", ""))
            if destination.startswith("[^"):
                return destination
            return f"[{content.strip()}]({destination})" if destination and content.strip() else content
        if re.fullmatch(r"h[1-6]", tag):
            heading = re.sub(r"\s+", " ", content).strip()
            return f"\n\n{'#' * int(tag[1])} {heading}\n\n"
        if tag == "blockquote":
            return "\n\n" + "\n".join("> " + line if line else ">" for line in content.strip().splitlines()) + "\n\n"
        if tag in {"em", "i", "strong", "b", "del", "s", "mark"}:
            marker = {"em": "*", "i": "*", "strong": "**", "b": "**",
                      "del": "~~", "s": "~~", "mark": "=="}[tag]
            return self.emphasis(content, marker)
        style = dict((key.lower(), value.strip().lower().replace("!important", "").strip())
                     for key, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", attrs.get("style", "")))
        if style.get("font-style") in {"italic", "oblique"}:
            content = self.emphasis(content, "*")
        if style.get("font-weight") in {"bold", "700", "800", "900"}:
            content = self.emphasis(content, "**")
        if tag in BLOCK_TAGS or tag in {"figcaption", "dl", "dt", "dd", "nav"}:
            if tag == "p":
                content = re.sub(r"^(\s*)([-+])(?=\s)", r"\1\\\2", content)
                content = re.sub(r"^(\s*\d+)([.)])(?=\s)", r"\1\\\2", content)
            return "\n\n" + content.strip() + "\n\n" if content.strip() else ""
        # Subíndices y superíndices se conservan con HTML admitido por Obsidian.
        if tag in {"sub", "sup"}:
            return f"<{tag}>{content}</{tag}>" if not content.startswith("[^") else content
        return content

    @staticmethod
    def emphasis(content: str, marker: str) -> str:
        """Coloca las marcas de énfasis dentro de los espacios periféricos.

        :param content: Fragmento a enfatizar.
        :param marker: Marca Markdown de apertura y cierre.
        :returns: Texto enfatizado o espacios si no hay contenido.
        """
        stripped = content.strip()
        if not stripped:
            return content
        # El énfasis anidado del mismo tipo no debe convertirse por accidente
        # en negrita ni cerrar las marcas del contenedor.
        if marker in {"*", "**"}:
            def reduce_marks(match: re.Match[str]) -> str:
                """Elimina una capa de énfasis redundante.

                :param match: Secuencia de asteriscos sin escapar.
                :returns: Marcas que conservan el otro tipo de énfasis.
                """
                count = len(match[0])
                if marker == "*":
                    return "*" * (count - count % 2)
                return "*" * (count - 2 if count >= 2 else count)

            stripped = re.sub(r"(?<![\\*])\*+(?!\*)", reduce_marks, stripped)
        left = content[:len(content) - len(content.lstrip())]
        right = content[len(content.rstrip()):]
        return f"{left}{marker}{stripped}{marker}{right}"

    def render_list(self, node: HtmlNode) -> str:
        """Convierte listas anidadas sin perder párrafos de sus elementos.

        :param node: Lista ordenada o sin numerar.
        :returns: Bloque Markdown con sangría para las sublistas.
        """
        items: list[str] = []
        number = int(node.attrs.get("start", "1")) if node.attrs.get("start", "1").isdigit() else 1
        for child in node.children:
            if not isinstance(child, HtmlNode) or child.tag != "li":
                continue
            if child in self.footnotes:
                self.render(child)
                continue
            marker = f"{number}. " if node.tag == "ol" else "- "
            content = self.render(child).strip().splitlines()
            if content:
                items.append(marker + content[0] + "\n" + "\n".join(
                    " " * len(marker) + line if line else "" for line in content[1:]))
            number += 1
        return "\n\n" + "\n".join(items).rstrip() + "\n\n" if items else ""

    def render_table(self, node: HtmlNode) -> str:
        """Convierte tablas sencillas con una cabecera Markdown.

        :param node: Elemento de tabla.
        :returns: Tabla Markdown; las celdas combinadas se aplanan.
        """
        rows = []
        for row in node.walk():
            if row.tag != "tr":
                continue
            cells = [re.sub(r"\s*\n\s*", "<br>", self.children(cell).strip())
                     for cell in row.children if isinstance(cell, HtmlNode) and cell.tag in {"th", "td"}]
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        width = max(map(len, rows))
        lines = ["| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in rows]
        lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
        return "\n\n" + "\n".join(lines) + "\n\n"
