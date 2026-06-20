---
name: make-pptx
description: Build a styled PowerPoint deck. Prefer the bundled pptxgenjs renderer (designed cards, real timelines, stat callouts, auto-opens); fall back to python-pptx only when Node is missing. Use when the user wants slides / a deck / a pitch / a presentation. NOT for reading existing decks.
version: 3.0.0
---

# Make a PPTX — designed by default, never a bullet wall

A plain title-plus-bullets slide with an empty bottom half is the #1 tell of an
auto-generated deck. Don't ship it. Two routes, in order of preference:

## Route A (PREFER): the pptxgenjs renderer — `scripts/build_deck.js`

Node is required (check: `run_command node --version`). You write a **content
JSON spec**; the renderer lays it out as a designed deck — dark cover/closing,
light content, shadowed cards, real timeline rails, stat callouts, accent
badges. It is structurally impossible to emit a bare bullet wall.

```
run_command node <skill-folder>/scripts/build_deck.js <workspace>/.build/<slug>.json <workspace>/PPTX/<slug>.pptx
```

`load_skill` gave you `<skill-folder>`. Spec shape (only include the slides you need):

```json
{
  "title": "Deck Title",
  "subtitle": "one-line subtitle",
  "theme": "forest",
  "slides": [
    { "type": "cover",   "eyebrow": "category", "title": "...", "subtitle": "..." },
    { "type": "timeline","heading": "1972-1992", "subhead": "...",
      "events": [ {"date":"1972","title":"Stockholm","text":"one tight line"} ] },
    { "type": "cards",   "heading": "...", "subhead": "...",
      "cards": [ {"badge":"UNEP","title":"...","text":"2-3 lines"} ] },
    { "type": "stats",   "heading": "...",
      "stats": [ {"value":"196","label":"parties to Paris"} ] },
    { "type": "split",   "heading": "...", "lead": "the big idea",
      "points": ["...","...","..."] },
    { "type": "closing", "title": "...", "subtitle": "..." }
  ]
}
```

- **theme** — pick one to FIT the topic so decks don't all look the same:
  `forest` (nature/climate/health), `tech` (startup/AI/product),
  `corporate` (finance/business), `gaming` (consumer/entertainment),
  `academic` (research/sober). The theme drives the whole palette.
- **timeline** — up to 5 events per slide; alternating cards on a real rail with
  date nodes. THE go-to for any "history / timeline / how X evolved" ask. Split a
  long history across two timeline slides (e.g. foundations, then modern era).
- **cards** — up to 6; 2 or 3 columns chosen automatically; each gets an accent
  badge (short acronym/number) + title + body. For "the players / pillars / parts".
- **stats** — up to 4 big-number callouts. For impact / "why it matters".
- **split** — a dark lead panel + supporting points. For a thesis or summary.
- Mix slide types. A 6-slide deck that is cover + 2 timelines + cards + stats +
  closing reads as designed; six identical bullet slides reads as a robot.

Keep each text field tight — the renderer sizes regions; overlong strings wrap
ugly. One idea per card.

## Route B (fallback): python-pptx — only if Node is unavailable

`python-pptx` is always installed. Write a fresh build script to
`<workspace>/.build/<slug>_build.py` and run it. Your FIRST slide design is a
**card-based** layout (shadowed rounded rects + accent badges), NOT bullets.
Cheat sheet:

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn

prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
def slide(bg):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    r.fill.solid(); r.fill.fore_color.rgb = bg; r.line.fill.background(); return s
def shadow(shape, blur=90000, dist=38000, alpha=62000):  # python-pptx has no shadow API
    sp = shape._element.spPr; eff = sp.makeelement(qn('a:effectLst'), {})
    sh = sp.makeelement(qn('a:outerShdw'), {'blurRad':str(blur),'dist':str(dist),'dir':'5400000','rotWithShape':'0'})
    c = sp.makeelement(qn('a:srgbClr'), {'val':'000000'}); a = sp.makeelement(qn('a:alpha'), {'val':str(alpha)})
    c.append(a); sh.append(c); eff.append(sh); sp.append(eff)
def card(s, x, y, w, h, fill=RGBColor(0xFF,0xFF,0xFF)):
    c = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    c.fill.solid(); c.fill.fore_color.rgb = fill; c.line.fill.background(); c.shadow.inherit = False
    shadow(c); return c
```

Charts: render with **matplotlib to a transparent PNG** and `add_picture` —
never python-pptx `add_chart` (its legend + fonts are uncontrollable). Drive a
themed line/bar at `dpi=200`, `fig.patch.set_alpha(0)`, control the legend by hand.

Append a cross-platform open snippet so the deck opens in one shot:
```python
import sys, os, subprocess
if sys.platform=="win32": os.startfile(p)
elif sys.platform=="darwin": subprocess.Popen(["open",p])
else: subprocess.Popen(["xdg-open",p])
```

## QA — verify before you claim anything

You placed shapes blind. Run the checker on the REAL output path:
`run_command python <skill-folder>/scripts/qa_check_pptx.py <workspace>/PPTX/<slug>.pptx`
It flags off-slide shapes, collisions, leftover placeholder text, tiny fonts,
empty slides. If it says FIX NEEDED, edit the spec/script, rebuild ONCE, re-run.
If vision-capable, also render to images (`Spire.Presentation`) and look.

## Open it for them — only AFTER it built + passed QA

The renderer does NOT open the file; YOU open it with a visible command so the
user sees it happen and can gate it:
`run_command start "" "<workspace>/PPTX/<slug>.pptx"` (Windows),
`open <path>` (macOS), `xdg-open <path>` (Linux). Never open a file you haven't
confirmed exists.

## Hard rules — honesty (this matters most)

- **NEVER say a deck is "created" / "saved" / "done" until the build command
  ACTUALLY RAN this turn and printed the output path.** If a `run_command` is
  awaiting the user's approval, nothing has happened yet — wait for it. Do not
  narrate a fake "Done." A claimed file the user can't find destroys trust.
- **Verify the file exists at the path you report** (the build script printing
  the path is your proof; if it didn't print, it didn't save).
- **Do NOT delete the build script / JSON spec until after a successful build +
  QA.** Deleting it first means a re-run 404s.

## Hard rules — design

- 16:9 only (13.333 x 7.5). Each slide: ONE idea.
- No edge accent stripes, no "Slide 1/6" footers, no empty bottom halves.
- Cover + closing on dark; content on light. One repeated motif.
- Don't reuse one theme for every deck — match it to the subject.
