---
name: make-diagram
description: Build a diagram — flowchart, architecture/system diagram, sequence, mind map, org chart, decision tree — as a self-contained HTML/SVG that opens in any browser, OR as an editable Excalidraw file. Use when the user wants a diagram, chart of boxes-and-arrows, system design picture, or flow. NOT for data charts (that's a chart inside make-pdf) or for reading an existing diagram.
version: 2.1.0
---

# Make a diagram — author it FRESH, let the helper handle the arrows

There is NO template. You design each diagram from scratch so it fits THIS
request — your own palette, layout, box shapes, grouping, emphasis. A login flow,
a cloud architecture, and an org chart should look like three different pieces,
not the same canned picture.

The only part you must NOT hand-compute is **arrow geometry**. Hand-placed line
coordinates are the #1 way diagrams break — arrows overshoot a box, miss it, or
leave a node with nothing pointing at it. So you place the boxes (full design
freedom) and call `connect()` for every arrow; it routes each one edge-to-edge.

## The pipeline (every time)

1. **Plan the design + the graph.** Pick the look (palette, dark/light, shapes,
   spacing, which nodes carry the flow and get an accent). Then list nodes and
   edges (which box points to which, branch labels, loops).

2. **Write a fresh build script** to `<workspace>/.build/<slug>_build.py`. Place
   YOUR boxes on a grid as plain dicts, draw them however your design wants, and
   import the connector helper so the arrows are correct (load_skill gave you
   this skill's folder):
   ```python
   import sys, os
   sys.path.insert(0, r"<skill-folder>/scripts")
   from diagram_helpers import connect, marker_defs

   BG="#14121c"; CARD="#211d33"; ACCENT="#7c5cff"; FG="#e9e0ff"
   GREEN="#22c55e"; GREENBG="#0c3a22"; RED="#ef4444"; REDBG="#3f1c1c"; AMBER="#f59e0b"

   # YOU choose positions + styling — this is where the design lives:
   start = {"x":300,"y":40, "w":200,"h":58}
   creds = {"x":300,"y":170,"w":200,"h":58}
   valid = {"x":300,"y":300,"w":170,"h":80,"shape":"diamond"}
   ok    = {"x":120,"y":460,"w":200,"h":58}
   fail  = {"x":480,"y":460,"w":200,"h":58}

   def rect(b,label,stroke=ACCENT,fill=CARD):
       return (f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" '
               f'rx="13" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
               f'<text x="{b["x"]+b["w"]/2}" y="{b["y"]+b["h"]/2+5}" fill="{FG}" '
               f'font-family="Segoe UI,Arial" font-size="15" font-weight="600" '
               f'text-anchor="middle">{label}</text>')
   def diamond(b,label,stroke=AMBER,fill="#3a2a0a"):
       cx,cy=b["x"]+b["w"]/2,b["y"]+b["h"]/2
       pts=f'{cx},{b["y"]} {b["x"]+b["w"]},{cy} {cx},{b["y"]+b["h"]} {b["x"]},{cy}'
       return (f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
               f'<text x="{cx}" y="{cy+5}" fill="{FG}" font-family="Segoe UI,Arial" '
               f'font-size="15" font-weight="600" text-anchor="middle">{label}</text>')

   # edges FIRST (so boxes draw on top of the line ends), via the helper:
   edges = (connect(start,creds) + connect(creds,valid)
            + connect(valid,ok, kind="success", label="Yes")
            + connect(valid,fail,kind="error",   label="No")
            + connect(fail,creds,kind="error",   label="retry"))   # loop: auto side-lane

   W,H=800,560
   svg=(f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'xmlns="http://www.w3.org/2000/svg"><defs>{marker_defs()}</defs>'
        f'<rect width="{W}" height="{H}" fill="{BG}"/>{edges}'
        f'{rect(start,"Start")}{rect(creds,"Enter credentials")}{diamond(valid,"Valid?")}'
        f'{rect(ok,"Login complete",GREEN,GREENBG)}{rect(fail,"Failure",RED,REDBG)}</svg>')
   out=r"<workspace>/diagrams/<slug>.html"
   os.makedirs(os.path.dirname(out),exist_ok=True)
   open(out,"w",encoding="utf-8").write(
       f'<!doctype html><meta charset="utf-8"><body style="margin:0;background:{BG};'
       f'display:grid;place-items:center;min-height:100vh">{svg}</body>')
   print(out)
   ```
   `connect(a,b,kind=,label=,lane_x=)` figures out the route from the two boxes'
   positions: b below a → down-elbow into its top; b above a (a loop) → out the
   side and up a lane; b beside a → side-to-side. `kind`: normal/success/error.

3. **Run it** — `run_command python <workspace>/.build/<slug>_build.py`. It prints
   the output path.

4. **Open it** so the user sees it right away:
   ```python
   import sys, subprocess, os
   if sys.platform=="win32":   os.startfile(p)
   elif sys.platform=="darwin": subprocess.Popen(["open", p])
   else:                         subprocess.Popen(["xdg-open", p])
   ```

5. **Eyeball it + delete the build script.** Confirm every node is present and
   every branch/loop is drawn, then `delete_path` the `.build` script.

## Shortcut for a plain flowchart (no bespoke styling needed)

If the user just wants a quick correct flowchart and doesn't care about custom
design, skip writing a script: put the graph in a JSON spec and run the bundled
renderer, which does layout + routing for you:
`run_command python <skill-folder>/scripts/build_diagram.py <spec.json> --out <workspace>/diagrams/<slug>.html`
(spec schema is documented at the top of build_diagram.py; `--format excalidraw`
gives an editable file whose arrows stay bound to the boxes.) Use this ONLY for
throwaway/quick asks — for anything the user cares about the look of, author it
fresh per step 2.

## Hard rules
- Self-contained: NO external JS/CSS/CDN — it must open offline.
- Arrows via `connect()` (or the renderer); never hand-type line coordinates.
- One palette per diagram; recolor only the few nodes that carry the flow.
- Draw edges before boxes so boxes cover the line ends.
- No "generated by" footer or date unless asked (AI-tell).
