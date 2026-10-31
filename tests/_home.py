"""Imported first by a test that starts the terminal, the window's server or a
bridge: a temporary home and workspace, so nothing it starts can read or change
the real ~/.hearth or workspace. A workspace the test sets afterwards, under
either name, still wins."""
import os
import tempfile

HOME = tempfile.mkdtemp(prefix="inglen-test-home-")
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_TEST_HOME="1")
os.environ.pop("HEARTH_KEYS_SEALED", None)       # a test run never seals: see keystore.may_seal
_tmp = os.path.normcase(os.path.realpath(tempfile.gettempdir()))


def _is_temp(p: str) -> bool:
    return bool(p) and os.path.normcase(os.path.realpath(p)).startswith(_tmp)


if not _is_temp(os.environ.get("HEARTH_WORKSPACE", "")):
    os.environ.pop("HEARTH_WORKSPACE", None)
if not _is_temp(os.environ.get("JARVIS_WORKSPACE", "")):
    WORKSPACE = os.path.join(HOME, "Inglen")
    os.makedirs(WORKSPACE, exist_ok=True)
    os.environ["JARVIS_WORKSPACE"] = WORKSPACE
WORKSPACE = os.environ.get("HEARTH_WORKSPACE") or os.environ["JARVIS_WORKSPACE"]
