"""Build the Team room's sprite atlases from the source art.

The source is generated pixel art on a magenta background: pose sheets (2x2),
action sheets (2x2) and walk sheets (3x2), one of each per role, plus the room.
Each frame is keyed, cropped, scaled so every role stands the same height
against the desks, anchored on the feet, and colour-reduced so it stays crisp
at room size. Output goes to assets/team/: one atlas per role, the room, and
a manifest the window reads.

    python scripts/build_team_sprites.py <art folder>

The art folder holds bg.png, 1-8.png (actions), 9-16.png (walks) and
old/1-8.png (poses), in role order, and optionally bg_props.png, whose lower
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
STAND_H = 118          # standing height in room pixels (the room is 1536 wide)
CELL_W, CELL_H = 104, 144
COLORS = 40
# Furniture, in grid order, with its width in room pixels (measured from the
# room the characters were drawn against, so everything shares one scale).
PROPS = [("desk", 233), ("desk_on", 233), ("chair", 80), ("armchair", 145), ("side_table", 60), ("mug", 22)]


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
        quantize(atlas).save(os.path.join(OUT, f"{role}.png"), optimize=True)
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
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("built", len(ROLES), "atlases in", OUT)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(sys.argv[1])
