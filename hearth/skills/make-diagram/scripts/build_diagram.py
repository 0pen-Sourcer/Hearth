"""Deterministic diagram renderer for the make-diagram skill.

The model supplies a LOGICAL graph (nodes + edges) as JSON; this script does the
layout and — critically — auto-routes every connector so arrows always attach to
box edges and never overshoot or orphan a node. Hand-computed SVG coordinates are
the #1 way flowcharts come out broken; this removes that failure mode.

Spec (JSON file or stdin):
  {
    "title": "Login flow",
    "direction": "TB",                  # TB (top-down, default) or LR (left-right)
    "nodes": [
      {"id": "s",  "label": "Start",            "type": "start"},
      {"id": "c",  "label": "Enter credentials","type": "process"},
      {"id": "v",  "label": "Valid?",           "type": "decision"},
      {"id": "ok", "label": "Success",          "type": "success"},
      {"id": "no", "label": "Failure",          "type": "error"}
    ],
    "edges": [
      {"from": "s",  "to": "c"},
      {"from": "c",  "to": "v"},
      {"from": "v",  "to": "ok", "label": "Yes", "kind": "success"},
      {"from": "v",  "to": "no", "label": "No",  "kind": "error"},
      {"from": "no", "to": "c"}                     # back-edge: auto side-lane routed
    ]
  }

node.type: start | end | process | io | decision | success | error
edge.kind: normal (default) | success | error

Usage:
  python build_diagram.py spec.json --out diagrams/login.html [--format html|svg|excalidraw]

Always auto-lays-out from edges; you never pass x/y. Prints the output path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict, deque

# ---- palette (dark, readable) -------------------------------------------------
P = {
    "bg": "#14121c", "card": "#211d33", "accent": "#7c5cff", "fg": "#e9e0ff",
    "muted": "#9b8cc7", "green": "#22c55e", "greenbg": "#0c3a22",
    "red": "#ef4444", "redbg": "#3f1c1c", "amber": "#f59e0b", "amberbg": "#3a2a0a",
    "blue": "#38bdf8", "bluebg": "#0c2a3a",
}
# type -> (stroke, fill)
TYPE_STYLE = {
    "start": ("accent", "card"), "end": ("accent", "card"),
    "process": ("accent", "card"), "io": ("blue", "bluebg"),
    "decision": ("amber", "amberbg"), "success": ("green", "greenbg"),
    "error": ("red", "redbg"),
}
KIND_COLOR = {"normal": "muted", "success": "green", "error": "red"}

CHAR_W = 8.4          # approx px per char at 15px bold
PAD_X = 34
MIN_W = 150
LINE_H = 20
BASE_H = 58
MARGIN = 44
HGAP = 60
VGAP = 74
LANE_GAP = 26         # spacing between back-edge side lanes


# ---- text wrapping ------------------------------------------------------------
def _wrap(label, max_chars=20):
    words = str(label).split()
    if not words:
        return [""]
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:3]


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---- layout -------------------------------------------------------------------
def _layout(nodes, edges):
    """Assign rank (layer) + x/y to every node; mark back-edges. Returns
    (ordered ranks, canvas_w, canvas_h, n_back)."""
    nid = {n["id"]: n for n in nodes}
    out = defaultdict(list)
    indeg = defaultdict(int)
    for e in edges:
        if e["from"] in nid and e["to"] in nid:
            out[e["from"]].append(e["to"])

    # back-edge detection via DFS (edge to a node on the current stack)
    color = {n["id"]: 0 for n in nodes}   # 0=unseen 1=on-stack 2=done
    back = set()

    def dfs(u):
        color[u] = 1
        for v in out[u]:
            if color[v] == 1:
                back.add((u, v))
            elif color[v] == 0:
                dfs(v)
        color[u] = 2

    for n in nodes:
        if color[n["id"]] == 0:
            dfs(n["id"])

    # forward indegree (ignoring back-edges) for longest-path layering
    fwd = defaultdict(list)
    for e in edges:
        if e["from"] in nid and e["to"] in nid and (e["from"], e["to"]) not in back:
            fwd[e["from"]].append(e["to"])
            indeg[e["to"]] += 1
    rank = {n["id"]: 0 for n in nodes}
    q = deque([n["id"] for n in nodes if indeg[n["id"]] == 0])
    seen = set(q)
    while q:
        u = q.popleft()
        for v in fwd[u]:
            rank[v] = max(rank[v], rank[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0 and v not in seen:
                q.append(v)
                seen.add(v)
    # any node never reached (cycle remnant) keeps rank 0 -> bump below a parent
    for e in edges:
        if (e["from"], e["to"]) not in back and e["from"] in nid and e["to"] in nid:
            if rank[e["to"]] <= rank[e["from"]]:
                rank[e["to"]] = rank[e["from"]] + 1

    # size each node
    for n in nodes:
        lines = _wrap(n["label"])
        n["_lines"] = lines
        longest = max((len(s) for s in lines), default=1)
        n["w"] = max(MIN_W, int(longest * CHAR_W + PAD_X))
        n["h"] = BASE_H + (len(lines) - 1) * LINE_H
        if n.get("type") == "decision":          # diamonds need breathing room
            n["w"] = int(n["w"] * 1.25)
            n["h"] = int(n["h"] * 1.3)

    # group by rank, preserve declaration order within a rank
    ranks = defaultdict(list)
    for n in nodes:
        ranks[rank[n["id"]]].append(n)
    order = sorted(ranks)

    row_w = {r: sum(n["w"] for n in ranks[r]) + HGAP * (len(ranks[r]) - 1) for r in order}
    row_h = {r: max(n["h"] for n in ranks[r]) for r in order}
    max_row_w = max(row_w.values()) if row_w else MIN_W
    n_back = len(back)
    lane_space = (n_back * LANE_GAP + 30) if n_back else 0

    canvas_w = MARGIN * 2 + max_row_w + lane_space
    # y per rank
    y_at = {}
    yy = MARGIN
    for r in order:
        y_at[r] = yy
        yy += row_h[r] + VGAP
    canvas_h = yy - VGAP + MARGIN

    # x within each rank, centered in the max-row band
    for r in order:
        x = MARGIN + (max_row_w - row_w[r]) / 2
        for n in ranks[r]:
            n["x"] = int(x)
            n["y"] = int(y_at[r] + (row_h[r] - n["h"]) / 2)
            x += n["w"] + HGAP

    for e in edges:
        e["_back"] = (e["from"], e["to"]) in back
    return order, ranks, int(canvas_w), int(canvas_h), max_row_w


# ---- geometry helpers ---------------------------------------------------------
def _cx(n):
    return n["x"] + n["w"] / 2


def _cy(n):
    return n["y"] + n["h"] / 2


def _route(a, b, e, lane_x):
    """Return a list of (x,y) points for an orthogonal connector + a label
    anchor. Forward edges go bottom->top with a single elbow; back-edges route
    out the right side, up a dedicated lane, and back into the target's right."""
    if e["_back"]:
        ax, ay = a["x"] + a["w"], _cy(a)            # exit right side of source
        bx, by = b["x"] + b["w"], _cy(b)            # enter right side of target
        pts = [(ax, ay), (lane_x, ay), (lane_x, by), (bx, by)]
        return pts, (lane_x, (ay + by) / 2)

    # forward (b is below a)
    if a.get("type") == "decision":
        # exit the vertex nearest the target's horizontal position
        if _cx(b) > _cx(a) + 4:
            sx, sy = a["x"] + a["w"], _cy(a)        # right vertex
        elif _cx(b) < _cx(a) - 4:
            sx, sy = a["x"], _cy(a)                 # left vertex
        else:
            sx, sy = _cx(a), a["y"] + a["h"]        # bottom vertex
    else:
        sx, sy = _cx(a), a["y"] + a["h"]            # bottom center

    ex, ey = _cx(b), b["y"]                          # top center of target
    if abs(sx - ex) < 2:
        pts = [(sx, sy), (ex, ey)]
        return pts, ((sx + ex) / 2, (sy + ey) / 2)
    if abs(sy - _cy(a)) < 2:                         # side exit (decision): across then down
        pts = [(sx, sy), (ex, sy), (ex, ey)]
        return pts, ((sx + ex) / 2, sy)
    midy = (sy + ey) / 2                             # bottom exit: down, across, down
    pts = [(sx, sy), (sx, midy), (ex, midy), (ex, ey)]
    return pts, ((sx + ex) / 2, midy)


# ---- SVG renderer -------------------------------------------------------------
def _svg(nodes, edges, order, ranks, w, h, max_row_w):
    def col(name):
        return P.get(name, name)

    defs = []
    for kind, cname in KIND_COLOR.items():
        c = col(cname)
        defs.append(
            f'<marker id="arr-{kind}" markerWidth="11" markerHeight="11" refX="8" '
            f'refY="3.2" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path d="M0,0 L9,3.2 L0,6.4 Z" fill="{c}"/></marker>')

    parts = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
             f'xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial,sans-serif">',
             f'<defs>{"".join(defs)}</defs>',
             f'<rect width="{w}" height="{h}" fill="{P["bg"]}"/>']

    # connectors FIRST so boxes sit on top of any line ends
    lane_base = MARGIN + max_row_w + 14
    lane_i = 0
    nid = {n["id"]: n for n in nodes}
    for e in edges:
        a, b = nid.get(e["from"]), nid.get(e["to"])
        if not a or not b:
            continue
        lane_x = lane_base + lane_i * LANE_GAP
        if e["_back"]:
            lane_i += 1
        pts, (lx, ly) = _route(a, b, e, lane_x)
        kind = e.get("kind", "normal")
        if kind not in KIND_COLOR:
            kind = "normal"
        c = col(KIND_COLOR[kind])
        d = "M " + " L ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
        parts.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2.4" '
                     f'marker-end="url(#arr-{kind})"/>')
        if e.get("label"):
            parts.append(
                f'<rect x="{lx-len(str(e["label"]))*4-4:.0f}" y="{ly-10:.0f}" '
                f'width="{len(str(e["label"]))*8+8:.0f}" height="18" rx="4" '
                f'fill="{P["bg"]}" opacity="0.9"/>'
                f'<text x="{lx:.0f}" y="{ly+3:.0f}" fill="{c}" font-size="12.5" '
                f'font-weight="600" text-anchor="middle">{_esc(e["label"])}</text>')

    # nodes
    for n in nodes:
        stroke_n, fill_n = TYPE_STYLE.get(n.get("type", "process"), TYPE_STYLE["process"])
        stroke, fill = col(stroke_n), col(fill_n)
        x, y, bw, bh = n["x"], n["y"], n["w"], n["h"]
        if n.get("type") == "decision":
            cx, cy = x + bw / 2, y + bh / 2
            pts = f"{cx},{y} {x+bw},{cy} {cx},{y+bh} {x},{cy}"
            parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        else:
            parts.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="13" '
                         f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        lines = n["_lines"]
        ty = y + bh / 2 - (len(lines) - 1) * LINE_H / 2 + 5
        for i, ln in enumerate(lines):
            parts.append(f'<text x="{x+bw/2:.0f}" y="{ty+i*LINE_H:.0f}" fill="{P["fg"]}" '
                         f'font-size="15" font-weight="600" text-anchor="middle">{_esc(ln)}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


# ---- excalidraw renderer (editable; excalidraw auto-routes bound arrows) ------
def _excalidraw(nodes, edges):
    import random
    els = []
    idmap = {}

    def rid():
        return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8))

    for n in nodes:
        eid = rid()
        idmap[n["id"]] = eid
        shape = "diamond" if n.get("type") == "decision" else "rectangle"
        stroke = {"start": "#7048e8", "end": "#7048e8", "process": "#7048e8",
                  "io": "#1971c2", "decision": "#f08c00", "success": "#2f9e44",
                  "error": "#e03131"}.get(n.get("type", "process"), "#7048e8")
        text_id = rid()
        els.append({
            "id": eid, "type": shape, "x": n["x"], "y": n["y"], "width": n["w"],
            "height": n["h"], "angle": 0, "strokeColor": stroke,
            "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 2,
            "strokeStyle": "solid", "roughness": 1, "opacity": 100, "seed": random.randint(1, 1 << 30),
            "version": 1, "versionNonce": random.randint(1, 1 << 30), "isDeleted": False,
            "boundElements": [{"type": "text", "id": text_id}], "groupIds": [],
            "frameId": None, "roundness": {"type": 3}, "link": None, "locked": False,
        })
        els.append({
            "id": text_id, "type": "text", "x": n["x"] + 8, "y": n["y"] + n["h"] / 2 - 10,
            "width": n["w"] - 16, "height": 20, "angle": 0, "strokeColor": "#1e1e1e",
            "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 1,
            "strokeStyle": "solid", "roughness": 1, "opacity": 100, "seed": random.randint(1, 1 << 30),
            "version": 1, "versionNonce": random.randint(1, 1 << 30), "isDeleted": False,
            "boundElements": [], "groupIds": [], "frameId": None, "roundness": None,
            "link": None, "locked": False, "text": n["label"], "fontSize": 16,
            "fontFamily": 1, "textAlign": "center", "verticalAlign": "middle",
            "containerId": eid, "originalText": n["label"], "lineHeight": 1.25,
        })
    for e in edges:
        if e["from"] not in idmap or e["to"] not in idmap:
            continue
        aid, bid = idmap[e["from"]], idmap[e["to"]]
        arr = rid()
        els.append({
            "id": arr, "type": "arrow", "x": 0, "y": 0, "width": 1, "height": 1,
            "angle": 0, "strokeColor": "#868e96", "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "seed": random.randint(1, 1 << 30), "version": 1,
            "versionNonce": random.randint(1, 1 << 30), "isDeleted": False,
            "boundElements": [], "groupIds": [], "frameId": None,
            "roundness": {"type": 2}, "link": None, "locked": False,
            "points": [[0, 0], [60, 0]], "lastCommittedPoint": None,
            "startBinding": {"elementId": aid, "focus": 0, "gap": 4},
            "endBinding": {"elementId": bid, "focus": 0, "gap": 4},
            "startArrowhead": None, "endArrowhead": "arrow",
        })
    return json.dumps({"type": "excalidraw", "version": 2, "source": "hearth",
                       "elements": els, "appState": {"viewBackgroundColor": "#ffffff",
                       "gridSize": None}, "files": {}}, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", help="path to spec JSON (or '-' / omit for stdin)")
    ap.add_argument("--out", required=True, help="output file path")
    ap.add_argument("--format", default=None, choices=["html", "svg", "excalidraw"])
    args = ap.parse_args()

    raw = (sys.stdin.read() if (not args.spec or args.spec == "-")
           else open(args.spec, encoding="utf-8").read())
    spec = json.loads(raw)
    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    if not nodes:
        print("error: spec has no nodes", file=sys.stderr)
        return 2

    fmt = args.format or (os.path.splitext(args.out)[1].lstrip(".") or "html")
    if fmt == "excalidraw":
        # excalidraw still needs positions; run layout then export
        _layout(nodes, edges)
        content = _excalidraw(nodes, edges)
    else:
        direction = (spec.get("direction") or "TB").upper()
        order, ranks, w, h, max_row_w = _layout(nodes, edges)
        if direction == "LR":                       # transpose TB layout into LR
            for n in nodes:
                n["x"], n["y"] = n["y"], n["x"]
                n["w"], n["h"] = n["h"], n["w"]
            w, h = h, w
            max_row_w = h  # lane band recompute is approximate in LR; routing still attaches
        svg = _svg(nodes, edges, order, ranks, w, h, max_row_w)
        if fmt == "svg":
            content = svg
        else:
            title = _esc(spec.get("title") or "diagram")
            content = (f'<!doctype html><html><head><meta charset="utf-8">'
                       f'<title>{title}</title></head>'
                       f'<body style="margin:0;background:{P["bg"]};display:grid;'
                       f'place-items:center;min-height:100vh">{svg}</body></html>')

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(content)
    print(os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
