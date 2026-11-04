"""Writing a matplotlib figure to disk or the clipboard, shared by every canvas.

The main spectrum canvas and the CEST plots offer the same "Save image"
formats and "Copy image" and must produce the same kind of output: one
implementation, so a fix in one place (a format, editable text in vector
output) reaches both.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

# (file-dialog filter, extension). EMF is deliberately absent: matplotlib
# cannot write it, and the only route (an external Inkscape) silently
# produced nothing when Inkscape was missing.
EXPORT_FILTERS = [
    ("PNG image (*.png)", "png"),
    ("JPEG image (*.jpg)", "jpg"),
    ("TIFF image (*.tif)", "tif"),
    ("SVG vector (*.svg)", "svg"),
    ("PDF vector (*.pdf)", "pdf"),
    ("EPS vector (*.eps)", "eps"),
    ("PostScript (*.ps)", "ps"),
    ("PowerPoint slide (*.pptx)", "pptx"),
]

EXPORT_SUFFIXES = {ext for _label, ext in EXPORT_FILTERS} | {"jpeg", "tiff"}


def suffix_for_filter(chosen: str, default: str = "png") -> str:
    """Extension belonging to a file-dialog filter string."""
    return next((ext for label, ext in EXPORT_FILTERS if label == chosen), default)


def save_figure(figure, path, *, transparent: bool = False, dpi: int = 300) -> None:
    """Write `figure` exactly as displayed; the format follows the suffix.

    No tight bounding box and no layout change: those re-crop and re-scale,
    so the file would not match the canvas. Vector formats keep text as
    editable text rather than outlines.
    """
    import matplotlib as mpl

    path = str(path)
    # Path().suffix, never a split on ".": a dot in a DIRECTORY name would
    # otherwise be read as the format.
    suffix = Path(path).suffix.lstrip(".").lower() or "png"
    if suffix == "pptx":
        _save_pptx(figure, path, dpi=dpi)
        return
    overrides = {"svg.fonttype": "none", "pdf.fonttype": 42, "ps.fonttype": 42}
    with mpl.rc_context(overrides):
        figure.savefig(
            path, format=suffix, dpi=dpi,
            facecolor="none" if transparent else "white",
            edgecolor="none", transparent=transparent,
        )


def _save_pptx(figure, path: str, dpi: int = 300) -> None:
    """One slide sized to the figure, holding a full-resolution PNG of it."""
    try:
        from pptx import Presentation
        from pptx.util import Inches
    except ImportError as exc:   # pragma: no cover - dependency present
        raise RuntimeError("PowerPoint export needs the python-pptx package") from exc

    width_in, height_in = figure.get_size_inches()
    with tempfile.TemporaryDirectory() as tmp:
        png = f"{tmp}/slide.png"
        figure.savefig(png, format="png", dpi=dpi, facecolor="white",
                       edgecolor="none")
        prs = Presentation()
        prs.slide_width = Inches(float(width_in))
        prs.slide_height = Inches(float(height_in))
        slide = prs.slides.add_slide(prs.slide_layouts[6])   # blank layout
        slide.shapes.add_picture(png, 0, 0, width=Inches(float(width_in)),
                                 height=Inches(float(height_in)))
        prs.save(path)


def figure_png_bytes(figure, *, dpi: int = 300) -> bytes:
    """The figure as PNG bytes: white background, on-screen size and limits.

    The same rendering as a saved PNG -- no tight bounding box, no layout
    change -- so a pasted image matches both the canvas and a saved file.
    """
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, facecolor="white",
                   edgecolor="none")
    return buffer.getvalue()


def copy_figure_to_clipboard(figure, *, dpi: int = 300, clipboard=None):
    """Put the figure on the system clipboard as an image; return the QImage.

    For pasting straight into PowerPoint, Keynote, Word, Illustrator, a mail
    or a chat. Copied as a bitmap through QClipboard.setImage, which Qt
    offers in each platform's native image formats (TIFF/PNG on macOS,
    DIB on Windows) -- what other applications actually accept on paste.
    Opaque white rather than transparent: Windows' DIB clipboard formats have
    no reliable alpha, so a transparent background pastes as black there.

    The resolution matches Save image (300 dpi by default) and is recorded
    in the image, so applications that honour it paste the figure at its
    on-screen physical size.

    Raises RuntimeError if the image cannot be produced or no clipboard is
    available, so callers can report it rather than fail silently.
    """
    from PySide6.QtGui import QGuiApplication, QImage

    image = QImage.fromData(figure_png_bytes(figure, dpi=dpi), "PNG")
    if image.isNull():
        raise RuntimeError("the figure could not be rendered as an image")
    # Set explicitly rather than trusting the PNG's pHYs chunk to be read.
    dots_per_metre = round(float(dpi) / 0.0254)
    image.setDotsPerMeterX(dots_per_metre)
    image.setDotsPerMeterY(dots_per_metre)
    if clipboard is None:
        clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        raise RuntimeError("no clipboard is available")
    clipboard.setImage(image)
    return image
