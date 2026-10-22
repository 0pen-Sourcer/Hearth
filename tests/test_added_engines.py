"""Other builds of llama.cpp, for models the official build can't read.

Some models are made for a fork of llama.cpp (their own packing or model
family), and the official build refuses them with "invalid ggml type". Hearth
had no way to use any other build. Now a build can be added, by its GitHub page
or a folder, and when the official one can't read a file Hearth tries the
added ones and remembers which one read it. No build is named anywhere.

Uses a throwaway home folder and a stand-in for starting the server.

    .\\.venv\\Scripts\\python.exe tests\\test_added_engines.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="hearth-engines-")
os.environ["USERPROFILE"] = os.environ["HOME"] = HOME
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import llmserver as L                                   # noqa: E402

if not os.path.expanduser("~").lower().startswith(HOME.lower()):
    sys.exit("refusing to run: home is not the throwaway folder")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def engine(folder):
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    (d / L.SERVER_EXE).write_bytes(b"MZ" + b"\0" * (L._MIN_SERVER_EXE_BYTES + 10))
    return d


engine(Path(HOME, ".hearth", "llamacpp", "b10488"))
engine(Path(HOME, ".hearth", "llamacpp", "Some-Fork@v1"))
mine = engine(Path(HOME, "my-builds", "other-llama"))
L.reset_native_cache()
check("an added build never becomes the engine for every model",
      L.find_native_llama_server()["label"] == "b10488" and L.installed_llama_tags() == ["b10488"])
check("a folder without the server is refused", not L.add_engine_folder(os.path.join(HOME, "nothing"))["ok"])
added = L.add_engine_folder(str(mine))
check(f"a folder with one is added {added}", added["ok"] and added["name"] == "other-llama")
check(f"both added builds are listed {[e['label'] for e in L.added_engines()]}",
      {e["source"] for e in L.added_engines()} == {"github", "folder"})

MODEL = os.path.join(HOME, "fork-only.gguf")
Path(MODEL).write_bytes(b"GGUF")
calls = []
_real = L._start_builtin


def fake(model_path, *a, engine=None, **k):
    eng = engine or L.engine_for(model_path)
    calls.append(eng["label"] if eng else "official")
    if eng and eng["source"] == "folder":
        return {"ok": True, "url": "http://127.0.0.1:1234/v1"}
    return {"ok": False, "error": "server exited (code 1) before responding",
            "log_tail": "gguf_init_from_file_impl: tensor 'blk.0.w' has invalid ggml type 142"}


L._start_builtin = fake
r = L.start_builtin(MODEL)
check(f"when the official build can't read a model, the added builds are tried until one does {calls} {r}",
      r.get("ok") and calls[0] == "official" and calls[-1] == "other-llama" and r.get("engine") == "other-llama")
calls.clear()
r = L.start_builtin(MODEL)
check(f"and the next load goes straight to the one that read it {calls}", r.get("ok") and calls == ["other-llama"])

calls.clear()
L._start_builtin = lambda m, *a, engine=None, **k: (calls.append("x") or
                                                   {"ok": False, "log_tail": "CUDA error: out of memory"})
r = L.start_builtin(os.path.join(HOME, "big.gguf"))
check(f"a failure that isn't about reading the file doesn't try other builds {calls}", calls == ["x"])
L._start_builtin = _real

hint = L.load_failure_hint("tensor has invalid ggml type 142")
check(f"the message for an unreadable file names no build {hint!r}",
      "different build of llama.cpp" in hint and "Add an engine" in hint and "Prism" not in hint)

check("a GitHub page or owner/name is read as a repository",
      L._repo_of("https://github.com/Some-Fork/llama.cpp/releases") == "Some-Fork/llama.cpp"
      and L._repo_of("Some-Fork/llama.cpp") == "Some-Fork/llama.cpp" and L._repo_of(r"C:\builds\x") == "")

# Settings named the official engine while an added build was running the model.
class _Alive:
    def poll(self):
        return None
L._proc, L._proc_info = _Alive(), {"exe": str(mine / L.SERVER_EXE), "model_path": MODEL}
info = L.llama_runtime_info()
L._proc, L._proc_info = None, {}
check(f"the engine card names the added build when it's the one running {info.get('running')}",
      (info.get("running") or {}).get("label") == "other-llama" and info["running"]["model"] == "fork-only.gguf")
check("and names none when the official engine runs", L.llama_runtime_info().get("running") is None)

removed = L.remove_added_engine(str(mine / L.SERVER_EXE))
check("removing a folder build takes it off the list and leaves the folder alone",
      removed["ok"] and (mine / L.SERVER_EXE).is_file()
      and [e["source"] for e in L.added_engines()] == ["github"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all added engine checks passed")
