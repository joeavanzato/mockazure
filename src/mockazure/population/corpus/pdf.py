"""Bounded Unicode PDF output; no markup parsing, links, or external resource access."""

import io
import threading
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from mockazure.population.corpus.text import paragraphs

_LOCK = threading.Lock()
_NAME = "MockAzureNotoSans"


def render_pdf(spec):
    # ReportLab registers font objects globally. Serialize registration and subsetting.
    with _LOCK:
        if _NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(
                TTFont(_NAME, str(Path(__file__).parent / "data/NotoSans-Regular.ttf"))
            )
        font = pdfmetrics.getFont(_NAME)
        lines = paragraphs(spec)
        for line in lines:
            if any(ord(c) not in font.face.charToGlyph for c in line if c not in "\n\r\t"):
                raise ValueError("PDF font has no glyph for a requested character")
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=(595, 842), invariant=1, pageCompression=1)
        canvas.setTitle("Synthetic business document")
        canvas.setAuthor("MockAzure")
        canvas.setCreator("MockAzure corpus-v1")
        y = 794
        canvas.setFont(_NAME, 10)
        for paragraph in lines:
            for line in paragraph.expandtabs(4).splitlines() or [""]:
                # Wrap by font width, not by byte count, without dropping long tokens.
                current, width = "", 0
                for character in line:
                    advance = pdfmetrics.stringWidth(character, _NAME, 10)
                    if current and width + advance > 499:
                        if y < 48:
                            canvas.showPage()
                            canvas.setFont(_NAME, 10)
                            y = 794
                        canvas.drawString(48, y, current)
                        y -= 15
                        current, width = "", 0
                    current += character
                    width += advance
                if y < 48:
                    canvas.showPage()
                    canvas.setFont(_NAME, 10)
                    y = 794
                canvas.drawString(48, y, current)
                y -= 15
            y -= 8
        canvas.save()
        return buffer.getvalue()
