"""Hearth voice input (STT).

Built on faster-whisper — CTranslate2 backend, real-time on CPU, ~150MB
for the base.en model (auto-downloaded on first use via HuggingFace).

One-shot mode:
    text = listen.listen_once()        # record until silence, transcribe

Continuous mode:
    listen.start_continuous(callback)  # background thread
    listen.stop_continuous()

Continuous mode is interrupt-aware:
    - While the assistant is speaking (voice.is_speaking()), the listener watches
      mic RMS. If sustained speech is detected → voice.stop() is called to
      mute TTS, then we record/transcribe the new input.
    - Between turns, listener waits for any speech then records.

The first call lazily loads the model. Module is import-safe even when
faster-whisper isn't installed — is_available() returns False.

ONE-TIME SETUP:
    pip install faster-whisper sounddevice numpy
"""

from __future__ import annotations

import os
from . import env as _env
import re
import sys
import time
import threading
from queue import Queue, Empty
from typing import Optional, Callable

# Env-var-based suppression of huggingface_hub warnings lives in hearth/__init__.py
# (must run before any HF import, which means before listen.py is imported).
# Kept here as a no-op fallback in case someone imports listen.py directly.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from .tools import WORKSPACE
from . import voice as _voice

VOICES_DIR = os.path.join(WORKSPACE, "voices")
# faster-whisper model size: base.en (~150MB) is the sweet spot for speed/quality
# on CPU. Override via env if user has a beefier setup.
MODEL_SIZE = _env.get("STT_MODEL", "base.en")
COMPUTE_TYPE = _env.get("STT_COMPUTE", "int8")  # CPU-friendly
# Empty means let the model decide from the audio. A fixed code here was
# forcing English on every utterance, so a multilingual model handed back a
# rough English rendering of whatever was said rather than the words.
LANGUAGE = _env.get("STT_LANGUAGE", "").strip()


def device_rows() -> tuple:
    """([(index, name, input_channels, hostapi)], default_input_index) from a
    fresh PyAudio instance. PyAudio enumerates devices each time it is created,
    so a headset plugged in after launch is there; the sounddevice table is a
    snapshot from startup that cannot be refreshed while a stream is open.
    Falls back to sounddevice when PyAudio is not installed."""
    try:
        import pyaudio  # type: ignore
        pa = pyaudio.PyAudio()
        try:
            rows = []
            for i in range(pa.get_device_count()):
                try:
                    d = pa.get_device_info_by_index(i)
                    api = pa.get_host_api_info_by_index(d["hostApi"]).get("name", "")
                    rows.append((i, str(d.get("name", "")), int(d.get("maxInputChannels", 0)), api))
                except Exception:
                    continue
            try:
                default = int(pa.get_default_input_device_info()["index"])
            except Exception:
                default = None
            return rows, default
        finally:
            pa.terminate()
    except Exception:
        pass
    try:
        import sounddevice as sd  # type: ignore
        apis = [a.get("name", "") for a in sd.query_hostapis()]
        rows = [(i, str(d.get("name", "")), int(d.get("max_input_channels", 0)),
                 apis[d["hostapi"]] if d.get("hostapi") is not None else "")
                for i, d in enumerate(sd.query_devices())]
        try:
            default = sd.default.device[0]
        except Exception:
            default = None
        return rows, default
    except Exception:
        return [], None


def list_input_devices() -> list:
    """[{index, name, default}] of real microphones for the mic picker, from the
    current device table. Skips raw kernel-streaming duplicates and loopback
    capture of the speakers, and lists each mic once under its fullest name."""
    rows, default_in = device_rows()
    by_pfx: dict = {}
    wdmks_only = []
    for i, name, ch, api in rows:
        name = name.strip()
        if ch <= 0 or not name:
            continue
        low = name.lower()
        if "@system32" in low or "bthhfenum.sys" in low or ("%1" in name and "%0" in name):
            continue
        try:
            if is_system_audio_device(name):
                continue
        except Exception:
            pass
        is_wdmks = "wdm-ks" in (api or "").lower() or "kernel streaming" in (api or "").lower()
        is_def = (i == default_in)
        pfx = low[:18]
        cur = by_pfx.get(pfx)
        if cur is None:
            if is_wdmks:
                wdmks_only.append({"index": i, "name": name, "default": is_def, "pfx": pfx})
            else:
                by_pfx[pfx] = {"index": i, "name": name, "default": is_def}
        else:
            if len(name) > len(cur["name"]):
                cur["name"] = name
                if not cur["default"]:
                    cur["index"] = i
            if is_def:
                cur["index"] = i
                cur["default"] = True
    for w in wdmks_only:
        if w["pfx"] not in by_pfx:
            by_pfx[w["pfx"]] = {"index": w["index"], "name": w["name"], "default": w["default"]}
    return list(by_pfx.values())


class MicInput:
    """A mic opened through PyAudio, read like a sounddevice stream:
    read(n) -> (float32 array of shape (n, 1), overflowed)."""

    def __init__(self, index, rate: int, block: int):
        import pyaudio  # type: ignore
        self._pa = pyaudio.PyAudio()
        try:
            self._st = self._pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                                     input_device_index=index, frames_per_buffer=block)
        except Exception:
            self._pa.terminate()
            raise

    def read(self, n: int):
        import numpy as np  # type: ignore
        raw = self._st.read(n, exception_on_overflow=False)
        return (np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0).reshape(-1, 1), False

    def stop(self) -> None:
        try:
            self._st.stop_stream()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._st.close()
        finally:
            self._pa.terminate()


def rescan_devices() -> list:
    """The mic list, freshly enumerated (see device_rows)."""
    return list_input_devices()


_MIC_METER_CODE = (
    "import os, sys, json, time\n"
    "try:\n"
    "    import sounddevice as sd, numpy as np, queue\n"
    "    try: sd._terminate(); sd._initialize()\n"     # fresh PortAudio -> sees a just-plugged mic
    "    except Exception: pass\n"
    "    import hearth.listen as L\n"
    "    idx=L.input_device_index()\n"          # the picked mic, by name, in this table

    "    q=queue.Queue()\n"
    "    def cb(indata, frames, t, s):\n"
    "        try: q.put_nowait(float(np.abs(indata).max()))\n"
    "        except Exception: pass\n"
    "    name=''\n"
    "    try:\n"
    "        qd=idx if idx is not None else sd.default.device[0]\n"
    "        name=str(sd.query_devices(qd).get('name',''))\n"
    "    except Exception: pass\n"
    "    with sd.InputStream(device=idx, channels=1, samplerate=16000, blocksize=1600, dtype='float32', callback=cb):\n"
    "        print(json.dumps({'start':True,'device':name}), flush=True)\n"
    "        end=time.time()+SECONDS\n"
    "        while time.time()<end:\n"
    "            try: lvl=q.get(timeout=0.15)\n"
    "            except Exception: lvl=0.0\n"
    "            print(json.dumps({'level':round(min(1.0,lvl),4)}), flush=True)\n"
    "        print(json.dumps({'done':True}), flush=True)\n"
    "except Exception as e:\n"
    "    print(json.dumps({'error': type(e).__name__+': '+str(e)}), flush=True)\n"
)


def stream_mic_levels(emit, seconds: float = 6.0) -> None:
    """Stream live input levels from the SELECTED mic by running the capture in an
    ISOLATED subprocess (its own fresh PortAudio), so a headset plugged in after
    launch is actually seen — the main process's device table is a stale startup
    snapshot and re-initializing it in-process crashes the app. `emit(obj)` is
    called per line ({start|level|done|error}); it returns False when the client
    is gone, which tears the subprocess down."""
    import subprocess
    import json as _json
    code = _MIC_METER_CODE.replace("SECONDS", repr(float(seconds)))
    args = ([sys.executable, "--hearth-run-python", "-c", code]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-c", code])
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True,
                             creationflags=flags)
    except Exception as e:
        emit({"error": f"{type(e).__name__}: {e}"})
        return
    try:
        for line in p.stdout:  # blocks per line; subprocess flushes each
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except Exception:
                continue
            if not emit(obj):
                break
    finally:
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def mic_test(seconds: float = 1.6) -> dict:
    """Record a short clip from the SELECTED mic and report its level, so the user
    can confirm the right device is picked and see the meter move when they talk.
    Returns peak + rms (0..1) and whether it heard anything."""
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:
        return {"ok": False, "error": f"audio libs unavailable: {e}"}
    try:
        idx = input_device_index()
        sr = 16000
        rec = sd.rec(int(max(0.3, seconds) * sr), samplerate=sr, channels=1,
                     dtype="float32", device=idx)
        sd.wait()
        rec = rec.reshape(-1)
        peak = float(np.abs(rec).max()) if rec.size else 0.0
        rms = float(np.sqrt(np.mean(rec ** 2))) if rec.size else 0.0
        name = ""
        try:
            _q = idx if idx is not None else sd.default.device[0]
            name = str(sd.query_devices(_q).get("name", ""))
        except Exception:
            pass
        return {"ok": True, "peak": round(peak, 4), "rms": round(rms, 4),
                "heard": peak > 0.012, "device": name}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# The wake listener and voice mode run in one process and want the same mic.
# A Bluetooth headset opens for one capture at a time, and two listeners also
# means two transcriptions of every word. Voice mode claims the mic; the wake
# listener lets go while any claim is held and says when it has.
_mic_claims = 0
_mic_claims_lock = threading.Lock()
wake_released = threading.Event()
wake_released.set()


def claim_mic(wait_s: float = 2.0) -> None:
    """Take the mic for voice mode, waiting briefly for the wake listener to
    close its stream."""
    global _mic_claims
    with _mic_claims_lock:
        _mic_claims += 1
    wake_released.wait(wait_s)


def release_mic() -> None:
    global _mic_claims
    with _mic_claims_lock:
        _mic_claims = max(0, _mic_claims - 1)


def mic_claimed() -> bool:
    return _mic_claims > 0


def selected_input_name() -> str:
    """The mic picked in Settings > Voice, by name, or '' for none picked."""
    return (_env.get("VOICE_INPUT_NAME", "") or "").strip()


# A device number is an index into one PortAudio's table at one moment:
# plugging in a headset renumbers it, and the table sounddevice builds is not
# the one PyAudio (inside RealtimeSTT) builds. So a pick is kept by name and
# looked up in whichever table is about to open it. The same mic appears once
# per host API; this is the order it is opened through.
_HOSTAPI_ORDER = ("wasapi", "mme", "directsound", "wdm-ks")


def index_for_name(name: str, devices) -> Optional[int]:
    """Find `name` among `devices`, [(index, name, input_channels, hostapi)].
    MME cuts names to 31 characters, so a 31-character name also matches as a
    prefix of the full one."""
    want = (name or "").strip().lower()
    if not want:
        return None
    ins = [d for d in devices if d[2] > 0]
    found = [d for d in ins if d[1].strip().lower() == want]
    if not found:
        found = [d for d in ins if len(d[1].strip()) >= 31
                 and want.startswith(d[1].strip().lower())]

    def rank(d):
        api = (d[3] or "").lower()
        return next((i for i, k in enumerate(_HOSTAPI_ORDER) if k in api),
                    len(_HOSTAPI_ORDER))
    found.sort(key=rank)
    return found[0][0] if found else None


def sounddevice_inputs() -> list:
    """sounddevice's table as [(index, name, input_channels, hostapi)]."""
    import sounddevice as sd  # type: ignore
    apis = [a.get("name", "") for a in sd.query_hostapis()]
    return [(i, str(d.get("name", "")), int(d.get("max_input_channels", 0)),
             apis[d["hostapi"]] if d.get("hostapi") is not None else "")
            for i, d in enumerate(sd.query_devices())]


def name_for_numbered_pick(idx) -> tuple:
    """A mic pick saved by an older version as a device number, as a name.

    The number only meant something in the device table of the moment it was
    saved; unplugging a headset renumbers the table, and the same number can
    then point at a speaker output. Returns (name, "") when the number is a
    real microphone now, or ("", why) when it is not."""
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return "", ""
    if idx < 0:
        return "", ""
    try:
        dev = next((d for d in sounddevice_inputs() if d[0] == idx), None)
    except Exception:
        return "", "no microphone is connected"
    if dev is None or dev[2] <= 0:
        return "", "the mic you picked is not connected"
    if is_system_audio_device(dev[1]):
        return "", f"'{dev[1]}' is not a microphone; pick yours in Settings > Voice"
    return dev[1], ""


def input_device_index():
    """The picked mic's index in sounddevice's table, or None for the OS
    default. A pick saved by name is looked up now; an older pick saved only
    as a number is used as it is."""
    name = selected_input_name()
    if name:
        try:
            return index_for_name(name, sounddevice_inputs())
        except Exception:
            return None
    v = (_env.get("VOICE_INPUT_DEVICE", "") or "").strip()
    if v.lstrip("-").isdigit():
        legacy, _ = name_for_numbered_pick(int(v))
        if legacy:
            try:
                return index_for_name(legacy, sounddevice_inputs())
            except Exception:
                return None
    return None


# Windows/PulseAudio loopback + virtual-cable capture endpoints. These are NOT
# microphones — they carry whatever the SPEAKERS are playing. If Hearth ever
# opens one as its "mic" it transcribes system audio (its own TTS, a video,
# music) and can loop on itself. Never fall back to one.
_SYSTEM_AUDIO_HINTS = (
    "stereo mix", "wave out mix", "what u hear", "what you hear",
    "loopback", "cable output", "voicemeeter out", "vb-audio point",
    "monitor of", "primary sound capture", "mixage stéréo", "mezcla est",
    # 'Sound Mapper - Input' just ROUTES to the OS default device, so if the
    # default is a loopback (Stereo Mix) it silently becomes one too — never pick
    # it as a fallback. 'Streaming Speakers' is a Steam speaker-monitor, not a mic.
    "sound mapper", "streaming speakers",
)


def is_system_audio_device(name: str) -> bool:
    """True if a device NAME looks like a system-audio/loopback capture endpoint
    rather than a real microphone."""
    n = (name or "").lower()
    return any(h in n for h in _SYSTEM_AUDIO_HINTS)
DEVICE = _env.get("STT_DEVICE", "cpu")  # "cuda" if you want GPU
SAMPLE_RATE = 16000  # Whisper input rate

# Tunable thresholds — RMS-based VAD. Works without webrtcvad.
SPEECH_RMS = float(_env.get("STT_THRESHOLD", "0.012"))
SILENCE_TAIL_S = float(_env.get("STT_SILENCE_TAIL", "1.2"))
MAX_UTTERANCE_S = float(_env.get("STT_MAX_UTTERANCE", "30"))
MIN_SPEECH_S = 0.4  # ignore very short blips

# Optional wake-word filter for continuous mode. If set, `/listen on` only
# triggers a turn when the user opens with this phrase. Empty = no filter
# (current behavior — every speech burst goes through).
#   HEARTH_WAKE_WORD="jarvis"
#   HEARTH_WAKE_WORD="hey jarvis"
WAKE_WORD = _env.get("WAKE_WORD", "").strip().lower()


_PUNCT_STRIP = re.compile(r"^[^\w]+|[^\w]+$")


def _strip_wake_word(text: str, wake: str) -> Optional[str]:
    """If `text` STARTS with the wake word (position 0, punctuation-tolerant),
    return the text with the wake-word prefix removed. Otherwise return None
    (caller should drop the utterance — not addressed to the assistant).

    Position-0 matching only: 'the jarvis system is cool' does NOT match
    wake='jarvis', because the assistant is being talked about, not addressed."""
    raw_tokens = text.split()
    tokens = [_PUNCT_STRIP.sub("", t).lower() for t in raw_tokens]
    wake_tokens = wake.lower().split()
    n = len(wake_tokens)
    if n == 0 or len(tokens) < n:
        return None
    if tokens[:n] != wake_tokens:
        return None
    return " ".join(raw_tokens[n:]).strip(",.!?:; ").strip()

_model = None  # faster_whisper.WhisperModel
_load_lock = threading.Lock()
_last_load_error: Optional[str] = None

_listening = False
_listener_thread: Optional[threading.Thread] = None
_text_queue: "Queue[str]" = Queue()


def set_model(size: str) -> str:
    """Switch the STT (Whisper) model at runtime. Resets the cached model so
    the new one loads on the next /listen. Common sizes: tiny(.en), base(.en),
    small(.en), medium(.en), large-v3, distil-small.en. Returns a status line."""
    global MODEL_SIZE, _model
    size = (size or "").strip()
    if not size:
        return f"STT model is '{MODEL_SIZE}'. Set one: tiny.en / base.en / small.en / medium.en / large-v3 (bigger = slower + more accurate; downloads on next /listen)."
    with _load_lock:
        MODEL_SIZE = size
        _model = None  # force reload with the new size on next use
    return f"STT model -> '{size}'. It'll download (if new) on your next /listen."


def set_device(device: str) -> str:
    """Switch the STT device (cpu / cuda) at runtime. Resets the cached model
    so the next transcribe rebuilds on the new device. Called by the GUI when
    the user flips the 'Where to run voice' dropdown — without this, the
    setting flips in the env but the in-process WhisperModel stays pinned
    to whatever device it loaded on at import."""
    global DEVICE, _model
    device = (device or "cpu").strip().lower()
    if device not in ("cpu", "cuda"):
        return f"STT device must be 'cpu' or 'cuda' (got {device!r})"
    with _load_lock:
        DEVICE = device
        _env.set("STT_DEVICE", device)
        _model = None
    return f"STT device -> {device}"


def cuda_available() -> bool:
    """True only if faster-whisper can ACTUALLY run on GPU right now — i.e. the
    GPU build of ctranslate2 is installed AND a CUDA device is visible. A plain
    CPU ctranslate2 wheel reports 0 devices even on a machine with an RTX card,
    so this reflects 'usable today', not 'has a GPU'."""
    try:
        import ctranslate2  # type: ignore
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


# The language a voice speaks, by the letter its id starts with.
_VOICE_SPEAKS = {"h": "hi", "e": "es", "f": "fr", "i": "it", "j": "ja", "p": "pt", "z": "zh"}


def spoken_language() -> str:
    """The language to listen in: the one named in Settings, else the language
    of the voice picked to answer. Someone who picked a Hindi voice and left
    listening on its default heard their Hindi turned into English words."""
    lang = (_env.get("STT_LANGUAGE", "") or "").strip().lower()
    return lang or _VOICE_SPEAKS.get((_env.get("VOICE", "") or "")[:1].lower(), "")


def model_size() -> str:
    """The model that is actually loaded. An English-only size (base.en) holds
    no other language, so when a language other than English is named, the
    multilingual model of the same size is used instead; otherwise Hindi
    speech came back as English words that sounded like it."""
    size = (_env.get("STT_MODEL", MODEL_SIZE) or MODEL_SIZE or "").strip()
    lang = spoken_language()
    if lang and lang != "en" and size.lower().endswith(".en"):
        return size[:-3]
    return size


def _try_load_model():
    global _model, _last_load_error, DEVICE, COMPUTE_TYPE
    if _model is not None:
        return _model
    with _load_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            _last_load_error = f"faster-whisper not installed: {e}"
            return None
        # Store models inside the workspace so the user's HF cache stays out of
        # their home dir clutter. Falls back to default cache.
        download_root = os.path.join(VOICES_DIR, "whisper")
        os.makedirs(download_root, exist_ok=True)
        # Device pick: an explicit HEARTH_STT_DEVICE wins; otherwise auto-detect
        # CUDA so a GPU box isn't silently stuck on slow CPU whisper (the ~10s
        # latency). GPU → float16 (fast); CPU → int8 (CPU-friendly).
        _explicit = _env.get("STT_DEVICE")
        if _explicit:
            _device_now = _explicit.strip().lower()
        elif cuda_available():
            _device_now = "cuda"
        else:
            _device_now = "cpu"
        _compute = "float16" if _device_now == "cuda" else COMPUTE_TYPE
        try:
            _model = WhisperModel(model_size(), device=_device_now,
                                  compute_type=_compute, download_root=download_root)
            DEVICE, COMPUTE_TYPE = _device_now, _compute
            _last_load_error = None
        except Exception as e:
            # GPU load can fail mid-flight (missing cuDNN, VRAM pressure). Fall
            # back to CPU rather than leaving voice dead.
            if _device_now == "cuda":
                try:
                    _model = WhisperModel(model_size(), device="cpu",
                                          compute_type="int8", download_root=download_root)
                    DEVICE, COMPUTE_TYPE = "cpu", "int8"
                    _last_load_error = (f"GPU whisper failed ({type(e).__name__}); "
                                        f"fell back to CPU.")
                    return _model
                except Exception as e2:
                    e = e2
            # Weights that are on disk and still won't load are damaged: they
            # are removed and fetched again, once.
            if model_present() and _drop_model_files():
                try:
                    _model = WhisperModel(model_size(), device="cpu", compute_type="int8", download_root=download_root)
                    DEVICE, COMPUTE_TYPE = "cpu", "int8"
                    _last_load_error = None
                    return _model
                except Exception as e3:
                    e = e3
            _last_load_error = f"whisper load failed: {type(e).__name__}: {e}"
            return None
    return _model


def _model_dirs() -> list:
    """The folders under the whisper cache that hold the selected size."""
    root = os.path.join(VOICES_DIR, "whisper")
    want = (model_size() or "").lower().replace(".", "").replace("-", "")
    if not want or not os.path.isdir(root):
        return []
    return [os.path.join(root, d) for d in os.listdir(root)
            if want in d.lower().replace(".", "").replace("-", "").replace("_", "") and os.path.isdir(os.path.join(root, d))]


def _drop_model_files() -> bool:
    import shutil
    dirs = _model_dirs()
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    return bool(dirs)


def model_present() -> bool:
    """True when the whisper weights for the selected size are already on disk.
    Constructing WhisperModel downloads them, which blocks the first mic use
    with nothing on screen, so setup fetches them ahead of time instead."""
    root = os.path.join(VOICES_DIR, "whisper")
    if not os.path.isdir(root):
        return False
    want = (model_size() or "").lower().replace(".", "").replace("-", "")
    if not want:
        return False
    # The size tag is in an ancestor directory (models--Systran--faster-whisper-
    # base.en), while the leaf is a commit hash, so match the whole path.
    for dirpath, _dirs, files in os.walk(root):
        tag = dirpath.lower().replace(".", "").replace("-", "").replace("_", "")
        if want in tag and any(f.endswith((".bin", ".safetensors")) for f in files):
            return True
    return False


def ensure_model() -> bool:
    """Load (and therefore download) the whisper weights now. Returns True when
    the model is ready. Safe to call off the request thread."""
    try:
        return _try_load_model() is not None
    except Exception:
        return False


def is_available() -> bool:
    try:
        import faster_whisper  # type: ignore  # noqa: F401
        import sounddevice    # type: ignore  # noqa: F401
        import numpy          # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> dict:
    have_fw = False
    have_sd = False
    have_np = False
    try:
        import faster_whisper  # type: ignore  # noqa: F401
        have_fw = True
    except ImportError:
        pass
    try:
        import sounddevice  # type: ignore  # noqa: F401
        have_sd = True
    except ImportError:
        pass
    try:
        import numpy  # type: ignore  # noqa: F401
        have_np = True
    except ImportError:
        pass
    return {
        "model_size": model_size(),
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "cuda_available": cuda_available(),
        "faster_whisper_installed": have_fw,
        "sounddevice_installed": have_sd,
        "numpy_installed": have_np,
        "model_loaded": _model is not None,
        "listening": _listening,
        "ready": have_fw and have_sd and have_np,
        "last_load_error": _last_load_error,
        "last_transcribe_error": _last_transcribe_error,
        "last_transcribe_error_at": _last_transcribe_error_at,
    }


def _record_until_silence(max_seconds: float = MAX_UTTERANCE_S):
    """Block-record audio from default mic until SILENCE_TAIL_S of quiet
    follows at least MIN_SPEECH_S of speech. Returns float32 mono numpy
    array at SAMPLE_RATE, or None on error / no-speech-detected."""
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np         # type: ignore
    except ImportError:
        return None

    block_s = 0.05  # 50ms blocks
    block_n = int(SAMPLE_RATE * block_s)
    buf: list = []
    speech_since = -1.0
    last_speech = -1.0
    t0 = time.time()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=block_n, device=input_device_index()) as stream:
        while time.time() - t0 < max_seconds:
            data, _overflow = stream.read(block_n)
            mono = data[:, 0] if data.ndim > 1 else data
            buf.append(mono.copy())
            rms = float((mono ** 2).mean() ** 0.5)
            now = time.time()
            if rms > SPEECH_RMS:
                if speech_since < 0:
                    speech_since = now
                last_speech = now
            else:
                if speech_since > 0 and (now - last_speech) > SILENCE_TAIL_S:
                    # Silence after some speech — done
                    break

    if speech_since < 0 or (last_speech - speech_since) < MIN_SPEECH_S:
        return None
    audio = np.concatenate(buf)
    return audio


_last_transcribe_error: Optional[str] = None
_last_transcribe_error_at: float = 0.0


def note_transcribe_error(exc: BaseException) -> None:
    global _last_transcribe_error, _last_transcribe_error_at
    _last_transcribe_error = f"{type(exc).__name__}: {exc}"
    _last_transcribe_error_at = time.time()
    try:
        print(f"[listen] transcription failed: {_last_transcribe_error}",
              file=sys.stderr, flush=True)
    except Exception:
        pass


def last_transcribe_error() -> dict:
    return {"error": _last_transcribe_error, "at": _last_transcribe_error_at}


def _transcribe_language() -> Optional[str]:
    """The language code to transcribe in, or None to detect it per utterance.

    Read on every call rather than captured at import, because the setting is
    applied by writing the environment and reloading the model in place.
    """
    lang = spoken_language()
    if model_size().lower().endswith(".en"):
        return "en"
    return lang or None


def transcribe(audio) -> str:
    """Run the model on a float32 numpy audio array at SAMPLE_RATE.
    Returns the transcribed text (best segment). Empty string on failure."""
    model = _try_load_model()
    if model is None:
        return ""
    try:
        lang = _transcribe_language()
        segments, _info = model.transcribe(
            audio,
            # A model whose name ends in .en holds no other language, and
            # asking it to detect one wastes a pass to reach the same answer.
            language=lang,
            beam_size=1,            # fast - boost to 5 for accuracy
            # Silero VAD (ships with faster-whisper, no extra dep) drops
            # speech-free chunks AND tightens utterance edges - much smarter
            # than our RMS-based pre-trim. Critical for the noisy-room case
            # where RMS thresholds fail. Enabled with conservative params so
            # short conversational pauses don't fragment the transcript.
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 400,
                "speech_pad_ms":           250,
            },
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        # The language it was said in, so the reply is spoken in it too.
        if text:
            said = lang or (getattr(_info, "language", "")
                            if getattr(_info, "language_probability", 0) >= 0.6 else "")
            _voice.heard_language(said or "")
        return text
    except Exception as e:
        # An empty string is what "you said nothing" looks like too, so a
        # transcription that fails outright was indistinguishable from silence
        # and left nothing behind to explain the difference.
        note_transcribe_error(e)
        return ""


def listen_once(max_seconds: float = MAX_UTTERANCE_S) -> str:
    """Block until the user speaks + falls silent, then transcribe.
    Returns the transcript or '' if nothing was heard / model unavailable."""
    if not is_available():
        return ""
    audio = _record_until_silence(max_seconds=max_seconds)
    if audio is None:
        return ""
    return transcribe(audio)


def _continuous_loop(on_utterance: Callable[[str], None]):
    """Background thread for continuous listening with TTS-interrupt.

    Behavior:
      - While TTS is playing, watch mic. If sustained speech appears,
        stop TTS and pivot into a full record-until-silence cycle.
      - When TTS isn't playing, just wait for a speech burst then record.
    """
    global _listening
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np         # type: ignore
    except ImportError:
        _listening = False
        return

    block_s = 0.05
    block_n = int(SAMPLE_RATE * block_s)
    speech_blocks_needed = 4  # ~200ms of speech before we decide it's real
    speech_run = 0

    # While TTS is playing, raise the trigger threshold so the mic doesn't
    # pick up speaker echo and feedback-loop on the assistant's own voice. Tunable
    # via env var; default 5x makes normal-volume TTS not trigger but a
    # human speaking AT or louder than the speakers still interrupts.
    TTS_MULT = float(_env.get("STT_TTS_THRESHOLD_MULT", "5.0"))
    # Post-stop debounce: after we kill TTS, the speakers keep playing for
    # ~200-400ms (audio buffer). Wait that out before recording so we don't
    # transcribe the assistant's own tail.
    POST_STOP_DEBOUNCE_S = float(_env.get("STT_POST_STOP_DEBOUNCE", "0.4"))

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", blocksize=block_n,
                            device=input_device_index()) as stream:
            while _listening:
                try:
                    data, _ = stream.read(block_n)
                except Exception:
                    time.sleep(0.05)
                    continue

                # While the assistant speaks, the mic hears it too. Half duplex
                # (speakers) drops that audio outright, which is the only
                # deterministic stop for the self-reply loop. Full duplex
                # (headset) keeps listening so you can talk over the reply, and
                # raises the bar instead so only speech clearly louder than the
                # bleed counts. This used to drop the audio either way, which
                # made the barge path below unreachable.
                tts_active = False
                tts_recent = False
                try:
                    tts_active = _voice.is_speaking()
                    tts_recent = _voice.seconds_since_spoke() < _voice.ECHO_TAIL_S
                except Exception:
                    pass
                if (tts_active or tts_recent) and _voice.half_duplex_enabled():
                    speech_run = 0  # reset so a partial run doesn't carry over
                    continue
                if tts_recent and not tts_active:
                    # Speaking has stopped but the speaker buffer is still
                    # draining; that tail is never a user turn.
                    speech_run = 0
                    continue

                mono = data[:, 0] if data.ndim > 1 else data
                rms = float((mono ** 2).mean() ** 0.5)
                # Full duplex over speakers: require speech well above the
                # bleed so the reply cannot trigger itself.
                threshold = SPEECH_RMS * TTS_MULT if tts_active else SPEECH_RMS

                if rms > threshold:
                    speech_run += 1
                else:
                    speech_run = 0
                    continue

                if speech_run < speech_blocks_needed:
                    continue

                # We have sustained speech. If TTS is playing, kill it and
                # wait briefly for the speaker audio to die before recording.
                try:
                    if _voice.is_speaking():
                        _voice.stop()
                        time.sleep(POST_STOP_DEBOUNCE_S)
                except Exception:
                    pass

                # Now do a full record-until-silence cycle for the rest
                # of the utterance. (We've already captured ~200ms of it,
                # but re-recording is simpler than splicing buffers.)
                speech_run = 0
                audio = _record_until_silence(max_seconds=MAX_UTTERANCE_S)
                if audio is None or len(audio) < SAMPLE_RATE * MIN_SPEECH_S:
                    continue
                text = transcribe(audio)
                if text:
                    if WAKE_WORD:
                        stripped = _strip_wake_word(text, WAKE_WORD)
                        if stripped is None:
                            continue  # speech detected but not addressed to Jarvis
                        if not stripped:
                            continue  # wake word with nothing after — ignore
                        text = stripped
                    try:
                        on_utterance(text)
                    except Exception:
                        pass
    finally:
        _listening = False


def start_continuous(on_utterance: Callable[[str], None]) -> str:
    """Spawn the background listener. Returns a status string."""
    global _listening, _listener_thread
    if _listening:
        return "already listening"
    if not is_available():
        return "Error: faster-whisper or sounddevice not installed"
    # Warm the model up so the first transcription isn't slow
    if _try_load_model() is None:
        return f"Error: {_last_load_error}"
    _listening = True
    _listener_thread = threading.Thread(
        target=_continuous_loop, args=(on_utterance,), daemon=True
    )
    _listener_thread.start()
    return "listening"


def stop_continuous() -> str:
    global _listening
    _listening = False
    return "stopped"


def is_listening() -> bool:
    return _listening


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(status(), indent=2))
    if "--listen" in sys.argv:
        print("listening... say something then pause:")
        text = listen_once()
        print(f"heard: {text!r}")
