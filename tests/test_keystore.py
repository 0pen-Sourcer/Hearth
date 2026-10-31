"""Saved keys are sealed: settings.json and the ~/.hearth files hold no plain
secret after the first start, and the window, the terminal, the bridges and
Plus still read them.

    .\\.venv\\Scripts\\python.exe tests\\test_keystore.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REAL_HOME = os.path.expanduser("~")       # only ever compared with, never opened
home = tempfile.mkdtemp(prefix="hearth-ks-home-")
work = os.path.join(home, "Jarvis")
os.makedirs(os.path.join(home, ".hearth"))
os.makedirs(work)
os.environ.update(HOME=home, USERPROFILE=home, JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1", HEARTH_NO_ONBOARDING="1",
                  HEARTH_SECRETS_DIR=os.path.join(home, "secret-dir"))
for k in ("LOCAL_API_KEY", "OPENAI_API_KEY", "LOCAL_API_BASE", "HEARTH_KEYSTORE"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ANT = "sk-ant-STANDIN0000000000000000000000"
XAI = "xai-STANDIN000000000000000000000000"
BOT = "123456789:STANDINSTANDINSTANDINSTANDINSTAND"
LIC = "LIC-STANDIN-0000-1111"
PWD = "standin-mail-password"
PCK = "c3RhbmRpbi1wYy1rZXktc3RhbmRpbi1wYy1rZXk="
OLD = "gsk_STANDINOLDSTANDINOLDSTANDINOLD"


def write(path, d):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)


def raw(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


settings = os.path.join(work, "settings.json")
hh = os.path.join(home, ".hearth")
write(settings, {"llm_provider": "anthropic", "llm_url": "https://api.anthropic.com/v1/", "llm_key": ANT,
                 "provider_keys": {"anthropic": ANT, "grok": XAI}, "approval_mode": "auto", "onboarded": True})
write(os.path.join(work, "brain_keys.json"), {"groq": {"url": "https://api.groq.com/openai/v1", "key": OLD}})
write(os.path.join(hh, "phone_bridge.json"), {"bot_token": BOT, "allowed_chat_ids": [1]})
write(os.path.join(hh, "plus.json"), {"key": LIC, "instance": "i-1"})
write(os.path.join(hh, "email.json"), {"address": "a@example.com", "password": PWD})
write(os.path.join(hh, "relay.json"), {"pc_key": PCK, "phones": []})

from hearth import keystore  # noqa: E402

changed = keystore.migrate()
check(f"first start seals every file that held a key ({len(changed)} files)", len(changed) == 6)
every = [settings, os.path.join(work, "brain_keys.json")] + [os.path.join(hh, n) for n in
                                                               ("phone_bridge.json", "plus.json", "email.json", "relay.json")]
leaks = [os.path.basename(p) for p in every for v in (ANT, XAI, BOT, LIC, PWD, PCK, OLD) if v in raw(p)]
check(f"no saved key is left as plain text ({leaks})", not leaks)
check("settings that aren't keys stay readable as they were", json.loads(raw(settings))["approval_mode"] == "auto")
check("a second start changes nothing", keystore.migrate() == [])

from hearth import web  # noqa: E402
s = web._load_settings()
check("the window reads the key in use", s.get("llm_key") == ANT)
check("the window reads every provider's key", (s.get("provider_keys") or {}).get("grok") == XAI)
check("a key the terminal saved earlier still works", web._provider_key("groq") == OLD)

NEW = "sk-ant-STANDINNEW00000000000000000000"
web._save_settings({"llm_key": NEW, "provider_keys": {"anthropic": NEW, "grok": XAI}})
check("saving a key in the window keeps it sealed", NEW not in raw(settings) and XAI not in raw(settings))
check("and it reads back", web._load_settings().get("llm_key") == NEW)

from hearth import headless  # noqa: E402
check("the headless engine reads the sealed key", headless._brain_from_settings().get("key") == NEW)

import hearth_cli  # noqa: E402
check("the terminal reads the sealed key", hearth_cli._read_brain_key("anthropic") == NEW)
check("the terminal reads its own older key", hearth_cli._read_brain_key("groq") == OLD)

from hearth import channel_core, plus, email_tools, relay_bridge  # noqa: E402
check("a bridge reads its bot token", channel_core.config_of("telegram").get("bot_token") == BOT)
check("Plus reads the licence key", plus._read().get("key") == LIC)
check("mail reads its password", email_tools._config().get("password") == PWD)
check("the relay reads the PC's key", relay_bridge._read().get("pc_key") == PCK)
relay_bridge._write(dict(relay_bridge._read(), phones=["p"]))
check("the relay's own save keeps its key sealed", PCK not in raw(os.path.join(hh, "relay.json")))

vals = keystore.known_values()
check("every saved secret is known for redaction", {NEW, XAI, BOT, LIC, PWD, PCK, OLD} <= vals)

# Where DPAPI isn't there: a 0600 file outside the workspace.
os.environ["HEARTH_KEYSTORE"] = "file"
sealed = keystore.seal("file-standin-value", "t:1")
check("the file store keeps only a pointer in the file it belongs to", sealed == "sealed:file:t:1")
store = keystore.store_path()
check("the file store is outside the workspace", not os.path.abspath(store).startswith(os.path.abspath(work)))
check("and opens again", keystore.unseal(sealed) == "file-standin-value")
if os.name == "posix":
    check("and only its owner can read it", (os.stat(store).st_mode & 0o777) == 0o600)
os.environ.pop("HEARTH_KEYSTORE")

# From a test run (a temp workspace), migrate() never reaches the real home. Reads
# and writes are stood in for, so nothing real is opened even if this broke.
reads, writes = [], []
_orig = (keystore._read_raw, keystore._write_raw, os.path.isfile)
keystore._read_raw = lambda p: reads.append(p) or {f: "standin-plain-value" for f in (
    "llm_key", "bot_token", "key", "password", "pc_key", "provider_keys")}
keystore._write_raw = lambda p, d, indent=2: writes.append((p, d))
os.path.isfile = lambda p: True
real_ws = os.path.join(REAL_HOME, "Jarvis")
saved_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "HEARTH_WORKSPACE", "JARVIS_WORKSPACE",
                                             "HEARTH_TEST_HOME", "HEARTH_KEYS_SEALED")}


def to_real(test_mark, workspace):
    for k in ("HOME", "USERPROFILE"):
        os.environ[k] = REAL_HOME
    for k in ("HEARTH_WORKSPACE", "JARVIS_WORKSPACE"):
        os.environ[k] = workspace
    os.environ.pop("HEARTH_KEYS_SEALED", None)
    if test_mark:
        os.environ["HEARTH_TEST_HOME"] = "1"
    else:
        os.environ.pop("HEARTH_TEST_HOME", None)


def real_writes():
    return [p for p, _ in writes if not keystore._under(p, tempfile.gettempdir())]


try:
    # Every way a test can reach the real home and workspace: with or without the
    # test mark, a temp workspace or the real one. None seals, none writes there.
    for mark, ws_ in ((True, work), (False, work), (True, real_ws), (False, real_ws)):
        to_real(mark, ws_)
        del writes[:]
        keystore.migrate()
        check(f"migrate() leaves the real home and workspace alone (test mark {mark}, "
              f"{'real' if ws_ == real_ws else 'temp'} workspace): {real_writes()}", not real_writes())
        del writes[:]
        keystore.write_json(os.path.join(REAL_HOME, ".hearth", "plus.json"), {"key": "standin"})
        check("and a save there seals nothing", writes and writes[0][1].get("key") == "standin")
    # Only Inglen's own start turns sealing on, and only outside a test run.
    to_real(True, real_ws)
    del writes[:]
    keystore.app_start()
    check("app_start() under the test mark still seals nothing real", not real_writes()
          and os.environ.get("HEARTH_KEYS_SEALED") is None)
    to_real(False, real_ws)
    del writes[:]
    keystore.app_start()
    check("app_start() as Inglen itself seals the install's files (stand-in writes)",
          real_writes() and all("sealed:" in repr(d) for p, d in writes))
finally:
    keystore._read_raw, keystore._write_raw, os.path.isfile = _orig
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

# The window's server, started the way tests start it, never migrates.
called = []
_mig, _app = keystore.migrate, keystore.app_start
keystore.migrate = lambda: called.append("migrate") or []
keystore.app_start = lambda: called.append("app_start") or []
from hearth import web as _web  # noqa: E402
_srv = _web.serve("127.0.0.1", 0)
_srv.shutdown()
keystore.migrate, keystore.app_start = _mig, _app
check(f"starting the window's server doesn't seal anything by itself ({called})", not called)

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall keystore checks passed")
