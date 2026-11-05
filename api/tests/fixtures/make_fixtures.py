"""Generate the ingestion pipeline's test PDF fixtures.

Produces two PDFs under `api/tests/fixtures/`:

- `digital_sample.pdf`: a born-digital, multi-page PDF with a section
  heading structure and a simple table, used to prove correct page-number
  provenance, section_path extraction, and table-aware chunking (ING-01,
  ING-02) with NO OCR involved (real extractable text layer).
- `scanned_sample.pdf`: a single page containing ONLY a rasterized image
  (no text layer at all) with known words baked into the image via PIL, used
  to prove Docling's automatic per-region OCR triggers (do_ocr=True) with no
  manual intervention (ING-01 edge: OCR-trigger).

Run directly: `python tests/fixtures/make_fixtures.py` (from `api/`). Uses
reportlab + pillow (dev-only fixture-generation dependencies, see
pyproject.toml `[project.optional-dependencies].dev`) - never imported by
production code.
"""

import io
from pathlib import Path

import reportlab
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FIXTURES_DIR = Path(__file__).resolve().parent

# reportlab bundles the Vera TrueType font family inside its own installed
# package - reusing it here means the scanned-page fixture needs no extra
# font dependency beyond what's already baked into the ML image.
_VERA_BOLD_PATH = str(Path(reportlab.__path__[0]) / "fonts" / "VeraBd.ttf")

# Known words baked into the scanned page's raster image. The OCR
# integration test asserts these (or a tolerant subset,
# case-insensitively) appear in the recognized text, proving automatic OCR
# actually ran rather than the test silently passing on an empty/garbage
# result.
SCANNED_PAGE_KEYWORDS = ["forest", "ecosystem", "carbon", "cycle"]


def _build_digital_sample(path: Path) -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=LETTER)
    body_text = (
        "Photosynthesis is the process by which green plants, algae, and some "
        "bacteria convert light energy, usually from the sun, into chemical "
        "energy stored in glucose. This process takes place primarily in the "
        "chloroplasts of plant cells, specifically using the green pigment "
        "chlorophyll to capture light energy. The overall reaction consumes "
        "carbon dioxide and water, releasing oxygen as a byproduct, and is "
        "foundational to nearly all life on Earth because it forms the base "
        "of most food chains and replenishes atmospheric oxygen. "
    ) * 6

    methods_text = (
        "Researchers measured oxygen output from aquatic plant samples under "
        "varying light intensities using a simple gas-collection apparatus. "
        "Each trial recorded the volume of oxygen bubbles produced over a "
        "fixed ten-minute interval, with light intensity controlled by "
        "adjusting the distance between the light source and the sample. "
    ) * 4

    results_text = (
        "Results confirmed the expected positive relationship between light "
        "intensity and oxygen output up to a saturation point, beyond which "
        "additional light produced no further increase, consistent with "
        "chlorophyll's finite capacity to absorb photons at high irradiance. "
    ) * 4

    story = [
        Paragraph("Chapter 1: Introduction to Photosynthesis", styles["Title"]),
        Spacer(1, 0.2 * inch),
        Paragraph(body_text, styles["BodyText"]),
        PageBreak(),
        Paragraph("Methods", styles["Heading1"]),
        Spacer(1, 0.1 * inch),
        Paragraph(methods_text, styles["BodyText"]),
        Spacer(1, 0.2 * inch),
        Table(
            [
                ["Trial", "Light Intensity (lux)", "Oxygen Output (mL)"],
                ["1", "500", "2.1"],
                ["2", "1000", "4.3"],
                ["3", "1500", "6.0"],
                ["4", "2000", "6.1"],
            ],
            style=TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ]
            ),
        ),
        PageBreak(),
        Paragraph("Results", styles["Heading1"]),
        Spacer(1, 0.1 * inch),
        Paragraph(results_text, styles["BodyText"]),
    ]
    doc.build(story)


def _build_scanned_sample(path: Path) -> None:
    # Render known keywords onto a raster image at high resolution so
    # RapidOCR has a reasonable chance of recognizing them cleanly, then
    # place that image as the ENTIRE content of a single PDF page - no
    # reportlab text objects at all, so the page has no extractable text
    # layer and Docling must OCR it to get any text out.
    #
    # Deliberately regular-weight, paragraph-shaped prose (not a large/bold
    # title line): Docling's layout model classifies isolated large/bold
    # single lines as a `section_header`, and HybridChunker's structural
    # pass drops a bare heading with no following body text - producing
    # zero chunks even though OCR succeeded. Ordinary paragraph styling
    # gets classified as body text, which HybridChunker always emits.
    width_px, height_px = 1700, 2200
    image = Image.new("RGB", (width_px, height_px), color="white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(_VERA_BOLD_PATH, size=52)

    lines = [
        "This scanned page is a test of automatic OCR.",
        "It describes a typical forest ecosystem and",
        "explains how the carbon cycle sustains it over time.",
    ]
    y = 300
    for line in lines:
        draw.text((150, y), line, fill="black", font=font)
        y += 120

    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    image_reader = ImageReader(image_buffer)

    c = canvas.Canvas(str(path), pagesize=LETTER)
    page_width, page_height = LETTER
    c.drawImage(
        image_reader,
        0,
        0,
        width=page_width,
        height=page_height,
        preserveAspectRatio=False,
    )
    c.showPage()
    c.save()


def main() -> None:
    _build_digital_sample(FIXTURES_DIR / "digital_sample.pdf")
    _build_scanned_sample(FIXTURES_DIR / "scanned_sample.pdf")
    print("Wrote digital_sample.pdf and scanned_sample.pdf")


if __name__ == "__main__":
    main()
