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

1. **Decide the design. Aim high — a designed page, not a dumped table.**
   The baseline is a piece that looks *designed*: a clear title block, sectioned
   content with accent headers, and elements that earn the space — stat callout
   cards, a small palette-themed chart, a tinted sidebar, an icon row, or a
   two-column split. A single plain table stranded on a half-empty page is a
   FAIL, even for a "simple" ask — fill the page with structure or shrink the
   page. Match the effort to the best PDF you know you can make, every time,
   even on a thin topic (turn 3 rows into cards, add a short intro + a "key
   takeaways" strip). Then pick:
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
5. **QA the layout — you wrote the script BLIND, so verify before you claim
   done.** reportlab decides page breaks at render time; you cannot tell from
   the script whether content spilled onto a near-empty trailing page or a
   footer collides with a rule. Run the bundled checker (load_skill gave you
   this skill's folder):
   `run_command python <skill-folder>/scripts/qa_check.py <workspace>/PDFs/<slug>.pdf`
   It prints per-page fill % and flags ORPHAN / blank pages + big mid-page
   gaps. If it says **FIX NEEDED**, EDIT the build script (reduce spaceAfter,
   trim a sentence, shrink a block, or merge a short section), rebuild ONCE, and
   re-run the checker until it says OK. The classic failure this catches: ONE
   card or section spilling onto an otherwise-blank trailing page (a 90%-empty
   page 2). That is NOT done — pull it back onto page 1 (tighten spacing, fewer
   columns, or commit to a real second page of content), then re-check.
   **Then LOOK AT IT — not optional.** Render the PDF to a PNG with pypdfium2
   and `view_image` EVERY page (not just page 1 — the orphan hides on the last
   one). The fill-checker is blind to color, contrast, theme, and collisions;
   the only way to know what a page looks like is to look at the pixels.
   **Anti-hallucination rule (hard):** be as detailed and verbose as the user
   likes when you describe the result — match their preference — but only claim
   what you ACTUALLY VERIFIED by viewing it. Writing `colors.dark` in the script
   does NOT mean it rendered dark; claiming "sleek dark cyberpunk theme" about a
   render you never opened is a lie, and the user WILL open the file and see a
   plain white table. If you didn't view it, describe only the CONTENT you put
   in (sections, facts) — never the visual styling you didn't confirm. Over-
   claiming what you didn't check is the one unforgivable move here.
   NEVER present a PDF with an unresolved orphan page, a reported collision, a
   big empty gap, or a look you haven't verified with your own eyes.
6. **Open it for them.** Cross-platform recipe — append this to the
   end of your build script so it runs in one shot, no second
   `run_command`:
   ```python
   import sys, subprocess, os
   p = "out.pdf"  # your final path
   if sys.platform == "win32":     os.startfile(p)
   elif sys.platform == "darwin":  subprocess.Popen(["open", p])
   else:                           subprocess.Popen(["xdg-open", p])
   ```
7. **Delete the build script** after a successful build via
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

# 4) Tables when you have rows of data.
# HARD RULE — this is the #1 table bug (columns overlapping into a mess):
#   (a) ANY cell that can exceed ~2 words MUST be a Paragraph(text, style) so it
#       WRAPS. Raw strings do NOT wrap — long text overruns into the next column.
#   (b) colWidths MUST sum to <= the content width: A4 usable ≈ 170mm, Letter
#       ≈ 176mm (page width minus left+right margins). Wider = it runs off-page.
CELL  = ParagraphStyle("cell",  parent=BODY, fontSize=9, leading=12)
HEADC = ParagraphStyle("headc", parent=CELL, textColor=white, fontName="Helvetica-Bold")
def _row(cells, style=CELL):
    return [Paragraph(str(c), style) for c in cells]   # every cell wraps
table = Table(
    [_row(["Object", "History / usage", "Material"], HEADC)] +
    [_row(["Alarm clock", "Mechanical winding clock used since the early 1900s to wake people", "Brass"]),
     _row(["Recipe book", "Handwritten family recipes passed down across generations", "Paper"])],
    colWidths=[40*mm, 100*mm, 30*mm],   # 40+100+30 = 170mm → fits A4 exactly
)
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

## The polish layer — what separates "decent" from "wow"

A plain table + a default-blue matplotlib chart reads as auto-generated. The
four things below are what make a PDF look hand-designed. Do them by default
on any report/brief/deck-style doc — don't wait to be asked.

### 1. Charts must MATCH the design (never raw matplotlib)

Raw matplotlib (blue bars, black spines, cramped rotated labels colliding
with the bars) is the #1 "AI made this" tell. Theme every chart to the
palette and size it to fill the column. For 6+ category labels, `barh`
(horizontal) reads cleanest — flat labels, no rotation. Themed vertical bars
are fine too (in a card, well-spaced) as long as the labels don't collide
with the bars. Either way: palette colors, despined, value labels, tight bbox.

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ACCENT = "#1F3A5F"; MUTED = "#888888"
labels = ["Python","C","C++","Java","C#","JavaScript","Visual Basic","SQL","R","Rust"]
vals   = [18.96, 10.77, 8.03, 7.90, 4.85, 3.04, 2.80, 1.77, 1.69, 1.26]

fig, ax = plt.subplots(figsize=(7.0, 3.6), dpi=200)
ax.barh(labels[::-1], vals[::-1], color=ACCENT, height=0.66)   # barh = no label collisions
ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
ax.spines["left"].set_color(MUTED);  ax.spines["bottom"].set_color(MUTED)
ax.tick_params(colors="#333333", labelsize=9)
for i, v in enumerate(vals[::-1]):                              # value labels ON the bars
    ax.text(v + 0.2, i, f"{v:.1f}%", va="center", fontsize=8, color="#333333")
ax.set_xlabel("TIOBE rating (%)", fontsize=9, color=MUTED)
fig.tight_layout()
fig.savefig("chart.png", dpi=200, bbox_inches="tight")         # bbox_inches='tight' = nothing cut off
plt.close(fig)
# Embed sized to the text column (A4 minus margins ≈ 6.3"):
story.append(Image("chart.png", width=6.3*inch, height=3.24*inch))
```

Rules: theme bars to ACCENT, despine top+right, `bbox_inches="tight"`,
label values on the bars, size to the column width. If vertical bars are a
must, rotate 30° with `ha="right"` AND add bottom margin — but prefer `barh`.

### 2. Stat callout cards (instant visual hierarchy)

A row of big-number cards beats a paragraph for key figures. One `Table` row,
each cell a tinted box with a huge accent number + small caption:

```python
def stat_card(big, small):
    return Table([[Paragraph(big, H_STAT)], [Paragraph(small, CAP)]],
                 colWidths=[58*mm], rowHeights=[14*mm, 8*mm])
cards = Table([[stat_card("18.96%","Python share"), stat_card("#12","Rust rank"),
               stat_card("20+","search engines")]], colWidths=[60*mm]*3)
cards.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),HexColor("#F2F4F8")),
    ("BOX",(0,0),(-1,-1),0,white),("INNERGRID",(0,0),(-1,-1),6,white),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ALIGN",(0,0),(-1,-1),"CENTER")]))
```
(`H_STAT` = 26pt bold ACCENT; `CAP` = 8pt MUTED, centered.)

### 3. Icons / section markers

Pure-python (no Node): draw a small accent shape as a section marker via a
1-cell `Table` with a colored `BACKGROUND` + the heading beside it, or a
tiny `reportlab.graphics` Drawing (circle/check/arrow). For RICH icons when
Node is present, render `react-icons` → PNG (see the make-pptx skill's icon
pipeline) and `Image()` them at 14-18px. At minimum: a 4mm accent square
before each H2 so sections scan visually instead of being a prose wall.

### 4. Density — never ship a half-empty page

If a chart leaves 50% of a page blank, you laid it out wrong. Fill it: put
the callout cards next to the chart (two-column `Table`), or move the chart
up beside the table. Balance content across pages. A page that's 30% ink and
70% white looks unfinished. Aim for even, magazine-like density.

**No trailing orphan page.** The classic failure: content spills a few lines
onto a final page that's then ~85% empty. Before finishing, estimate your
page count and TIGHTEN to land cleanly — trim a sentence, reduce `spaceAfter`,
shrink the chart slightly, or merge a short section — so the doc ends on a
FULL page. A 3-page report that bleeds two lines onto page 4 should be 3
pages, not 4. Never ship a final page under ~40% full.

**EXCEPTION — honor an explicit size request.** If the user asked for
something deliberately small ("a quarter-page note", "a tiny one-pager", "just
a short card", "make it minimal"), that whitespace IS the design. Do NOT pad
it to fill, and IGNORE the qa_check gap/orphan flag — it's advisory and
expects a full page by default. Better yet, build on a smaller canvas: set the
`pagesize` to the requested size (e.g. `(A4[0], A4[1]/4)` for a quarter page,
or a custom `(w*mm, h*mm)`) so the "page" is the small thing the user wanted
and it fills naturally. User intent always beats the fill heuristic.

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
  AI-tell footer. Page numbers are fine; self-references are not.
- Don't auto-insert today's date — no "Report Date: <month>", no "as of
  <date>", no datetime.now() in the doc — UNLESS the user asked for a date.
  An unrequested date is an AI-tell and is often wrong for the topic.
- Don't render the same title TWICE (cover-title + first H1 = same text).
- Don't use `#` in pptxgenjs hex strings (different lib — but reportlab
  HexColor() needs the `#`, so reportlab IS fine).
- Don't accent-stripe card edges. Use background tints or shadows.
- Don't underline headings — whitespace separates sections.
- Don't drop a raw default-matplotlib chart (blue bars, black box-spines,
  rotated labels overlapping the bars). Theme it to the palette + use `barh`
  (see the polish layer above). An off-theme chart wrecks the whole doc.
- Don't leave a page more than ~50% empty. Fill it or rebalance — a chart
  floating in white space looks unfinished.

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
