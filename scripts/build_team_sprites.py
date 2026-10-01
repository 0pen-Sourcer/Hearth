"""Build the Team room's sprite atlases from the source art.

The source is generated pixel art on a magenta background: pose sheets (2x2),
action sheets (2x2) and walk sheets (3x2), one of each per role, plus the room.
Each frame is keyed, cropped, scaled so every role stands the same height
against the desks, anchored on the feet, and colour-reduced so it stays crisp
at room size. Output goes to assets/team/: one atlas per role, the room, and
a manifest the window reads.

    python scripts/build_team_sprites.py <art folder>

The art folder holds bg.png, 1-8.png (actions), 9-16.png (walks),
old/1-8.png (poses), in role order, and optionally 17.png (more poses for the
main character), 18.png (a second main character), 19.png (small things for
the room), bg_props.png, whose lower
part is a 3x2 grid of furniture on magenta (desk, desk in use, chair,
armchair, side table, mug) and room_empty.png, the room with no furniture.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "team")
ROLES = ["coder", "researcher", "summarizer", "librarian", "archivist",
         "coordinator", "custom", "main"]
# Frames in atlas order, with where each comes from: (sheet, cols, rows, cell).
FRAMES = [
    ("idle", "pose", 2, 2, 0), ("type", "pose", 2, 2, 1),
    ("raise", "pose", 2, 2, 2), ("stretch", "pose", 2, 2, 3),
    ("read", "act", 2, 2, 0), ("coffee", "act", 2, 2, 1),
    ("think", "act", 2, 2, 2), ("gaze", "act", 2, 2, 3),
    ("walk_down_1", "walk", 3, 2, 0), ("walk_up_1", "walk", 3, 2, 1), ("walk_side_1", "walk", 3, 2, 2),
    ("walk_down_2", "walk", 3, 2, 3), ("walk_up_2", "walk", 3, 2, 4), ("walk_side_2", "walk", 3, 2, 5),
]
STAND_H = 132          # standing height in room pixels (the room is 1536 wide)
CELL_W, CELL_H = 104, 144
COLORS = 40
# Furniture, in grid order, with its width in room pixels (measured from the
# room the characters were drawn against, so everything shares one scale).
PROPS = [("desk", 233), ("desk_on", 233), ("chair", 80), ("armchair", 145), ("side_table", 60), ("mug", 22)]
MORE_PROPS = [("cat_sleep", 50), ("cat_sit", 38), ("book_open", 44), ("books", 40),
              ("note", 20), ("kettle", 34), ("blanket", 50), ("stool", 46)]
# The main character's poses beyond the fourteen everyone has, in sheet order.
EXTRA_MAIN = ["sit", "sit_read", "sit_coffee", "talk", "nod", "wave",
              "side_idle", "side_coffee", "side_read", "tend_fire", "sleep", "pin"]


def key_magenta(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    # Magenta and its anti-aliased fringe: red and blue both well above green.
    bg = (r - g > 70) & (b - g > 70) & (np.abs(r - b) < 90)
    rgba = np.dstack([a.astype(np.uint8), np.where(bg, 0, 255).astype(np.uint8)])
    # Pull magenta spill out of edge pixels that survived.
    edge = (rgba[..., 3] > 0) & (r - g > 40) & (b - g > 40)
    rgba[edge, 0] = np.minimum(rgba[edge, 0], g[edge] + 40)
    rgba[edge, 2] = np.minimum(rgba[edge, 2], g[edge] + 40)
    return rgba


def cells(rgba: np.ndarray, cols: int, rows: int):
    h, w = rgba.shape[:2]
    for i in range(cols * rows):
        c, r = i % cols, i // cols
        yield rgba[r * h // rows:(r + 1) * h // rows, c * w // cols:(c + 1) * w // cols]


def bbox(cell: np.ndarray):
    mask = cell[..., 3] > 0
    # Ignore specks: a column or row needs a few solid pixels to count.
    ys = np.where(mask.sum(1) > 3)[0]
    xs = np.where(mask.sum(0) > 3)[0]
    if not len(ys) or not len(xs):
        return None
    return xs[0], ys[0], xs[-1] + 1, ys[-1] + 1


def sheet_frames(path: str, cols: int, rows: int):
    rgba = key_magenta(Image.open(path))
    out = []
    for cell in cells(rgba, cols, rows):
        bb = bbox(cell)
        out.append((cell, bb))
    return out


def _drop_edge_bits(cell: np.ndarray, limit: int = 220) -> None:
    """Clear small pieces that touch a side edge of the cell: what is left of
    a neighbour after the cut."""
    mask = cell[..., 3] > 0
    h, w = mask.shape
    seen = np.zeros_like(mask)
    for x0 in (0, 1, 2, w - 3, w - 2, w - 1):
        for y0 in np.where(mask[:, x0])[0]:
            if seen[y0, x0]:
                continue
            stack, piece = [(y0, x0)], []
            seen[y0, x0] = True
            while stack:
                y, x = stack.pop()
                piece.append((y, x))
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(piece) <= limit:
                for y, x in piece:
                    cell[y, x, 3] = 0


def strip_frames(band: np.ndarray, expect: int):
    """The frames in one row that was drawn by eye, not on a grid: split where
    the columns are empty, hand small marks (a sparkle, a wisp of steam) to the
    figure next to them, and divide a run of touching figures evenly."""
    solid = (band[..., 3] > 0).sum(0) > 2
    runs, start = [], None
    for x, v in enumerate(list(solid) + [False]):
        if v and start is None:
            start = x
        elif not v and start is not None:
            runs.append([start, x])
            start = None
    nominal = band.shape[1] / expect
    single = [b - a for a, b in runs if 0.5 * nominal <= b - a <= 1.4 * nominal]
    unit = float(np.median(single)) if single else nominal
    big = [r for r in runs if r[1] - r[0] >= 0.35 * unit]
    for a, b in runs:
        if b - a < 0.35 * unit and big:
            near = min(big, key=lambda r: min(abs(r[0] - b), abs(a - r[1])))
            near[0], near[1] = min(near[0], a), max(near[1], b)
    spans = []
    for a, b in sorted(big):
        n = max(1, round((b - a) / unit))
        # Figures that touch: cut near each even division, at the thinnest column.
        weight = (band[..., 3] > 0).sum(0)
        cuts = [a]
        for i in range(1, n):
            est = a + (b - a) * i // n
            lo, hi = max(a + 1, est - 16), min(b - 1, est + 16)
            cuts.append(lo + int(np.argmin(weight[lo:hi])))
        cuts.append(b)
        spans += list(zip(cuts, cuts[1:]))
    if len(spans) != expect:
        w = band.shape[1]
        spans = [(w * i // expect, w * (i + 1) // expect) for i in range(expect)]
    out = []
    for a, b in spans:
        cell = band[:, a:b].copy()
        # A sliver at the very edge is the neighbour's elbow, not this figure.
        _drop_edge_bits(cell)
        out.append((cell, bbox(cell)))
    return out


def rows_of(rgba: np.ndarray, rows: int):
    """The image cut into its rows of figures, at the empty bands between them."""
    solid = (rgba[..., 3] > 0).sum(1) > 3
    ys = np.where(solid)[0]
    if rows == 1 or not len(ys):
        return [rgba]
    mid = rgba.shape[0] // 2
    gap = [y for y in range(ys[0], ys[-1]) if not solid[y]]
    cut = min(gap, key=lambda y: abs(y - mid)) if gap else mid
    return [rgba[:cut], rgba[cut:]]


def standing_height(frames, idxs):
    hs = [f[1][3] - f[1][1] for i, f in enumerate(frames) if i in idxs and f[1]]
    return float(np.median(hs)) if hs else 1.0


def place(cell, bb, scale) -> Image.Image:
    x0, y0, x1, y1 = bb
    crop = Image.fromarray(cell[y0:y1, x0:x1], "RGBA")
    w = max(1, round((x1 - x0) * scale))
    h = max(1, round((y1 - y0) * scale))
    small = crop.resize((w, h), Image.LANCZOS)
    a = np.asarray(small).copy()
    a[..., 3] = np.where(a[..., 3] > 110, 255, 0)
    small = Image.fromarray(a, "RGBA")
    canvas = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
    canvas.alpha_composite(small, ((CELL_W - w) // 2, CELL_H - h - 2))
    return canvas


def quantize(img: Image.Image) -> Image.Image:
    alpha = img.getchannel("A")
    rgb = img.convert("RGB").quantize(COLORS, method=Image.MEDIANCUT, dither=Image.NONE).convert("RGB")
    rgb.putalpha(alpha)
    return rgb


def extra_main(art: str, target: float):
    """The main character's further poses (17.png, two rows of six), each in a
    cell of its own, at the scale of her standing frames."""
    path = os.path.join(art, "17.png")
    if not os.path.isfile(path):
        return []
    frames = [f for band in rows_of(key_magenta(Image.open(path)), 2) for f in strip_frames(band, 6)]
    scale = target / standing_height(frames, {3, 4})
    return [place(cell, bb, scale) for cell, bb in frames[:len(EXTRA_MAIN)]]


def second_main(art: str):
    """The second main character (18.png): the fourteen frames in one row. His
    typing frame was drawn with a chair, and the room brings its own, so it is
    made from his back view instead: head to hips, the rest is behind the chair."""
    path = os.path.join(art, "18.png")
    if not os.path.isfile(path):
        return None
    frames = strip_frames(key_magenta(Image.open(path)), len(FRAMES))
    names = [f[0] for f in FRAMES]
    # His walk was drawn direction by direction (down, down, up, up, side,
    # side); everyone else's goes step by step (down, up, side, then again).
    drawn = ["walk_down_1", "walk_down_2", "walk_up_1", "walk_up_2", "walk_side_1", "walk_side_2"]
    walk = {name: frames[8 + k] for k, name in enumerate(drawn)}
    frames = [walk.get(n, f) for n, f in zip(names, frames)]
    scale = STAND_H * 1.06 / standing_height(frames, {0, 4, 5, 6, 7})
    atlas = Image.new("RGBA", (CELL_W * len(FRAMES), CELL_H), (0, 0, 0, 0))
    for i, (cell, bb) in enumerate(frames):
        if names[i] == "type":
            cell, bb = frames[names.index("gaze")]
            x0, y0, x1, y1 = bb
            bb = (x0, y0, x1, y0 + round((y1 - y0) * 0.62))
        atlas.alpha_composite(place(cell, bb, scale), (i * CELL_W, 0))
    return atlas


def more_props(art: str):
    """Small things for the room (19.png, two rows of four), by name."""
    path = os.path.join(art, "19.png")
    if not os.path.isfile(path):
        return {}
    frames = [f for band in rows_of(key_magenta(Image.open(path)), 2) for f in strip_frames(band, 4)]
    out = {}
    for (name, width), (cell, bb) in zip(MORE_PROPS, frames):
        x0, y0, x1, y1 = bb
        crop = Image.fromarray(cell[y0:y1, x0:x1], "RGBA")
        small = crop.resize((width, max(1, round((y1 - y0) * width / (x1 - x0)))), Image.LANCZOS)
        a = np.asarray(small).copy()
        a[..., 3] = np.where(a[..., 3] > 110, 255, 0)
        out[name] = quantize(Image.fromarray(a, "RGBA"))
    return out


def build(art: str) -> None:
    os.makedirs(OUT, exist_ok=True)
    manifest = {"cell": [CELL_W, CELL_H], "frames": [f[0] for f in FRAMES], "roles": ROLES}
    for n, role in enumerate(ROLES, start=1):
        sheets = {
            "pose": sheet_frames(os.path.join(art, "old", f"{n}.png"), 2, 2),
            "act": sheet_frames(os.path.join(art, f"{n}.png"), 2, 2),
            "walk": sheet_frames(os.path.join(art, f"{n + 8}.png"), 3, 2),
        }
        ref = {
            "pose": standing_height(sheets["pose"], {0, 2}),
            "act": standing_height(sheets["act"], {0, 1, 2, 3}),
            "walk": standing_height(sheets["walk"], {0, 1, 2, 3, 4, 5}),
        }
        target = STAND_H * (1.06 if role == "main" else 1.0)
        atlas = Image.new("RGBA", (CELL_W * len(FRAMES), CELL_H), (0, 0, 0, 0))
        for i, (_, sheet, _c, _r, idx) in enumerate(FRAMES):
            cell, bb = sheets[sheet][idx]
            if not bb:
                continue
            atlas.alpha_composite(place(cell, bb, target / ref[sheet]), (i * CELL_W, 0))
        if role == "main":
            more = extra_main(art, target)
            if more:
                wide = Image.new("RGBA", (atlas.width + CELL_W * len(more), CELL_H), (0, 0, 0, 0))
                wide.alpha_composite(atlas, (0, 0))
                for i, im in enumerate(more):
                    wide.alpha_composite(im, (atlas.width + i * CELL_W, 0))
                atlas = wide
                manifest["extra"] = {"main": EXTRA_MAIN}
        quantize(atlas).save(os.path.join(OUT, f"{role}.png"), optimize=True)
    second = second_main(art)
    if second:
        quantize(second).save(os.path.join(OUT, "main_m.png"), optimize=True)
        manifest["roles"] = ROLES + ["main_m"]
    props_src = os.path.join(art, "bg_props.png")
    if os.path.isfile(props_src):
        rgba = key_magenta(Image.open(props_src))
        magenta = rgba[..., 3] == 0
        top = next(y for y in range(rgba.shape[0]) if magenta[y].mean() > 0.5)
        grid = rgba[top:]
        placed = {}
        # Split on the drawn grid lines: the rows are not always equal height.
        gm = grid[..., 3] == 0
        cuts_y = [0] + [y for y in range(1, gm.shape[0] - 1) if gm[y].mean() < 0.3 and gm[y - 1].mean() >= 0.3] + [gm.shape[0]]
        cuts_x = [0] + [x for x in range(1, gm.shape[1] - 1) if gm[:, x].mean() < 0.3 and gm[:, x - 1].mean() >= 0.3] + [gm.shape[1]]
        grid_cells = [grid[cuts_y[r]:cuts_y[r + 1], cuts_x[c]:cuts_x[c + 1]]
                      for r in range(len(cuts_y) - 1) for c in range(len(cuts_x) - 1)]
        for i, cell in enumerate(grid_cells[:len(PROPS)]):
            name, width = PROPS[i]
            cell = cell[6:-6, 6:-6]                  # drop the grid lines themselves
            bb = bbox(cell)
            if not bb:
                continue
            x0, y0, x1, y1 = bb
            crop = Image.fromarray(cell[y0:y1, x0:x1], "RGBA")
            scale = width / (x1 - x0)
            small = crop.resize((width, max(1, round((y1 - y0) * scale))), Image.LANCZOS)
            a = np.asarray(small).copy()
            a[..., 3] = np.where(a[..., 3] > 110, 255, 0)
            placed[name] = quantize(Image.fromarray(a, "RGBA"))
        placed.update(more_props(art))
        x = 0
        sheet = Image.new("RGBA", (sum(im.width for im in placed.values()) + 4 * len(placed),
                                   max(im.height for im in placed.values())), (0, 0, 0, 0))
        manifest["props"] = {}
        for name, im in placed.items():
            sheet.alpha_composite(im, (x, 0))
            manifest["props"][name] = [x, 0, im.width, im.height]
            x += im.width + 4
        sheet.save(os.path.join(OUT, "props.png"), optimize=True)
    empty = os.path.join(art, "room_empty.png")
    room = Image.open(empty if os.path.isfile(empty) else os.path.join(art, "bg.png")).convert("RGB")
    manifest["room_empty"] = os.path.isfile(empty)
    room.quantize(128, method=Image.MEDIANCUT, dither=Image.NONE).save(os.path.join(OUT, "room.png"), optimize=True)
    # What the art's addresses end in: a browser that kept an older sheet
    # would otherwise draw frames the old sheet doesn't have, as nothing.
    import hashlib
    digest = hashlib.sha1()
    for name in sorted(os.listdir(OUT)):
        if name.endswith(".png"):
            with open(os.path.join(OUT, name), "rb") as f:
                digest.update(f.read())
    manifest["v"] = digest.hexdigest()[:10]
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("built", len(ROLES), "atlases in", OUT)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(sys.argv[1])
