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

# The new voice stack v0.7.5 adds, pulled from the built dist so they match the
# installer exactly. These now ship LOOSE in _internal (see _unfreeze_hearth +
# the loose-dep loop in Hearth.spec), so the patch can deliver working voice to an
# older install instead of forcing a full reinstall. torch is a big loose tree;
# scipy/halo/spinners/_soundfile_data are dirs; soundfile is a single file.
_BINARY_INCLUDES = ["torch", "scipy", "halo", "spinners",
                    "_soundfile_data", "soundfile.py"]
# Pure-Python packages the voice recorder imports that PyInstaller freezes into
# the exe rather than shipping loose, so they are absent from the dist tree and
# from any base install that never had torch. Sourced straight from the venv and
# dropped loose so a patched older install can resolve them. torchgen ships inside
# the torch wheel; both are import-only, no compiled files.
_VENV_PURE_INCLUDES = ["torchgen", "typing_extensions"]
# build_release.ps1 moves the Full bundle to dist_full; a plain build lands in
# dist. Prefer the Full edition (the patch's binaries are identical across
# editions, so either works).
_DIST_CANDIDATES = [
    os.path.join(ROOT, "dist_full", "Hearth", "_internal"),
    os.path.join(ROOT, "dist", "Hearth", "_internal"),
]
_DIST_INTERNAL = next((d for d in _DIST_CANDIDATES if os.path.isdir(d)),
                      _DIST_CANDIDATES[0])


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

    code_count = len(members)

    # Bundled-dependency additions for this release, pulled from the built dist so
    # they are the exact files the installer ships (right DLLs, right layout).
    bin_count = 0
    bin_bytes = 0
    if _BINARY_INCLUDES:
        if not os.path.isdir(_DIST_INTERNAL):
            print(f"WARNING: {_DIST_INTERNAL} not found. Run the full build first,\n"
                  f"         or the patch will ship WITHOUT {', '.join(_BINARY_INCLUDES)} "
                  f"(voice stays broken on a patched Full install).", file=sys.stderr)
        else:
            for sub in _BINARY_INCLUDES:
                base = os.path.join(_DIST_INTERNAL, sub)
                if os.path.isfile(base):                    # single file (soundfile.py)
                    arc = os.path.relpath(base, _DIST_INTERNAL).replace(os.sep, "/")
                    members.append((base, arc))
                    bin_count += 1
                    bin_bytes += os.path.getsize(base)
                    continue
                if not os.path.isdir(base):
                    print(f"WARNING: '{sub}' not in the build at {base} — skipped.", file=sys.stderr)
                    continue
                for dirpath, _dirs, files in os.walk(base):
                    for f in files:
                        src = os.path.join(dirpath, f)
                        arc = os.path.relpath(src, _DIST_INTERNAL).replace(os.sep, "/")
                        members.append((src, arc))
                        bin_count += 1
                        bin_bytes += os.path.getsize(src)

    # Pure-Python voice deps that are frozen into the exe (not in the dist tree),
    # sourced from the venv so a patched older install can import them.
    import importlib.util as _ilu
    for _mod in _VENV_PURE_INCLUDES:
        try:
            _spec = _ilu.find_spec(_mod)
        except Exception:
            _spec = None
        if _spec is None:
            print(f"WARNING: '{_mod}' not importable in this venv - skipped.", file=sys.stderr)
            continue
        if _spec.submodule_search_locations:                # package dir
            _pkg = list(_spec.submodule_search_locations)[0]
            for dirpath, _dirs, files in os.walk(_pkg):
                if "__pycache__" in dirpath:
                    continue
                for f in files:
                    if f.endswith((".pyc", ".pyo")):
                        continue
                    src = os.path.join(dirpath, f)
                    rel = os.path.relpath(src, os.path.dirname(_pkg))
                    members.append((src, rel.replace(os.sep, "/")))
                    bin_count += 1
                    bin_bytes += os.path.getsize(src)
        elif _spec.origin and os.path.isfile(_spec.origin):  # single module
            members.append((_spec.origin, os.path.basename(_spec.origin)))
            bin_count += 1
            bin_bytes += os.path.getsize(_spec.origin)

    if not members:
        print("nothing to package", file=sys.stderr)
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, arc in members:
            z.write(src, arc)

    size = os.path.getsize(out)
    print(f"{out}")
    print(f"  code: {code_count} files")
    if bin_count:
        print(f"  bundled deps ({', '.join(_BINARY_INCLUDES)}): "
              f"{bin_count} files, {bin_bytes / 1e6:.0f} MB uncompressed")
    print(f"  patch zip: {size / 1e6:.1f} MB  (version {HEARTH_VERSION})")
    if _BINARY_INCLUDES and not bin_count:
        print("  NOTE: no bundled deps in this patch — build the full dist first.")
    print("  upload beside the installers; the in-app updater finds it by name")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
