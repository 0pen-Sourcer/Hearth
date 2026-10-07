"""Build the Team room's sprite atlases from the source art.

The source is generated pixel art on a magenta background: pose sheets (2x2),
action sheets (2x2) and walk sheets (3x2), one of each per role, plus the room.
Each frame is keyed, cropped, scaled so every role stands the same height
against the desks, anchored on the feet, and colour-reduced so it stays crisp
at room size. Output goes to assets/team/: one atlas per role, the room, and
a manifest the window reads.

    python scripts/build_team_sprites.py <art folder>

The art folder holds bg.png, 1-8.png (actions), 9-16.png (walks),
walk_1-3.png (optional: side-on walks in four steps, three characters to an
image, in SIDE_WALKS order),
old/1-8.png (poses), in role order, and optionally 17.png (more poses for the
main character), 18.png (a second main character), 20.png (his further poses), 19.png (small things for
the room), bg_props.png, whose lower
part is a 3x2 grid of furniture on magenta (desk, desk in use, chair,
armchair, side table, mug) and room_empty.png, the room with no furniture.
The rooms in MORE_ROOMS, when present, are the same floor plan drawn in other
styles; they open in that order once the desks before them are taken.
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
    # Both side steps were drawn mid-stride, so the legs never pass each other
    # and the walk glides. These are the passing steps between them, made from
    # each stride by drawing the legs in under the hips.
    ("walk_side_3", "walk", 3, 2, -2), ("walk_side_4", "walk", 3, 2, -5),
]
# Further rooms: the same floor plan in other styles, (id, name, source).
MORE_ROOMS = [("winter", "Winter", "room_winter.png"), ("library", "Library", "room_library.png"),
              ("ocean", "Ocean", "room_ocean.png")]
# Who is on each row of walk_1-3.png, when they exist. Their four columns are
# a stride, the legs passing, the other stride, passing again.
SIDE_WALKS = [["main", "main_m", "coder"], ["researcher", "summarizer", "librarian"],
              ["archivist", "coordinator", "custom"]]
SIDE_ORDER = ["walk_side_1", "walk_side_3", "walk_side_2", "walk_side_4"]
LEGS = 0.30            # the part of a side-on figure that is legs
PASS_SQUEEZE = 0.5     # how far in the feet come at the passing step
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
# The second character was drawn in all twelve (20.png) and uses the ones on his feet.
EXTRA_SECOND = {"talk", "nod", "wave", "side_idle", "side_coffee", "side_read", "pin"}


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


def passing(cell: np.ndarray, bb):
    """The passing step of a side-on stride: the legs drawn in towards the
    hips, more the lower they go, so the feet come together under the body."""
    x0, y0, x1, y1 = bb
    fig = cell[y0:y1, x0:x1]
    h, w = fig.shape[:2]
    top = int(h * (1 - LEGS))
    out = fig.copy()
    cols = np.nonzero(fig[top - 1, :, 3] > 0)[0]
    cx = (cols.min() + cols.max()) / 2 if len(cols) else w / 2
    xs = np.arange(w)
    for y in range(top, h):
        k = 1 - (1 - PASS_SQUEEZE) * ((y - top) / max(1, h - 1 - top)) ** 0.8
        src = np.rint(cx + (xs - cx) / k).astype(int)
        ok = (src >= 0) & (src < w)
        row = np.zeros_like(fig[y])
        row[ok] = fig[y, src[ok]]
        out[y] = row
    return out, bbox(out)


def _clean(cell: np.ndarray, speck: int = 300) -> np.ndarray:
    """Background left in a gap the keying can't reach (between hair and coat)
    goes, and so do loose specks away from the figure."""
    cell = cell.copy()
    a = cell[..., :3].astype(np.int16)
    pink = (a[..., 0] - a[..., 1] > 45) & (a[..., 2] - a[..., 1] > 25) & (np.abs(a[..., 0] - a[..., 2]) < 120)
    cell[pink, 3] = 0
    mask = cell[..., 3] > 0
    h, w = mask.shape
    seen = np.zeros_like(mask)
    for y0, x0 in np.argwhere(mask):
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
        if len(piece) <= speck:
            ys, xs = zip(*piece)
            cell[list(ys), list(xs), 3] = 0
    return cell


def side_walks(art: str):
    """Each character's drawn side-on walk, by role, in SIDE_ORDER; empty
    when the sheets aren't there."""
    out = {}
    for n, roles in enumerate(SIDE_WALKS, start=1):
        path = os.path.join(art, f"walk_{n}.png")
        if not os.path.isfile(path):
            return {}
        frames = []
        for cell, _ in sheet_frames(path, 4, 3):
            cell = _clean(cell)
            frames.append((cell, bbox(cell)))
        for r, role in enumerate(roles):
            out[role] = frames[r * 4:(r + 1) * 4]
    return out


def put_side_walk(atlas: Image.Image, drawn, stride_h: float) -> None:
    """Swap the side walk in an atlas for the drawn four steps, sized so the
    strides stand as tall as the ones they replace."""
    scale = stride_h / standing_height(drawn, {0, 2})
    names = [f[0] for f in FRAMES]
    for name, (cell, bb) in zip(SIDE_ORDER, drawn):
        i = names.index(name)
        # Lined up on the body, not the box: a stride is wider than a passing
        # step, and centring each box would rock the figure back and forth.
        x0, y0, x1, y1 = bb
        top = cell[y0:(y0 + y1) // 2, x0:x1, 3] > 0
        body = float(np.nonzero(top)[1].mean()) if top.any() else (x1 - x0) / 2
        dx = round(((x1 - x0) / 2 - body) * scale)
        atlas.paste((0, 0, 0, 0), (i * CELL_W, 0, (i + 1) * CELL_W, CELL_H))
        atlas.alpha_composite(place(cell, bb, scale), (i * CELL_W + dx, 0))


def frame_of(frames, idx):
    """A frame by its source cell; a negative index is the passing step made
    from that stride."""
    if idx >= 0:
        return frames[idx]
    cell, bb = frames[-idx]
    return passing(cell, bb) if bb else (cell, bb)


def extra_main(art: str, target: float, sheet: str = "17.png", keep=None):
    """A main character's further poses (two rows of six), each in a cell of
    its own, at the scale of the standing frames. `keep` names the ones to use."""
    path = os.path.join(art, sheet)
    if not os.path.isfile(path):
        return []
    frames = [f for band in rows_of(key_magenta(Image.open(path)), 2) for f in strip_frames(band, 6)]
    # The side-on figures were drawn a little larger than the front-on ones.
    # Each group is brought to the same standing height, or the character
    # seems to grow when it turns.
    front = target / standing_height(frames, {3, 4})
    side = target / standing_height(frames, {6, 7, 8})
    return [place(cell, bb, side if name.startswith("side_") else front)
            for name, (cell, bb) in zip(EXTRA_MAIN, frames) if keep is None or name in keep]


def second_main(art: str):
    """The second main character (18.png): the fourteen frames in one row. His
    typing frame was drawn with a chair, and the room brings its own, so it is
    made from his back view instead: head to hips, the rest is behind the chair."""
    path = os.path.join(art, "18.png")
    if not os.path.isfile(path):
        return None
    frames = strip_frames(key_magenta(Image.open(path)), len(FRAMES) - 2)
    names = [f[0] for f in FRAMES]
    # His walk was drawn direction by direction (down, down, up, up, side,
    # side); everyone else's goes step by step (down, up, side, then again).
    drawn = ["walk_down_1", "walk_down_2", "walk_up_1", "walk_up_2", "walk_side_1", "walk_side_2"]
    walk = {name: frames[8 + k] for k, name in enumerate(drawn)}
    frames = [walk.get(n, f) for n, f in zip(names, frames)]
    frames += [passing(*walk["walk_side_1"]), passing(*walk["walk_side_2"])]
    scale = STAND_H * 1.06 / standing_height(frames, {0, 4, 5, 6, 7})
    drawn = side_walks(art).get("main_m")
    atlas = Image.new("RGBA", (CELL_W * len(FRAMES), CELL_H), (0, 0, 0, 0))
    for i, (cell, bb) in enumerate(frames):
        if names[i] == "type":
            cell, bb = frames[names.index("gaze")]
            x0, y0, x1, y1 = bb
            bb = (x0, y0, x1, y0 + round((y1 - y0) * 0.62))
        atlas.alpha_composite(place(cell, bb, scale), (i * CELL_W, 0))
    if drawn:
        x0, y0, x1, y1 = walk["walk_side_1"][1]
        put_side_walk(atlas, drawn, (y1 - y0) * scale)
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
    drawn = side_walks(art)
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
            cell, bb = frame_of(sheets[sheet], idx)
            if not bb:
                continue
            atlas.alpha_composite(place(cell, bb, target / ref[sheet]), (i * CELL_W, 0))
        if role in drawn:
            x0, y0, x1, y1 = sheets["walk"][2][1]
            put_side_walk(atlas, drawn[role], (y1 - y0) * target / ref["walk"])
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
        more = extra_main(art, STAND_H * 1.06, "20.png", EXTRA_SECOND)
        if more:
            wide = Image.new("RGBA", (second.width + CELL_W * len(more), CELL_H), (0, 0, 0, 0))
            wide.alpha_composite(second, (0, 0))
            for i, im in enumerate(more):
                wide.alpha_composite(im, (second.width + i * CELL_W, 0))
            second = wide
            manifest.setdefault("extra", {})["main_m"] = [n for n in EXTRA_MAIN if n in EXTRA_SECOND]
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
    manifest["rooms"] = [{"id": "cabin", "name": "Cabin", "file": "room.png"}]
    for rid, name, src in MORE_ROOMS:
        path = os.path.join(art, src)
        if not os.path.isfile(path):
            continue
        im = Image.open(path).convert("RGB").resize(room.size, Image.LANCZOS)
        im.quantize(128, method=Image.MEDIANCUT, dither=Image.NONE).save(os.path.join(OUT, f"room_{rid}.png"), optimize=True)
        manifest["rooms"].append({"id": rid, "name": name, "file": f"room_{rid}.png"})
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
