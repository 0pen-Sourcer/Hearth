---
name: make-diagram
description: Build a diagram — flowchart, architecture/system diagram, sequence, mind map, org chart — as a self-contained HTML/SVG that opens in any browser, OR as an editable Excalidraw file. Use when the user wants a diagram, chart of boxes-and-arrows, system design picture, or flow. NOT for data charts (that's a chart inside make-pdf) or for reading an existing diagram.
version: 1.0.0
---

# Make a diagram — pick the right backend, write a real file

No templates. You author the diagram fresh each time. Two output backends —
pick based on what the user needs:

| Want | Backend | Output |
|---|---|---|
| A polished diagram to view/share/screenshot | **inline-SVG in a self-contained .html** | opens in any browser, no internet |
| A diagram the user will keep EDITING by hand | **Excalidraw JSON** (`.excalidraw`) | they drag it onto excalidraw.com |

Default to the **SVG/HTML** backend unless the user says they want to edit it
later, then use Excalidraw.

## The pipeline (every time)

1. **Plan the graph.** Nodes (boxes) + edges (arrows) + layout (top-down,
   left-right, layered). Pick a palette (one bg, one accent, readable text).
2. **Write the build script** to `<workspace>/.build/<slug>_build.py` that
   emits the file to `<workspace>/diagrams/<slug>.html` (or `.excalidraw`).
   Compute every x/y/width by hand — there's no auto-layout, you place boxes
   on a grid.
3. **Run it** with `run_command python <workspace>/.build/<slug>_build.py`.
4. **Open it** — append the cross-platform open snippet so it opens in one shot:
   ```python
   import sys, subprocess, os
   p = "out.html"
   if sys.platform == "win32":   os.startfile(p)
   elif sys.platform=="darwin":  subprocess.Popen(["open", p])
   else:                         subprocess.Popen(["xdg-open", p])
   ```
5. **Delete the build script** via `delete_path` after a clean run.

## Backend A — self-contained SVG in HTML (the default)

Build the SVG as a Python string and wrap it in a minimal HTML doc. Boxes are
`<rect rx=...>` with a `<text>` centered inside; arrows are `<line>`/`<path>`
with an arrowhead `<marker>`. One dark theme that reads well:

```python
W, H = 1000, 640
ACCENT = "#7c5cff"; BG = "#14121c"; CARD = "#211d33"; FG = "#e9e0ff"; MUT = "#9b8cc7"

def box(x, y, w, h, label, fill=CARD):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
            f'fill="{fill}" stroke="{ACCENT}" stroke-width="1.5"/>'
            f'<text x="{x+w/2}" y="{y+h/2+5}" fill="{FG}" font-family="Segoe UI,Arial" '
            f'font-size="15" font-weight="600" text-anchor="middle">{label}</text>')

def arrow(x1, y1, x2, y2):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{MUT}" stroke-width="2" marker-end="url(#a)"/>'

svg = f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
    <path d="M0,0 L8,3 L0,6 Z" fill="{MUT}"/></marker></defs>
  <rect width="{W}" height="{H}" fill="{BG}"/>
  {box(400, 40, 200, 60, "User request")}
  {arrow(500, 100, 500, 160)}
  {box(400, 160, 200, 60, "Decision engine")}
  <!-- ...lay out the rest on a grid... -->
</svg>'''

html = f'<!doctype html><meta charset="utf-8"><title>{{title}}</title>' \
       f'<body style="margin:0;background:{BG};display:grid;place-items:center;min-height:100vh">{svg}</body>'
open("out.html", "w", encoding="utf-8").write(html)
print("out.html")
```

Layout rules: align boxes on a grid (consistent x columns, even y rows);
route arrows from box edges (not centers) so they don't cross the text;
give 40-60px gaps between rows. Recolor a few key nodes in ACCENT to show flow.

## Backend B — Excalidraw (when they'll edit it)

Emit an `.excalidraw` JSON file. Each element needs a unique `id`, `type`
(`rectangle`/`ellipse`/`diamond`/`arrow`/`text`), `x`/`y`/`width`/`height`,
and style fields. Bind arrows to shapes via `startBinding`/`endBinding` with
the shape ids so they stay attached when the user drags boxes. Minimal shape:

```python
import json, time, random
def el(t, x, y, w, h, **kw):
    return {"id": f"{t}{random.randint(1,1<<30)}", "type": t, "x": x, "y": y,
            "width": w, "height": h, "angle": 0, "strokeColor": "#1e1e1e",
            "backgroundColor": "#a5d8ff", "fillStyle": "solid", "strokeWidth": 2,
            "roughness": 1, "opacity": 100, "seed": random.randint(1,1<<30),
            "version": 1, "versionNonce": random.randint(1,1<<30),
            "isDeleted": False, "boundElements": [], "groupIds": [], **kw}
doc = {"type": "excalidraw", "version": 2, "source": "hearth", "elements": [...], "appState": {"viewBackgroundColor": "#ffffff"}}
open("out.excalidraw", "w", encoding="utf-8").write(json.dumps(doc, indent=2))
```

Tell the user: "open excalidraw.com → menu → Open → pick this file" to edit.

## Hard rules

- Self-contained: NO external JS/CSS/CDN in the HTML — it must open offline.
- Place by coordinate; never try to lay out boxes with whitespace/flow.
- Route arrows edge-to-edge, never through a box's label.
- One palette per diagram; recolor only the few nodes that carry the flow.
- Don't add a "generated by" footer or a date unless asked (AI-tell).
- Verify the file opened (the open snippet) before you say it's done.
