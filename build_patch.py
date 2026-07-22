"""Build the small code-only update archive published beside the installers.

The installers are ~1 GB because of the CUDA / llama.cpp / onnxruntime payload,
which is identical between Hearth releases. Only Hearth's own code changes, and
that is a few megabytes, so shipping it on its own means an update is a quick
download instead of a full reinstall.

    .\\.venv\\Scripts\\python.exe build_patch.py

Writes Output/hearth-code-<version>.zip laid out to extract directly over a
packaged install's _internal directory. Upload it as a release asset next to the
installers; the in-app updater picks it up by name.

Only usable by builds that ship their .py loose (see _unfreeze_hearth in
Hearth.spec). Older installs have the code frozen inside the exe and need one
full install to move onto the patchable layout.
"""
from __future__ import annotations

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from hearth.updater import HEARTH_VERSION  # noqa: E402


def main() -> int:
    out_dir = os.path.join(ROOT, "Output")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"hearth-code-{HEARTH_VERSION}.zip")

    # Exactly the files the packaged app loads from disk. ui.html is included
    # because the GUI is a real part of the code layer, and hearth_cli.py sits
    # at the bundle root rather than inside the package.
    members: list[tuple[str, str]] = []
    hearth_dir = os.path.join(ROOT, "hearth")
    for name in sorted(os.listdir(hearth_dir)):
        if name.endswith(".py") or name == "ui.html":
            members.append((os.path.join(hearth_dir, name), f"hearth/{name}"))
    cli = os.path.join(ROOT, "hearth_cli.py")
    if os.path.isfile(cli):
        members.append((cli, "hearth_cli.py"))

    if not members:
        print("nothing to package", file=sys.stderr)
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for src, arc in members:
            z.write(src, arc)

    size = os.path.getsize(out)
    print(f"{out}")
    print(f"  {len(members)} files, {size / 1e6:.2f} MB  (version {HEARTH_VERSION})")
    print("  upload beside the installers; the in-app updater finds it by name")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
