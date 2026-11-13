import unittest

from novel_pdf.common import LineItem
from novel_pdf.layout import (
    append_to_paragraph,
    estimate_style_left,
    extract_part_page_headings,
    flush_paragraph,
    has_scene_break_before,
    is_chapter_heading,
    starts_indented_paragraph,
    starts_new_text_block,
)


def line(
    text: str,
    *,
    block_id: int = 0,
    x0: float = 50.0,
    y0: float = 0.0,
    y1: float = 14.4,
    bold: bool = False,
    italic: bool = False,
) -> LineItem:
    return LineItem(
        text=text,
        size=14.4,
        bold=bold,
        italic=italic,
        block_id=block_id,
        x0=x0,
        y0=y0,
        y1=y1,
        page_height=842.0,
    )


class StructureTests(unittest.TestCase):
    def test_alphanumeric_chapter_heading(self) -> None:
        self.assertTrue(is_chapter_heading("000X: La directora."))

    def test_same_pdf_block_is_not_a_new_paragraph(self) -> None:
        first = line("He puesto a punto la lente. He", block_id=1, x0=93.2)
        second = line("arreglado la tubería.", block_id=1, x0=71.6)
        self.assertFalse(starts_new_text_block(first, second))

    def test_different_pdf_blocks_start_a_paragraph(self) -> None:
        self.assertTrue(starts_new_text_block(line("Uno.", block_id=1), line("Dos.", block_id=2)))

    def test_first_line_indent_starts_a_paragraph_inside_same_block(self) -> None:
        dialogue = line("—¿Dónde está?", block_id=1, x0=71.6)
        self.assertTrue(starts_indented_paragraph(dialogue, body_left=50.0, body_size=14.4))

    def test_body_margin_line_continues_current_paragraph(self) -> None:
        continuation = line("continúa la frase.", block_id=1, x0=50.0)
        self.assertFalse(
            starts_indented_paragraph(continuation, body_left=50.0, body_size=14.4)
        )

    def test_italic_block_uses_its_own_continuation_margin(self) -> None:
        lines = [
            line("Primera línea", x0=93.2, italic=True),
            line("segunda línea", x0=71.6, italic=True),
        ]
        italic_left = estimate_style_left(
            lines, body_size=14.4, italic=True, fallback=50.0
        )
        self.assertEqual(italic_left, 71.6)
        self.assertFalse(
            starts_indented_paragraph(lines[1], italic_left, body_size=14.4)
        )

    def test_large_vertical_gap_is_a_scene_break(self) -> None:
        previous = line("Fin.", y0=0.0, y1=14.4)
        current = line("Inicio.", y0=32.4, y1=46.8)
        self.assertTrue(has_scene_break_before(previous, current, 3.6, 14.4))

    def test_part_cover_becomes_two_heading_levels(self) -> None:
        lines = [
            line("PRIMERA PARTE", block_id=0, bold=True, y0=52.4, y1=76.0),
            line("Luz de enfilación", block_id=1, bold=True, y0=76.2, y1=100.0),
        ]
        self.assertEqual(
            extract_part_page_headings(lines),
            {0: (2, "PRIMERA PARTE"), 1: (3, "Luz de enfilación")},
        )

    def test_adjacent_italic_lines_become_one_emphasis_span(self) -> None:
        paragraph: list[str] = []
        output: list[str] = []
        append_to_paragraph(paragraph, "*Primera línea*")
        append_to_paragraph(paragraph, "*segunda línea*")
        flush_paragraph(paragraph, output)
        self.assertEqual(output, ["*Primera línea segunda línea*"])


if __name__ == "__main__":
    unittest.main()
