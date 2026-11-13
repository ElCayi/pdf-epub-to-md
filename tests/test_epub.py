"""Pruebas de EPUB con libros mínimos generados sin red ni archivos privados."""

import base64
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from novel_pdf.converter import convert_to_markdown
from novel_pdf.epub import convert_epub_to_markdown

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aYxkAAAAASUVORK5CYII="
)
PROJECT = Path(__file__).resolve().parents[1]


def make_epub(path, *, version="3.0", metadata="", chapters=None, resources=None,
              extra_manifest="", spine=None):
    """Construye un EPUB pequeño con un orden de lectura explícito.

    :param path: Ruta del EPUB de prueba.
    :param version: Versión del paquete OPF.
    :param metadata: Metadatos XML adicionales.
    :param chapters: Diccionario de archivos y cuerpos XHTML.
    :param resources: Recursos binarios o textuales adicionales.
    :param extra_manifest: Entradas XML adicionales del manifiesto.
    :param spine: Orden de los archivos; por defecto, orden del diccionario.
    :returns: Ruta del libro generado.
    """
    chapters = chapters if chapters is not None else {"one.xhtml": "<h1>Capítulo 1</h1><p>Hola.</p>"}
    spine = spine if spine is not None else list(chapters)
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
            <rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles>
            </container>''')
        manifest = "".join(f'<item id="d{i}" href="{name}" media-type="application/xhtml+xml"/>'
                           for i, name in enumerate(chapters))
        order = "".join(f'<itemref idref="d{list(chapters).index(name)}"/>' for name in spine)
        archive.writestr("OPS/book.opf", f'''<package xmlns="http://www.idpf.org/2007/opf" version="{version}"
            xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
            <metadata><dc:title>La novela</dc:title><dc:creator id="author">Ana Autora</dc:creator>
            {metadata}</metadata><manifest>{manifest}{extra_manifest}</manifest><spine>{order}</spine></package>''')
        # El orden físico del ZIP es intencionadamente diferente al spine.
        for name, body in reversed(list(chapters.items())):
            if not body.startswith("<?xml"):
                body = f'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
                    <head><title>Ignorar este título</title></head><body>{body}</body></html>'''
            archive.writestr("OPS/" + name, body)
        for name, data in (resources or {}).items():
            archive.writestr(name, data)
    return path


class EpubTests(unittest.TestCase):
    def setUp(self):
        """Crea una carpeta aislada por prueba."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "book.epub"
        self.output = self.root / "note.md"

    def convert(self, **options):
        """Convierte el libro de prueba sin consultar Internet.

        :param options: Opciones adicionales del conversor.
        :returns: Contenido Markdown de salida.
        """
        convert_to_markdown(self.source, self.output, genre_lookup=False, **options)
        return self.output.read_text()

    def test_reading_order_and_inline_format(self):
        make_epub(self.source, chapters={
            "z.xhtml": '<h1>Primero</h1><p>Uno <em>cursiva</em> y <strong>negrita</strong>.</p><p>Dos.</p>',
            "a.xhtml": '<h2>Segundo</h2><blockquote><p>Una cita.</p></blockquote><hr/>',
        })
        result = self.convert()
        self.assertLess(result.index("# Primero"), result.index("## Segundo"))
        self.assertIn("Uno *cursiva* y **negrita**.\n\nDos.", result)
        self.assertIn("> Una cita.", result)
        self.assertIn('<div class="scene-break"></div>', result)
        self.assertNotIn("Ignorar", result)
        self.assertNotIn("pages:", result)
        self.assertIn("cssclasses: novela", result)

    def test_metadata_epub2_and_manual_genres(self):
        make_epub(self.source, version="2.0", metadata='''
            <dc:contributor opf:role="trl">Eva Traductora</dc:contributor>
            <dc:publisher>Editorial Prueba</dc:publisher><dc:subject>Science Fiction</dc:subject>
            <meta name="calibre:series" content="La Saga"/><meta name="calibre:series_index" content="2"/>''')
        result = self.convert(manual_genres=["terror"])
        self.assertIn('translator: "Eva Traductora"', result)
        self.assertIn('publisher: "Editorial Prueba"', result)
        self.assertIn('saga: "La Saga - 2"', result)
        self.assertIn("saga/la-saga", result)
        self.assertIn("genero/terror", result)
        self.assertNotIn("ciencia-ficcion", result)

    def test_epub3_roles_series_and_native_genre_avoid_network(self):
        make_epub(self.source, metadata='''<meta refines="#author" property="role">aut</meta>
            <dc:creator id="translator">Eva</dc:creator><meta refines="#translator" property="role">trl</meta>
            <meta property="belongs-to-collection" id="saga">La Saga</meta>
            <meta refines="#saga" property="collection-type">series</meta>
            <meta refines="#saga" property="group-position">3</meta>
            <dc:subject>Science Fiction</dc:subject>''')
        with patch("novel_pdf.epub.lookup_genres") as lookup:
            convert_epub_to_markdown(self.source, self.output)
        result = self.output.read_text()
        self.assertIn('author: "Ana Autora"', result)
        self.assertIn('translator: "Eva"', result)
        self.assertIn('saga: "La Saga - 3"', result)
        self.assertIn("genero/ciencia-ficcion", result)
        lookup.assert_not_called()

    def test_isbn_from_opf_is_used_for_genre_lookup(self):
        make_epub(self.source, metadata='<dc:identifier>urn:isbn:9780306406157</dc:identifier>')
        with patch("novel_pdf.epub.lookup_genres", return_value=["fantasia"]) as lookup:
            convert_epub_to_markdown(self.source, self.output)
        lookup.assert_called_once_with("9780306406157", "La novela", "Ana Autora")
        self.assertIn("genero/fantasia", self.output.read_text())

    def test_cover_and_image_reuse_with_relative_percent_encoded_paths(self):
        make_epub(self.source, chapters={
            "text/one.xhtml": '<img src="../images/cover%20art.png"/><h1>Inicio</h1>'
                              '<p><img src="../images/pic.png"/></p><img src="../images/pic.png"/>',
        }, extra_manifest='''<item id="cover" href="images/cover%20art.png" media-type="image/png" properties="cover-image"/>
            <item id="pic" href="images/pic.png" media-type="image/png"/>''', resources={
            "OPS/images/cover art.png": PNG, "OPS/images/pic.png": PNG,
        })
        result = self.convert()
        cover = re.search(r'cover: "([^"]+)"', result)[1]
        self.assertEqual(result.count(f"![[{cover}]]"), 1)
        self.assertLess(result.index(f"![[{cover}]]"), result.index("# Inicio"))
        self.assertEqual(len(list((self.root / "img").iterdir())), 2)
        self.assertEqual((self.root / cover).read_bytes(), PNG)
        images = re.findall(r"!\[\[(img/[^]]+)\]\]", result)
        self.assertEqual(images[1], images[2])

    def test_epub2_cover_metadata_and_svg_wrapper(self):
        make_epub(self.source, version="2.0", metadata='<meta name="cover" content="cover"/>',
                  chapters={"one.xhtml": '<svg xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="cover.png"/></svg><p>Texto.</p>'},
                  extra_manifest='<item id="cover" href="cover.png" media-type="image/png"/>',
                  resources={"OPS/cover.png": PNG})
        result = self.convert()
        self.assertEqual(result.count("![[img/"), 1)
        self.assertIn("Texto.", result)

    def test_footnotes_in_linked_non_spine_document(self):
        make_epub(self.source, chapters={
            "one.xhtml": '<h1>Capítulo</h1><p>Texto<a epub:type="noteref" href="notes.xhtml#n1">1</a>.</p>',
            "notes.xhtml": '<aside epub:type="footnote" id="n1"><p>Una <em>nota</em>.</p><p>Otro párrafo. '
                           '<a role="doc-backlink" href="one.xhtml">Volver</a></p></aside>',
        }, spine=["one.xhtml"])
        result = self.convert()
        label = re.search(r"Texto\[\^([^]]+)\]\.", result)[1]
        self.assertIn(f"[^{label}]: Una *nota*.", result)
        self.assertIn("    Otro párrafo.", result)
        self.assertNotIn("Volver", result)
        self.assertEqual(result.count("Una *nota*."), 1)

    def test_internal_links_target_obsidian_blocks(self):
        make_epub(self.source, chapters={
            "one.xhtml": '<p><a href="two.xhtml#dest">Siguiente</a></p>',
            "two.xhtml": '<h2 id="dest">Destino</h2><p>Texto.</p>',
        })
        result = self.convert()
        label = re.search(r"\[Siguiente\]\(#\^([^)]+)\)", result)[1]
        self.assertIn(f"## Destino\n\n^{label}", result)
        self.assertNotIn("two.xhtml", result)

    def test_notes_with_repeated_ids_in_different_chapters(self):
        make_epub(self.source, chapters={
            "one.xhtml": '<p>Uno<a epub:type="noteref" href="#note">1</a></p>'
                         '<aside id="note" epub:type="footnote">Nota uno.</aside>',
            "two.xhtml": '<p>Dos<a epub:type="noteref" href="#note">1</a></p>'
                         '<aside id="note" epub:type="footnote">Nota dos.</aside>',
        })
        result = self.convert()
        labels = re.findall(r"^\[\^([^]]+)\]:", result, re.M)
        self.assertEqual(len(set(labels)), 2)

    def test_lists_tables_links_and_literal_markdown(self):
        make_epub(self.source, chapters={"one.xhtml": '''
            <ol start="3"><li>Primero<ul><li>Anidado</li></ul></li><li>Segundo</li></ol>
            <table><tr><th>A</th><th>B</th></tr><tr><td>Uno</td><td>Dos</td></tr></table>
            <p>Asterisco *literal* y [corchetes].</p><p>1. Una frase.</p>
            <p><a href="https://example.org/a(b)">Web</a></p><p>Hola<br/>mundo.</p>
            <p><code>a*b</code> H<sub>2</sub>O</p>'''})
        result = self.convert()
        self.assertIn("3. Primero", result)
        self.assertIn("   - Anidado", result)
        self.assertIn("4. Segundo", result)
        self.assertIn("| A | B |\n| --- | --- |", result)
        self.assertIn(r"\*literal\* y \[corchetes\]", result)
        self.assertIn(r"1\. Una frase.", result)
        self.assertIn("https://example.org/a%28b%29", result)
        self.assertIn("Hola  \nmundo.", result)
        self.assertIn("H<sub>2</sub>O", result)

    def test_css_emphasis_and_inline_override(self):
        make_epub(self.source, chapters={"one.xhtml": '''<?xml version="1.0" encoding="UTF-8"?>
            <html><head><link rel="stylesheet" href="styles.css"/><style>.fuerte {font-weight: bold;}</style></head>
            <body><p>Una <span class="cursiva">idea</span> <span class="fuerte">fuerte</span>.</p>
            <p><span class="cursiva" style="font-style:normal">Normal</span></p></body></html>'''},
                  resources={"OPS/styles.css": ".cursiva {font-style:italic;}"})
        result = self.convert()
        self.assertIn("Una *idea* **fuerte**.", result)
        self.assertIn("\n\nNormal\n", result)
        self.assertNotIn("*Normal*", result)

    def test_page_options_use_epub_markers_not_invented_pages(self):
        make_epub(self.source, chapters={
            "one.xhtml": '<p>Uno<span epub:type="pagebreak" title="25"/> continúa.</p>',
            "two.xhtml": '<p>Dos.</p>',
        })
        self.assertNotIn("25", self.convert())
        result = self.convert(keep_page_breaks=True, keep_page_numbers=True)
        self.assertIn("25", result)
        self.assertIn("continúa.\n\n---\n\nDos.", result)
        self.assertNotIn("pages:", result)

    def test_multiline_headings_and_code_keep_their_meaning(self):
        make_epub(self.source, chapters={"one.xhtml":
            '<h2>Capítulo 1<br/>El comienzo</h2><pre>uno\n\n\ncuatro\n```</pre>'})
        result = self.convert()
        self.assertIn("## Capítulo 1 El comienzo", result)
        self.assertIn("````\nuno\n\n\ncuatro\n```\n````", result)

    def test_nested_emphasis_does_not_change_italic_to_bold(self):
        make_epub(self.source, chapters={"one.xhtml":
            '<p><em>Una <i>idea</i> clara.</em></p>'
            '<p><b>Una <strong>idea</strong> fuerte.</b></p>'})
        result = self.convert()
        self.assertIn("*Una idea clara.*", result)
        self.assertIn("**Una idea fuerte.**", result)

    def test_document_links_jump_to_its_first_heading(self):
        make_epub(self.source, chapters={
            "one.xhtml": '<p><a href="two.xhtml">Siguiente</a></p>',
            "two.xhtml": '<h2>Capítulo dos</h2><p>Final.</p>',
        })
        result = self.convert()
        label = re.search(r"\[Siguiente\]\(#\^([^)]+)\)", result)[1]
        self.assertIn(f"## Capítulo dos\n\n^{label}\n\nFinal.", result)

    def test_cli_writes_next_to_book_and_supports_uppercase_extension(self):
        self.source = self.root / "book.EPUB"
        make_epub(self.source)
        result = subprocess.run([sys.executable, str(PROJECT / "pdf_epub_to_md.py"), str(self.source),
                                 "-o", "out.md", "--no-genre-lookup"], cwd=PROJECT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "out.md").exists())

    def test_invalid_epub_cli_reports_error_without_traceback(self):
        self.source.write_bytes(b"not a zip")
        result = subprocess.run([sys.executable, str(PROJECT / "pdf_to_md.py"), str(self.source),
                                 "--no-genre-lookup"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("EPUB inválido", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_resource_does_not_create_markdown(self):
        make_epub(self.source, chapters={"one.xhtml": '<img src="missing.png"/>'},
                  extra_manifest='<item id="missing" href="missing.png" media-type="image/png"/>')
        with self.assertRaisesRegex(ValueError, "incompleto"):
            self.convert()
        self.assertFalse(self.output.exists())

    def test_archive_paths_cannot_escape_image_directory(self):
        make_epub(self.source, chapters={"one.xhtml": '<img src="../../outside.png"/>'})
        with self.assertRaisesRegex(ValueError, "Ruta interna"):
            self.convert()
        self.assertFalse((self.root / "outside.png").exists())

    def test_encrypted_text_has_clear_error(self):
        make_epub(self.source, resources={"META-INF/encryption.xml": '''
            <encryption xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
            <enc:EncryptedData><enc:CipherData><enc:CipherReference URI="OPS/one.xhtml"/>
            </enc:CipherData></enc:EncryptedData></encryption>'''})
        with self.assertRaisesRegex(ValueError, "DRM"):
            self.convert()

    def test_reject_overwriting_original_and_unknown_formats(self):
        make_epub(self.source)
        with self.assertRaisesRegex(ValueError, "sobrescribir"):
            convert_to_markdown(self.source, self.source)
        with self.assertRaisesRegex(ValueError, "PDF o EPUB"):
            convert_to_markdown(self.root / "book.pub", self.output)

    def test_existing_pdf_dispatch_is_preserved(self):
        with patch("novel_pdf.converter.convert_pdf_to_markdown") as convert_pdf:
            convert_to_markdown(self.root / "book.pdf", self.output, genre_lookup=False)
        convert_pdf.assert_called_once_with(self.root / "book.pdf", self.output, False, False, False, None)


if __name__ == "__main__":
    unittest.main()
