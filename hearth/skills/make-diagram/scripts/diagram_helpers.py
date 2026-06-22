"""Connector helpers for FREEHAND diagrams.

You author each diagram fresh (your own boxes, palette, layout, composition — so
no two diagrams look the same). The ONE thing models reliably get wrong is arrow
geometry: lines overshoot a box, miss it, or leave a node orphaned. So you import
`connect()` here and let it compute the edge-to-edge route. You keep full design
control; the math you'd fumble is handled.

Usage in your build script:
    import sys
    sys.path.insert(0, r"<skill-folder>/scripts")   # path from load_skill
    from diagram_helpers import connect, marker_defs

    # YOU place boxes however your design wants — dicts with x,y,w,h:
    a = {"x": 300, "y": 40,  "w": 200, "h": 60}
    b = {"x": 300, "y": 180, "w": 200, "h": 60}
    c = {"x": 300, "y": 320, "w": 160, "h": 80, "shape": "diamond"}

    svg_edges = (
        connect(a, b) +                                  # straight down, auto
        connect(c, a, kind="error", label="retry")       # detects up-edge -> side lane
    )
    svg = f'''<svg ...>{marker_defs()}
      ...your <rect>/<polygon>/<text> for a,b,c, drawn AFTER the edges...
      {svg_edges}'''

Draw connectors BEFORE the boxes in the SVG so boxes sit on top of any line end.
connect() reads only x/y/w/h (+ optional "shape":"diamond") and the relative
position of the two boxes — below / above (a loop) / beside — and routes
accordingly. Pass lane_x to push a back-edge's vertical lane to a specific column
when you have several loops.
"""
from __future__ import annotations

KIND_COLOR = {"normal": "#9b8cc7", "success": "#22c55e", "error": "#ef4444"}


def marker_defs(colors: dict | None = None) -> str:
    """Arrowhead markers, one per kind. Drop inside your <defs> (or call standalone
    — it returns its own <marker> elements). Override colors with {kind: hex}."""
    c = dict(KIND_COLOR)
    if colors:
        c.update(colors)
    out = []
    for kind, col in c.items():
        out.append(
            f'<marker id="arr-{kind}" markerWidth="11" markerHeight="11" refX="8" '
            f'refY="3.2" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path d="M0,0 L9,3.2 L0,6.4 Z" fill="{col}"/></marker>')
    return "".join(out)


def _pts(a: dict, b: dict, lane_x: float | None):
    ax_c, ay_c = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx_c, by_c = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    a_top, a_bot, a_l, a_r = a["y"], a["y"] + a["h"], a["x"], a["x"] + a["w"]
    b_top, b_bot, b_l, b_r = b["y"], b["y"] + b["h"], b["x"], b["x"] + b["w"]
    diamond = a.get("shape") == "diamond"

    # b BELOW a  -> forward edge, bottom/side -> top
    if b_top >= a_bot - 2:
        if diamond and bx_c > ax_c + 4:
            sx, sy = a_r, ay_c
        elif diamond and bx_c < ax_c - 4:
            sx, sy = a_l, ay_c
        else:
            sx, sy = ax_c, a_bot
        ex, ey = bx_c, b_top
        if abs(sx - ex) < 2:
            return [(sx, sy), (ex, ey)]
        if abs(sy - ay_c) < 2:                       # side exit: across then down
            return [(sx, sy), (ex, sy), (ex, ey)]
        midy = (sy + ey) / 2                          # bottom exit: down, across, down
        return [(sx, sy), (sx, midy), (ex, midy), (ex, ey)]

    # b ABOVE a  -> back-edge (loop) -> route out a side and up a lane
    if b_bot <= a_top + 2:
        lx = lane_x if lane_x is not None else a_r + 40
        return [(a_r, ay_c), (lx, ay_c), (lx, by_c), (b_r, by_c)]

    # roughly same row -> side to side
    if bx_c >= ax_c:
        sx, sy, ex, ey = a_r, ay_c, b_l, by_c
    else:
        sx, sy, ex, ey = a_l, ay_c, b_r, by_c
    midx = (sx + ex) / 2
    return [(sx, sy), (midx, sy), (midx, ey), (ex, ey)]


def connect(a: dict, b: dict, kind: str = "normal", label: str | None = None,
            lane_x: float | None = None, width: float = 2.4,
            bg: str = "#14121c") -> str:
    """Return SVG for a connector from box `a` to box `b`, routed edge-to-edge.
    `kind`: normal | success | error (color + arrowhead). Optional `label` gets a
    small background chip so it stays readable over the line."""
    col = KIND_COLOR.get(kind, KIND_COLOR["normal"])
    pts = _pts(a, b, lane_x)
    d = "M " + " L ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    out = (f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{width}" '
           f'marker-end="url(#arr-{kind if kind in KIND_COLOR else "normal"})"/>')
    if label:
        # anchor on the longest segment's midpoint
        best, blen = (pts[0], pts[-1]), -1.0
        for p, q in zip(pts, pts[1:]):
            seg = abs(p[0] - q[0]) + abs(p[1] - q[1])
            if seg > blen:
                blen, best = seg, (p, q)
        lx = (best[0][0] + best[1][0]) / 2
        ly = (best[0][1] + best[1][1]) / 2
        w = len(str(label)) * 7 + 10
        out += (f'<rect x="{lx-w/2:.0f}" y="{ly-10:.0f}" width="{w:.0f}" height="18" '
                f'rx="4" fill="{bg}" opacity="0.9"/>'
                f'<text x="{lx:.0f}" y="{ly+3:.0f}" fill="{col}" font-size="12.5" '
                f'font-weight="600" text-anchor="middle">{label}</text>')
    return out
