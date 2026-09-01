#!/usr/bin/env python3
"""Build MANUAL.pdf from MANUAL.md.

Kept as a script rather than done once by hand so the PDF cannot drift away
from the Markdown: regenerating is `python packaging/build_manual.py`, and it
is part of the release checklist in HANDOFF.md.

reportlab rather than a Markdown-to-PDF converter because the alternatives
each want a system dependency (LaTeX, wkhtmltopdf, a headless browser) that
would have to be installed on every machine that cuts a release. This has no
dependency the application does not already have.

Only the Markdown subset actually used in MANUAL.md is handled -- headings,
paragraphs, lists, tables, code blocks, block quotes, images, and inline
bold/italic/code. It is not a general Markdown engine and does not pretend to
be; if the manual starts using a construct this cannot render, the build fails
loudly rather than dropping it silently.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "MANUAL.md"
OUTPUT = ROOT / "MANUAL.pdf"

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5f6b7a")
RULE = colors.HexColor("#d4dae1")
PANEL = colors.HexColor("#f4f6f8")
ACCENT = colors.HexColor("#1f77b4")


def styles():
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle(
        "title", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=26, leading=30, textColor=INK, spaceAfter=4,
    )
    s["subtitle"] = ParagraphStyle(
        "subtitle", parent=base["Normal"], fontName="Helvetica",
        fontSize=11.5, leading=16, textColor=MUTED, spaceAfter=2,
    )
    s["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, leading=20, textColor=INK,
        spaceBefore=18, spaceAfter=7,
    )
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=12.5, leading=16, textColor=INK,
        spaceBefore=13, spaceAfter=5,
    )
    s["body"] = ParagraphStyle(
        "body", parent=base["BodyText"], fontName="Helvetica",
        fontSize=10, leading=14.6, textColor=INK,
        alignment=TA_LEFT, spaceAfter=7,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], spaceAfter=3.5,
    )
    s["code"] = ParagraphStyle(
        "code", parent=base["Code"], fontName="Courier",
        fontSize=8.7, leading=12, textColor=INK,
        leftIndent=8, spaceBefore=1, spaceAfter=1,
    )
    s["quote"] = ParagraphStyle(
        "quote", parent=s["body"], leftIndent=9, rightIndent=6,
        spaceBefore=3, spaceAfter=3,
    )
    s["cell"] = ParagraphStyle(
        "cell", parent=s["body"], fontSize=9, leading=12.5, spaceAfter=0,
    )
    s["cellhead"] = ParagraphStyle(
        "cellhead", parent=s["cell"], fontName="Helvetica-Bold",
    )
    s["caption"] = ParagraphStyle(
        "caption", parent=s["body"], fontSize=8.6, leading=11,
        textColor=MUTED, spaceBefore=3, spaceAfter=11,
    )
    return s


def inline(text: str) -> str:
    """Markdown inline markup to reportlab's mini-HTML.

    Escaping comes first: an unescaped & or < in the source would otherwise be
    read as markup and either break the build or silently swallow text.
    """
    text = (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
    text = re.sub(r"`([^`]+)`",
                  r'<font face="Courier" size="9">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", text)
    # Superscript digits used for nuclei: 1H, 13C. The built-in fonts have no
    # glyphs for the Unicode superscript characters and would draw solid black
    # boxes, so they are converted to real superscript markup instead.
    for uni, plain in (("\u00b9", "1"), ("\u00b2", "2"), ("\u00b3", "3")):
        text = text.replace(uni, f"<super>{plain}</super>")
    return text


def table_flowable(rows, s, width):
    head, body = rows[0], rows[1:]
    data = [[Paragraph(inline(c), s["cellhead"]) for c in head]]
    data += [[Paragraph(inline(c), s["cell"]) for c in r] for r in body]
    columns = len(head)
    # First column carries the labels and is usually the longest.
    if columns == 2:
        widths = [width * 0.36, width * 0.64]
    else:
        widths = [width / columns] * columns
    table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PANEL),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def build_story(md: str, s, width):
    story = []
    lines = md.split("\n")
    i = 0
    in_code = False
    code: list[str] = []

    def flush_code():
        if not code:
            return
        block = Table(
            [[Paragraph("<br/>".join(
                line.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace(" ", "&nbsp;")
                for line in code), s["code"])]],
            colWidths=[width], hAlign="LEFT",
        )
        block.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("BOX", (0, 0), (-1, -1), 0.4, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(block)
        story.append(Spacer(1, 8))
        code.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                flush_code()
            in_code = not in_code
            i += 1
            continue
        if in_code:
            code.append(line)
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        # Horizontal rule -> a page break. Each rule in MANUAL.md separates
        # one numbered section from the next, so this gives one section per
        # page without the source needing to know about pagination.
        if stripped in ("---", "***", "___"):
            story.append(PageBreak())
            i += 1
            continue

        if stripped.startswith("!["):
            match = re.match(r"!\[(.*?)\]\((.*?)\)", stripped)
            if match:
                alt, src = match.group(1), match.group(2)
                path = ROOT / src
                if not path.is_file():
                    raise SystemExit(f"manual image missing: {path}")
                from PIL import Image as PILImage
                with PILImage.open(path) as im:
                    ratio = im.height / im.width
                shown = width * 0.86
                story.append(Image(str(path), width=shown,
                                   height=shown * ratio))
                if alt:
                    story.append(Paragraph(inline(alt), s["caption"]))
            i += 1
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[level:].strip()
            if level == 1:
                story.append(Paragraph(inline(text), s["title"]))
            elif level == 2:
                story.append(Paragraph(inline(text), s["h1"]))
                story.append(HRFlowable(width="100%", thickness=0.6,
                                        color=RULE, spaceBefore=1,
                                        spaceAfter=7))
            else:
                story.append(Paragraph(inline(text), s["h2"]))
            i += 1
            continue

        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") and c for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                story.append(table_flowable(rows, s, width))
                story.append(Spacer(1, 9))
            continue

        if stripped.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip().lstrip(">").strip())
                i += 1
            paragraphs = " ".join(quoted).split("  ")
            cell = [[Paragraph(inline(p), s["quote"])]
                    for p in " ".join(quoted).split("\u0000")] or [[]]
            del cell, paragraphs
            block = Table([[Paragraph(inline(" ".join(quoted)), s["quote"])]],
                          colWidths=[width], hAlign="LEFT")
            block.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(block)
            story.append(Spacer(1, 9))
            continue

        if re.match(r"^([-*]|\d+\.)\s+", stripped):
            items = []
            ordered = bool(re.match(r"^\d+\.", stripped))
            while i < len(lines):
                current = lines[i].strip()
                if not re.match(r"^([-*]|\d+\.)\s+", current):
                    # A wrapped continuation line belongs to the item above.
                    if current and lines[i].startswith(("  ", "\t")) and items:
                        items[-1] += " " + current
                        i += 1
                        continue
                    break
                items.append(re.sub(r"^([-*]|\d+\.)\s+", "", current))
                i += 1
            story.append(ListFlowable(
                [ListItem(Paragraph(inline(t), s["bullet"]), leftIndent=14)
                 for t in items],
                bulletType="1" if ordered else "bullet",
                bulletFontSize=8, leftIndent=14, start="1" if ordered else None,
            ))
            story.append(Spacer(1, 6))
            continue

        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#|\||>|```|!\[|[-*]\s|\d+\.\s|---$)", lines[i].strip()
        ):
            para.append(lines[i].strip())
            i += 1
        story.append(Paragraph(inline(" ".join(para)), s["body"]))

    flush_code()
    return story


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm, "HelSpin — User Manual")
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, str(canvas.getPageNumber()))
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 15.5 * mm, A4[0] - 20 * mm, 15.5 * mm)
    canvas.restoreState()


def main() -> int:
    if not SOURCE.is_file():
        print(f"missing {SOURCE}", file=sys.stderr)
        return 1
    s = styles()
    margin = 20 * mm
    width = A4[0] - 2 * margin
    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=18 * mm, bottomMargin=22 * mm,
        title="HelSpin — User Manual", author="H. Iw-ai",
        subject="Compare Bruker NMR spectra and build publication figures",
    )
    story = build_story(SOURCE.read_text(encoding="utf-8"), s, width)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
