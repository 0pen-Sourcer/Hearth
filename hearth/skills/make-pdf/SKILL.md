---
name: make-pdf
description: Build a styled PDF from scratch using reportlab Platypus. Use when the user wants a PDF — brief, report, resume, writeup, doc, recipe, anything. You write the build script fresh every time so the design fits the request. NOT for reading existing PDFs (that's read_file).
version: 2.0.0
---

# Make a PDF — write a real build script every time

There is NO template here. You author a fresh Python script per request,
using `reportlab` (already installed). The bundled `scripts/build_pdf.py`
exists as a fallback wrapper for simple cases, but **for anything where
the user cares about look + feel, write your own script.** That's the
whole point — full design control, no "every PDF looks the same".

## The pipeline (every time)

1. **Decide the design.** Read the user's request. Pick:
   - background color (white? black? cream? navy?)
   - heading color (accent)
   - body text color (must contrast the background)
   - font family (Helvetica, Times-Roman, Courier — these are the safe
     built-ins; for custom TTFs use `pdfmetrics.registerFont`)
   - page size (A4 default, LETTER for US resumes)
   - margins, spacing, motif (rule, no rule, tinted callouts, tables)
2. **Write the source content** as plain Python strings or a list of
   sections — don't write markdown first and parse it, just put the
   text directly into the build script as Paragraph args.
3. **Write the build script** to `<workspace>/.build/<slug>_build.py`
   using `reportlab.platypus` (Platypus = the auto-pagination engine).
   The `.build` subfolder is treated as scratch — files here are
   considered temporary and may be cleaned up later. The build script
   should write its OUTPUT (the .pdf) to `<workspace>/PDFs/<slug>.pdf`
   so the user has a clean organized folder of just the deliverables.
4. **Run it** with `run_command python <workspace>/.build/<slug>_build.py`.
   It prints the output PDF path on success.
5. **Open it for them.** Cross-platform recipe — append this to the
   end of your build script so it runs in one shot, no second
   `run_command`:
   ```python
   import sys, subprocess, os
   p = "out.pdf"  # your final path
   if sys.platform == "win32":     os.startfile(p)
   elif sys.platform == "darwin":  subprocess.Popen(["open", p])
   else:                           subprocess.Popen(["xdg-open", p])
   ```
6. **Delete the build script** after a successful build via
   `delete_path` on `<workspace>/.build/<slug>_build.py` — it's
   served its purpose, leaving it clutters the workspace.

## reportlab Platypus cheat sheet

```python
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm, inch
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, Image, KeepTogether,
)

# 1) Define style palette ONCE — reuse across all paragraphs
BG       = HexColor("#000000")   # background (set in onPage callback)
ACCENT   = HexColor("#DC143C")   # crimson heading
BODY_C   = HexColor("#E8E8E8")   # light grey body for dark bg
MUTED    = HexColor("#888888")

H1 = ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=24,
                    leading=30, textColor=ACCENT, spaceAfter=12)
H2 = ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=15,
                    leading=20, textColor=ACCENT, spaceBefore=14, spaceAfter=6)
BODY = ParagraphStyle("BODY", fontName="Helvetica", fontSize=11,
                      leading=16, textColor=BODY_C, spaceAfter=8)
BULLET = ParagraphStyle("BUL", parent=BODY, leftIndent=18,
                        bulletIndent=6, spaceAfter=4)

# 2) Background painter — runs on every page
def _paint_bg(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BG)
    w, h = doc.pagesize
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    # footer
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(w/2.0, 0.4*inch, str(canvas.getPageNumber()))
    canvas.restoreState()

# 3) Story list — order = render order
story = [
    Paragraph("My Title", H1),
    Paragraph("Subtitle line in body color", BODY),
    Spacer(1, 8*mm),
    Paragraph("Section heading", H2),
    Paragraph("Body paragraph with <b>bold</b> and <i>italic</i> "
              "and <font color='#DC143C'>inline color</font>.", BODY),
    Paragraph("First bullet", BULLET, bulletText="•"),
    Paragraph("Second bullet", BULLET, bulletText="•"),
]

# 4) Tables when you have rows of data
table = Table([
    ["Name", "Role", "Years"],
    ["Ada",  "Eng",  "5"],
    ["Linus","Eng",  "34"],
], colWidths=[60*mm, 60*mm, 30*mm])
table.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), ACCENT),
    ("TEXTCOLOR",  (0,0), (-1,0), white),
    ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [HexColor("#1a1a1a"), HexColor("#222")]),
    ("TEXTCOLOR",  (0,1), (-1,-1), BODY_C),
    ("GRID", (0,0), (-1,-1), 0.4, MUTED),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
]))
story.append(table)

# 5) Build
doc = SimpleDocTemplate(
    "out.pdf", pagesize=A4,
    leftMargin=18*mm, rightMargin=18*mm,
    topMargin=18*mm, bottomMargin=18*mm,
    title="My Doc",
)
doc.build(story, onFirstPage=_paint_bg, onLaterPages=_paint_bg)
print("out.pdf")
```

## Color recipes (riff on these — don't reuse one for everything)

- **Dark + crimson** (chaos, gaming, lifestyle): `BG=#000000  ACCENT=#DC143C  BODY=#E8E8E8`
- **Navy + serif** (resume, exec brief): `BG=#FFFFFF  ACCENT=#1F3A5F  BODY=#1A202C  font=Times-Roman`
- **Cream + brown** (recipe, cozy): `BG=#FDF6E3  ACCENT=#8A4F00  BODY=#3D2400  font=Times-Roman`
- **Pure white + sober black** (legal, academic): `BG=#FFFFFF  ACCENT=#000000  BODY=#222`
- **Pitch orange** (marketing one-pager): `BG=#FFFFFF  ACCENT=#C4451C  BODY=#1A1A1A`

The model picks the recipe. Don't ask the user "what color do you want?"
unless they were vague. If they said "navy + Times-Roman" — that's
unambiguous, use it.

## Hard rules — DON'T

- Don't write a markdown file first and then parse it into Platypus.
  Just put the text strings DIRECTLY into the Python build script.
- Don't add "Generated by Hearth" / "Research compiled <date>" / any
  AI-tell footer. Page numbers + date are fine; self-references are not.
- Don't render the same title TWICE (cover-title + first H1 = same text).
- Don't use `#` in pptxgenjs hex strings (different lib — but reportlab
  HexColor() needs the `#`, so reportlab IS fine).
- Don't accent-stripe card edges. Use background tints or shadows.
- Don't underline headings — whitespace separates sections.

## Hard rules — DO

- Define ParagraphStyle objects ONCE at the top, reuse everywhere.
- For background colors, paint via canvas in `onFirstPage` /
  `onLaterPages` — `SimpleDocTemplate` doesn't take a `bg` arg directly.
- For multi-column layouts, use `Table` with column widths (don't try to
  fake columns with spaces).
- For images: `Image("path.png", width=4*inch, height=3*inch)`.
- Test the contrast — black body on dark navy is unreadable. Always
  check the body color against the background color in your head.

## Fallback: the bundled wrapper (for trivial cases ONLY)

`scripts/build_pdf.py` exists as a minimal wrapper if the user asks for
"any PDF, default style is fine". It takes `--md`, `--out`, `--title`,
optional `--style-file <json>`. **Use it only when the user is
indifferent to design.** For anything where they specified colors,
fonts, layout, or mood — write a fresh build script instead. The
wrapper can't render arbitrary designs.
