"""Someone who speaks Hindi is never listened to with an English-only model.

base.en, the default, holds no Hindi at all: what was said came back as English
words that sounded like it. A language named in Settings, a Hindi voice, or
Hindi script in the newest chats each mean a multilingual model, here and when
the card is short of room. Short Hindi detected as Urdu (or noise as Georgian)
is written again in Hindi. Stand-ins for the recorder and the card.

    .\\.venv\\Scripts\\python.exe tests\\test_hindi_listening.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types

WORK = tempfile.mkdtemp(prefix="hearth-hindi-listen-")
os.environ.update(JARVIS_WORKSPACE=WORK, HEARTH_WORKSPACE=WORK, HEARTH_NO_ACTIVITY_LOG="1")
for k in ("HEARTH_REALTIME_MODEL", "HEARTH_REALTIME_LANG"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import env as _env, listen, realtime_voice as rv  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


sys.modules["ctranslate2"] = types.SimpleNamespace(get_cuda_device_count=lambda: 1, __file__=__file__)
rv._cudnn_present = lambda: True
import hearth.llmserver as ls  # noqa: E402
ls.detect_gpu_vram_free_gb = lambda: 350 / 1024.0          # a local model fills the card

convos = os.path.join(WORK, "conversations")
os.makedirs(convos, exist_ok=True)


def chats(*messages):
    for fn in os.listdir(convos):
        os.remove(os.path.join(convos, fn))
    with open(os.path.join(convos, "c_1.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "c_1", "messages": [{"role": r, "content": c} for r, c in messages]}, f, ensure_ascii=False)
    getattr(listen, "_recent", {}).update(at=0.0)


def setup(model="base.en", lang="", voice="af_heart", device="cuda"):
    _env.set("STT_MODEL", model)
    _env.set("STT_LANGUAGE", lang)
    _env.set("VOICE", voice)
    _env.set("STT_DEVICE", device)


chats(("user", "what's the weather"), ("assistant", "Sunny in Delhi."))
setup()
check(f"English chats, English voice: base.en stays ({rv._model_size()})", rv._model_size() == "base.en")

chats(("user", "can you talk to me in Hindi"), ("assistant", "हाँ, बिल्कुल। आज मौसम साफ़ है।"))
setup()
check(f"Hindi in a recent chat: the multilingual model ({rv._model_size()})", rv._model_size() == "small")
check(f"push-to-talk too ({listen.model_size()})", listen.model_size() == "small")
p = rv.stt_plan()
check(f"short of room on the card: small on the processor, the least that writes Hindi ({p})",
      p["device"] == "cpu" and p["model"] == "small")
check(f"and the caption model isn't .en either ({rv.caption_model(p['model'])})",
      not rv.caption_model(p["model"]).endswith(".en"))
check(f"Hindi or English may be heard ({rv._allowed_languages()})", rv._allowed_languages() == ["hi", "en"])

chats(("user", "hello"))
setup(voice="hf_alpha")
check(f"a Hindi voice picked: the multilingual model ({rv._model_size()})", rv._model_size() == "small")
setup(lang="hi")
check(f"Hindi named in Settings: the multilingual model, in Hindi ({rv._model_size()}, {rv._language()})",
      rv._model_size() == "small" and rv._language() == "hi")
setup(model="small", lang="en")
check(f"English named: English on the processor ({rv.stt_plan()['model']})", rv.stt_plan()["model"] == "base.en")


class Rec:
    def __init__(self, detected):
        self.detected_language = detected
        self.last_transcription_bytes = b"audio"
        self.language = ""
        self.redone_in = None

    def perform_final_transcription(self, audio):
        self.redone_in = self.language
        return "मुझे कल याद दिलाना"


chats(("assistant", "ठीक है"))
setup(model="small")
r = Rec("ur")
out = rv._in_their_language(r, "مجھے کل یاد دلانا")
check(f"heard as Urdu: written again in Hindi ({out!r}, {r.redone_in})", out == "मुझे कल याद दिलाना" and r.redone_in == "hi")
check("and the recorder's language is put back", r.language == "")
r = Rec("en")
check("heard as English: left alone", rv._in_their_language(r, "remind me tomorrow") == "remind me tomorrow"
      and r.redone_in is None)
setup(model="small", lang="hi")
r = Rec("ur")
check("Hindi forced already: nothing to redo", rv._in_their_language(r, "x") == "x" and r.redone_in is None)

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall hindi listening checks passed")
