---
name: make-pptx
description: Build a styled PowerPoint deck from scratch using python-pptx. Write a fresh build script each time so the deck design fits the topic. Use when the user wants slides / a deck / a pitch / a presentation. NOT for reading existing decks.
version: 2.0.0
---

# Make a PPTX — write a real build script every time

There is NO template here. You author a fresh Python script per request
using `python-pptx` (already installed). The bundled
`scripts/build_pptx.py` exists as a fallback wrapper for simple cases,
but **for any deck where the user cares about look + feel, write your
own script.** Full layout control, color control, fonts, shapes,
backgrounds, images, charts.

## The pipeline (every time)

1. **Decide the design.** Color palette (one dominant + one accent +
   one supporting), font, slide background (dark cover slide + light
   content slides = the sandwich), motif (rounded cards, icon circles,
   etc.). One motif per deck, repeated.
2. **Plan the slides.** Cover → 3-6 content slides → closing/CTA.
3. **Write the build script** to `<workspace>/.build/<slug>_build.py`
   using `pptx`. The `.build` subfolder is scratch — temporary files
   only. The build script should write its OUTPUT (.pptx) to
   `<workspace>/PPTX/<slug>.pptx` so the user's deliverables stay
   organized.
4. **Run** `python <workspace>/.build/<slug>_build.py`. It saves the
   .pptx and prints the path.
5. **Open it for them.** Append a cross-platform open snippet to the
   bottom of your build script so it runs in one shot:
   ```python
   import sys, subprocess, os
   p = "out.pptx"  # your final path
   if sys.platform == "win32":     os.startfile(p)
   elif sys.platform == "darwin":  subprocess.Popen(["open", p])
   else:                           subprocess.Popen(["xdg-open", p])
   ```
6. **Delete the build script** via `delete_path` on
   `<workspace>/.build/<slug>_build.py` after a successful build.

## python-pptx cheat sheet

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

# 1) Make a 16:9 deck
prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)

# 2) Palette
NAVY     = RGBColor(0x1A, 0x1F, 0x3A)
CRIMSON  = RGBColor(0xDC, 0x14, 0x3C)
CREAM    = RGBColor(0xFD, 0xF6, 0xE3)
WHITE    = RGBColor(0xFF, 0xFF, 0xFF)
GREY     = RGBColor(0x88, 0x88, 0x88)

# 3) Helper — blank slide with full-bleed bg fill
def new_slide(bg=WHITE):
    blank = prs.slide_layouts[6]  # 6 = blank in default master
    s = prs.slides.add_slide(blank)
    bg_shape = s.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height,
    )
    bg_shape.fill.solid(); bg_shape.fill.fore_color.rgb = bg
    bg_shape.line.fill.background()
    return s

# 4) Helper — text box with full style control
def textbox(slide, left, top, width, height, text, *,
            font="Helvetica", size=24, bold=False, color=NAVY,
            align=PP_ALIGN.LEFT):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return tb

# 5) Cover slide — dark
cover = new_slide(bg=NAVY)
textbox(cover, Inches(0.8), Inches(2.5), Inches(11.5), Inches(2),
        "Sprint", font="Helvetica", size=80, bold=True, color=WHITE)
textbox(cover, Inches(0.8), Inches(4.5), Inches(11.5), Inches(1),
        "The fitness app that actually sticks.",
        font="Helvetica", size=22, color=CREAM)

# 6) Content slide — light, with bullets
def content_slide(title, bullets):
    s = new_slide(bg=WHITE)
    textbox(s, Inches(0.6), Inches(0.4), Inches(12), Inches(0.9),
            title, font="Helvetica", size=32, bold=True, color=NAVY)
    body = s.shapes.add_textbox(Inches(0.6), Inches(1.5),
                                  Inches(12), Inches(5)).text_frame
    body.word_wrap = True
    for i, b in enumerate(bullets):
        p = body.paragraphs[0] if i == 0 else body.add_paragraph()
        r = p.add_run()
        r.text = f"•  {b}"
        r.font.name = "Helvetica"; r.font.size = Pt(20); r.font.color.rgb = NAVY
    return s

content_slide("The Problem", [
    "80% of people quit fitness goals within 3 months",
    "Generic apps feel like homework, not motivation",
    "No real accountability or social spark",
])

# 7) Save
prs.save("out.pptx")
print("out.pptx")
```

## Shape recipes

```python
# Card with shadow
from pptx.dml.color import RGBColor
card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                           Inches(0.5), Inches(2), Inches(4), Inches(2))
card.fill.solid(); card.fill.fore_color.rgb = WHITE
card.line.color.rgb = RGBColor(0xE0, 0xE0, 0xE0)
card.shadow.inherit = False  # avoid template shadow

# Pull stat
textbox(s, Inches(0.5), Inches(2.2), Inches(4), Inches(1),
        "$11.6B", size=56, bold=True, color=CRIMSON, align=PP_ALIGN.CENTER)
textbox(s, Inches(0.5), Inches(3.4), Inches(4), Inches(0.6),
        "Indie game market 2024", size=14, color=GREY, align=PP_ALIGN.CENTER)
```

## Color palettes (riff — don't reuse for every deck)

- **Tech / startup**: NAVY + CRIMSON + CREAM (modern, bold)
- **Corporate / finance**: NAVY + GOLD + WHITE (classic, trustworthy)
- **Lifestyle / wellness**: SOFT GREEN + CREAM + WHITE (calm)
- **Gaming / consumer**: BLACK + NEON + WHITE (high-contrast, energetic)
- **Academic / research**: WHITE + BLACK + ONE accent (sober)

## Hard rules — DON'T

- Don't use `prs.slide_layouts[0]` (Title slide) — it forces template
  placeholders and font defaults you don't control. Use `[6]` (blank).
- Don't leave default white background unless that's the design — paint
  the full-bleed bg rectangle explicitly.
- Don't add "Slide 1 / 6" footers unless asked.
- Don't try to layout with whitespace — use exact `Inches()` coordinates.
- Don't add accent stripes along slide edges (AI-tell).
- Don't write empty filler text. If a slide doesn't have content, cut it.

## Hard rules — DO

- 16:9 (13.333" x 7.5"). 4:3 is dead.
- One repeated visual motif across the deck.
- Cover + closing slides on dark bg, content slides on light.
- Each slide: ONE main idea. If a slide has 7 bullets, split into 2.
- Font sizes: 60-80 for cover title, 28-36 for content headings, 18-22
  for body bullets.

## Fallback: the bundled wrapper (trivial decks only)

`scripts/build_pptx.py` takes `--outline`, `--out`, `--title`, optional
`--style-file <json>`. Outlines are slides separated by `---`. **Use it
only when the user asks for a deck with no design specifics.** For
anything stylistic — write a fresh build script with the cheat sheet
above.


## The one upgrade that would 10x the PPTX skill 

If Node.js is available, PREFER pptxgenjs over python-pptx.
pptxgenjs enables: shadows, icons via react-icons, 
native charts, and proper image sizing modes.
python-pptx is the fallback only.

