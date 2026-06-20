# Voice pipeline upgrade plan

Implementation-ready plan for three voice upgrades to Hearth. Grounded in the
current code as of this writing. **No code was changed to produce this doc.**

## IMPORTANT research caveat (read first)

Live web access (`WebSearch` / `WebFetch`) was **denied** in the session that
produced this plan, so the NVIDIA "Nemotron 3.5-ASR-streaming" specifics in
Goal 1 could **not** be verified against the HuggingFace card / NVIDIA docs.
Everything in Goal 1 marked **[VERIFY]** is inferred from the known NeMo /
Riva / parakeet / canary lineage (knowledge cutoff Jan 2026) and MUST be
confirmed against the real model card before anyone writes code. The exact
`nvidia/nemotron*asr*` repo id, license, ONNX/CTranslate2 availability, and
the streaming API surface are the load-bearing unknowns. The architecture in
Goals 2 and 3 does **not** depend on those unknowns and is safe to build now.

---

## Current state (what exists today)

### STT
- `hearth/listen.py` — legacy STT. `faster-whisper` (CTranslate2) + RMS VAD +
  optional wake word. `base.en` default, `int8`/cpu or `float16`/cuda
  (auto-detect via `cuda_available()` at `listen.py:128`). Public surface:
  `is_available()`, `status()`, `transcribe(audio)`, `listen_once()`,
  `start_continuous(cb)`, `stop_continuous()`, `set_model()`, `set_device()`.
  Used by the GUI `/api/stt` handler (`web.py:2719` `_stt`) — browser records
  PCM, posts base64, server runs `_listen._try_load_model().transcribe(...)`.
- `hearth/realtime_voice.py` — newer streaming loop. `RealtimeSTT`
  (silero VAD + streaming faster-whisper). Public surface:
  `is_available()`, `status()`, `start_continuous(on_utterance)`,
  `stop_continuous()`, `set_caption_callback()`, `set_barge_callback()`,
  `stream_speak()`. Driven by the SSE handler `/api/voice/realtime/stream`
  (`web.py:2616`+, the `_voice_realtime_stream` method).

### TTS
- `hearth/voice.py` — Kokoro (ONNX) primary, Piper fallback. Public surface:
  `is_available()`, `status()`, `speak(text, blocking, voice, speed)`,
  `stop()`, `reset_abort()`, `set_default_voice()`, `set_speed()`,
  `list_voices()`, `reload()`, `is_speaking()`, `seconds_since_spoke()`.
  TTS plays through the **server's** system speakers via `sounddevice`
  (`voice.py:334` `_play`), NOT the browser. The GUI `/api/tts` handler
  (`web.py:2704` `_tts`) just calls `_voice.speak(text, blocking=False)`.

### Web endpoints (`hearth/web.py`)
- `GET  /api/voice/status` (`:1267`) → `{tts, stt, realtime}` status blocks.
- `GET  /api/voice/realtime/stream` (`:1287` route → `:2616` impl) → NDJSON
  SSE-ish stream of `{type: partial|final|barge|started|stopped|error}`.
- `POST /api/voice/device` (`:1628`) → one-click STT cpu/cuda flip.
- `POST /api/tts` (`:1733`), `POST /api/stt` (`:1735`).
- `POST /api/voice/reset|reload|stop` (`:1739`, `:1748`, `:1817`),
  `POST /api/voice/realtime/stop` (`:1788`).
- Settings persistence: `SETTINGS_PATH = ~/Jarvis/settings.json`
  (`web.py:114`), defaults block at `web.py:851` (`voice_tts`, `voice_stt`,
  `stt_device`, `tts_device`, `voice_speed`, `voice_name`, `stt_model`).
  Env vars synced from settings at `web.py:890` and `:929`.
- Provider/key config to **reuse**: `LOCAL_API_BASE` / `LOCAL_API_KEY`
  (`web.py:112`), brain-switch branch (`web.py:1855`+), per-provider key
  store `~/Jarvis/brain_keys.json` (referenced in CLI `/brain`).

### GUI (`hearth/ui.html`)
- Voice-mode overlay markup: `#voice-mode` (`:3664`), `#voice-grid`
  (`:3665`), CSS at `:2376`–`:2409` (15×15 grid, `.d`/`.d.on`/`.d.hot`).
- Visualizer driver: `VOICE_GRID_*` consts (`:8337`), `startVoiceGridWave()`
  (`:8351`) — a **time-based sine wave that ignores real audio**.
  `voiceUpdate(_level)` (`:8417`) is **deliberately inert** ("we do NOT
  animate the grid for user audio"). The mic processor already computes a
  `peak` and calls `voiceUpdate(peak)` (`:8223`) — so amplitude is available
  but thrown away.
- TTS playback: `speakText()` (`:8111`) and the streaming
  `maybeFlushTTS()` (`:5380`) both POST `/api/tts`; **audio is rendered
  server-side**, so the browser has no waveform handle for TTS today.
- Settings voice UI: device/model selectors wired at `:7362`–`:7452`
  (`setting-tts-device`, `setting-stt-device`, `setting-stt-model`,
  `setting-voice-speed`). Switch rows `voice_tts` (`:2872`), `voice_stt`
  (`:2880`).
- Onboarding voice step (`data-step="4"`): voice choice + picker at
  `:3494`–`:3536` (`ob-voice-name`, `ob-voice-speed`, `ob-voice-test`).

---

## Cross-cutting design: STT/TTS backend registry

The single most important change. Today STT and TTS each have one hardwired
engine path. To make local Whisper, Nemotron, and cloud APIs interchangeable
we introduce two tiny registry modules and route every call through them.

### New files

```
hearth/stt_backends/
  __init__.py          # registry + fallback-chain resolver
  base.py              # SttBackend ABC
  whisper_backend.py   # wraps existing listen.py / realtime_voice.py
  nemotron_backend.py  # NeMo / ONNX streaming (Goal 1)
  cloud_backend.py     # OpenAI-compatible / Gemini / ElevenLabs (Goal 2)

hearth/tts_backends/
  __init__.py          # registry + fallback-chain resolver
  base.py              # TtsBackend ABC
  kokoro_backend.py    # wraps existing voice.py (Kokoro + Piper)
  cloud_backend.py     # OpenAI tts-1 / gpt-4o-mini-tts / ElevenLabs / Gemini
```

### `stt_backends/base.py`

```python
from abc import ABC, abstractmethod
from typing import Optional

class SttBackend(ABC):
    id: str            # "whisper", "nemotron", "cloud-openai", ...
    label: str         # human label for Settings dropdown
    supports_streaming: bool = False

    @abstractmethod
    def is_available(self) -> bool: ...
    @abstractmethod
    def status(self) -> dict: ...
    # Batch path used by /api/stt (browser posts a finished PCM clip).
    @abstractmethod
    def transcribe(self, pcm_f32, sample_rate: int) -> str: ...
    # Optional streaming path used by /api/voice/realtime/stream.
    def start_continuous(self, on_utterance, on_partial=None, on_barge=None) -> str:
        raise NotImplementedError
    def stop_continuous(self) -> str:
        return "ok"
```

### `tts_backends/base.py`

```python
class TtsBackend(ABC):
    id: str
    label: str
    # Return (audio_int16_bytes, sample_rate) so callers can EITHER play
    # server-side (sounddevice) OR ship bytes to the browser for the
    # reactive visualizer (Goal 3). Key change vs today's fire-and-forget.
    @abstractmethod
    def synth(self, text: str, voice: Optional[str], speed: float) -> tuple[bytes, int]: ...
    @abstractmethod
    def is_available(self) -> bool: ...
    @abstractmethod
    def status(self) -> dict: ...
```

### Registry + fallback resolver (`stt_backends/__init__.py`)

```python
_REGISTRY: dict[str, "SttBackend"] = {}

def register(b): _REGISTRY[b.id] = b

def resolve(preferred: str, chain: list[str]) -> "SttBackend":
    """Return the first AVAILABLE backend: preferred, then each fallback."""
    order = [preferred] + [c for c in chain if c != preferred]
    for bid in order:
        b = _REGISTRY.get(bid)
        if b and b.is_available():
            return b
    return _REGISTRY["whisper"]   # last-resort default, always importable
```

### Wiring into `web.py` (minimal, surgical)

- New settings keys (extend the defaults block at `web.py:851`):
  `stt_backend` (default `"whisper"`), `tts_backend` (default `"kokoro"`),
  plus cloud sub-config (see Goal 2).
- `_stt` (`web.py:2719`): replace the direct `_listen._try_load_model()` call
  with `stt_backends.resolve(settings["stt_backend"], FALLBACK_STT).transcribe(pcm, 16000)`.
- `_tts` (`web.py:2704`): replace `_voice.speak(...)` with a call through
  `tts_backends.resolve(...)`. For the **server-speaker** path keep playing
  via `sounddevice`; for Goal 3 add a `?return_audio=1` mode that returns the
  bytes instead (see Goal 3).
- `/api/voice/realtime/stream` (`web.py:2616`): resolve the streaming STT
  backend; if the chosen one lacks `supports_streaming`, fall back to
  whisper-realtime (current behavior).
- `/api/voice/status` (`web.py:1267`): add a `backends` block listing every
  registered backend's `{id, label, available, streaming}` so the GUI can
  populate dropdowns and grey-out the unavailable ones (same pattern already
  used for ONNX providers at `ui.html:7374`).

This keeps every existing call site working: `whisper_backend` and
`kokoro_backend` are thin adapters over the current `listen.py` / `voice.py`
so there is zero behavior change when the default backends are selected.

---

## GOAL 1 — NVIDIA Nemotron 3.5-ASR-streaming as a local STT backend

### [VERIFY] What we believe is true (NeMo/parakeet/canary lineage)

NVIDIA's recent streaming ASR models (parakeet, canary, and the FastConformer
family) share these traits, which a ~0.6B "Nemotron 3.5-ASR-streaming" model
almost certainly inherits — **confirm each against the real card:**

- **Architecture**: FastConformer (Conformer + 8× depthwise-subsampling)
  encoder with a **cache-aware streaming** mode + RNNT/TDT or CTC decoder.
  ~0.6B params puts it between parakeet-ctc-0.6b and the 1.1b tier.
- **Runtime**: ships as a NeMo `.nemo` checkpoint, runnable via the `nemo_toolkit[asr]`
  Python package. Cache-aware streaming exposes a `transcribe()` plus a
  frame-by-frame streaming API (`FrameBatchASR` / `CacheAwareStreamingAudioBuffer`).
- **Footprint [VERIFY]**: 0.6B params ≈ ~1.2–2.5 GB VRAM in fp16 for inference
  (weights ~1.2 GB + activations). **Fits comfortably on the 8 GB RTX 5060
  ALONGSIDE an 8–10B LLM only if the LLM is quantized and leaves ~2.5 GB.**
  In practice the user runs the LLM near the VRAM ceiling, so Nemotron on GPU
  will often **contend with the LLM** — see hardware verdict below.
- **Latency [VERIFY]**: cache-aware streaming targets ~100–300 ms chunk
  latency, materially better than `base.en` faster-whisper's ~0.5–1 s
  endpoint-to-text on CPU and competitive with `tiny.en` on GPU.
- **License [VERIFY]**: NVIDIA model licenses are typically NVIDIA OneWay /
  research or a permissive variant — **must confirm it's redistributable /
  usable in a shipped app before bundling**. Likely NOT bundled in the
  installer regardless (size + license); download-on-first-use instead.
- **ONNX / CTranslate2 [VERIFY]**: NeMo can export FastConformer-CTC to ONNX;
  RNNT export is partial. CTranslate2 does **not** support FastConformer.
  So the realistic local paths are (a) NeMo+torch on GPU, or (b) an ONNX-CTC
  export on CPU/`onnxruntime`. **A clean CPU ONNX path is the deciding
  factor for whether this is worth it on this hardware.**

### Hardware verdict (honest)

- **The RTX 5060 has 8 GB and the LLM already eats most of it.** Running
  Nemotron on the **GPU** at the same time as a local 8–10B LLM is risky:
  VRAM contention will OOM or force the LLM to spill. So GPU-Nemotron is only
  realistic when the brain is a **cloud** model (no local VRAM use) or a very
  small local model.
- **CPU Nemotron** (ONNX-CTC) is feasible and frees VRAM, but a 0.6B
  Conformer on a Ryzen 7 CPU may not beat `faster-whisper base.en int8` by
  enough to justify the dependency weight — **must benchmark before shipping.**
- **`nemo_toolkit` is a heavy dependency** (pulls torch, torchaudio,
  hydra, sentencepiece, huge transitive tree). This conflicts directly with
  the project's standing "tool-diet / keep the bundle small" goal and the
  `HEARTH_LITE` build that *drops* CUDA/torch. **Recommendation: Nemotron is
  an OPT-IN, install-on-demand backend, never bundled, never in LITE.**

**Bottom line:** Build the backend slot and the install-on-demand flow, but
default to `whisper`. Promote Nemotron only if the live benchmark on this
exact box shows a real latency/accuracy win at acceptable VRAM cost. Do not
claim a speedup that hasn't been measured here.

### `nemotron_backend.py` (skeleton — adjust to the real API)

```python
import os, numpy as np
from .base import SttBackend

class NemotronBackend(SttBackend):
    id = "nemotron"
    label = "NVIDIA Nemotron 3.5-ASR (streaming)"
    supports_streaming = True

    def __init__(self):
        self._model = None
        self._device = os.environ.get("JARVIS_STT_DEVICE", "cpu")
        # [VERIFY] real repo id from the HF card:
        self._repo = os.environ.get("HEARTH_NEMOTRON_REPO",
                                    "nvidia/nemotron-3.5-asr-streaming")

    def is_available(self) -> bool:
        try:
            import nemo.collections.asr  # noqa
            return True
        except Exception:
            return False

    def _load(self):
        if self._model is not None:
            return self._model
        import nemo.collections.asr as nemo_asr
        # [VERIFY] class + from_pretrained signature against the card.
        self._model = nemo_asr.models.ASRModel.from_pretrained(self._repo)
        if self._device == "cuda":
            self._model = self._model.cuda().half()
        self._model.eval()
        return self._model

    def transcribe(self, pcm_f32, sample_rate: int) -> str:
        import soundfile as sf, tempfile
        m = self._load()
        if sample_rate != 16000:
            pcm_f32 = _resample(pcm_f32, sample_rate, 16000)  # reuse web.py logic
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, pcm_f32, 16000)
            out = m.transcribe([f.name])     # [VERIFY] return shape
        return (out[0] if out else "") or ""

    # Streaming: wrap NeMo CacheAwareStreamingAudioBuffer feeding 16k chunks
    # from the mic, emit partials via on_partial and finals via on_utterance.
    # Mirror realtime_voice.py's callback contract so web.py routing is uniform.
```

### pip deps (opt-in group — do NOT add to base requirements)

```
nemo_toolkit[asr]      # heavy: torch, torchaudio, sentencepiece, hydra, ...
soundfile              # wav I/O for NeMo transcribe()
# (torch/onnxruntime-gpu already conditionally present for CUDA builds)
```
Gate behind an extra, e.g. `pip install hearth[nemotron]`, or an in-app
"Download Nemotron ASR" button that shells the pip install with progress
(reuse the planned GPU-whisper install-with-progress flow from the open bugs
list). Never pulled by `HEARTH_LITE`.

### Fallback chain
`FALLBACK_STT = ["nemotron", "whisper", "cloud-openai"]`. If `nemo` import
fails or `_load()` raises (OOM on the 8 GB card), `resolve()` silently drops
to `whisper`. Surface the reason in `status()["last_load_error"]` exactly like
`listen.py` does today so the GUI can toast it.

### Testing Goal 1
1. `python -c "import hearth.stt_backends as s; print(s._REGISTRY.keys())"` —
   nemotron registers even when `nemo` absent (availability is lazy).
2. Availability probe: with `nemo` not installed, `is_available()` is False
   and `/api/voice/status` shows nemotron `available:false` greyed in the GUI.
3. **Live VRAM test on the actual box**: load the usual LLM, then select
   Nemotron-GPU, transcribe a 10 s clip, watch `nvidia-smi`. If it OOMs the
   LLM, that confirms GPU-Nemotron is cloud-brain-only. Record the number.
4. **Latency bench vs faster-whisper**: feed the same 10 s WAV through
   `whisper base.en` and Nemotron (CPU and GPU), print wall-clock each. Ship
   only if Nemotron wins meaningfully here.
5. Streaming: in voice mode, confirm partial captions appear and barge-in
   still fires (`set_barge_callback` contract preserved).

---

## GOAL 2 — Native cloud voice (STT + TTS) backends

Add OpenAI-compatible and provider voice APIs as selectable backends, reusing
the existing `LOCAL_API_BASE` / `LOCAL_API_KEY` + `brain_keys.json` config.

### Providers to support
- **STT**: OpenAI `gpt-4o-transcribe` / `gpt-4o-mini-transcribe` / `whisper-1`
  (all via `POST {base}/audio/transcriptions`, multipart). Gemini
  (`audio` part in `generateContent`). ElevenLabs STT (`scribe`).
- **TTS**: OpenAI `tts-1` / `tts-1-hd` / `gpt-4o-mini-tts`
  (`POST {base}/audio/speech` → audio bytes). ElevenLabs
  (`/v1/text-to-speech/{voice_id}`). Gemini TTS.

### `stt_backends/cloud_backend.py`

```python
import os, io, requests
from .base import SttBackend

class CloudSttBackend(SttBackend):
    id = "cloud-openai"
    label = "Cloud STT (OpenAI-compatible)"

    def __init__(self, base=None, key=None, model="gpt-4o-mini-transcribe"):
        # Reuse the brain config when the voice config is blank, so a user who
        # already wired an OpenAI brain key gets cloud voice for free.
        self.base = base or os.environ.get("HEARTH_STT_API_BASE") \
                    or os.environ.get("LOCAL_API_BASE")
        self.key  = key  or os.environ.get("HEARTH_STT_API_KEY") \
                    or os.environ.get("OPENAI_API_KEY") \
                    or os.environ.get("LOCAL_API_KEY")
        self.model = os.environ.get("HEARTH_STT_MODEL", model)

    def is_available(self) -> bool:
        return bool(self.base and self.key)

    def transcribe(self, pcm_f32, sample_rate: int) -> str:
        wav = _pcm_to_wav_bytes(pcm_f32, sample_rate)   # helper
        r = requests.post(
            f"{self.base.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.key}"},
            files={"file": ("audio.wav", io.BytesIO(wav), "audio/wav")},
            data={"model": self.model},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("text", "")
```

### `tts_backends/cloud_backend.py`

```python
class CloudTtsBackend(TtsBackend):
    id = "cloud-openai"
    label = "Cloud TTS (OpenAI-compatible)"
    def synth(self, text, voice, speed):
        r = requests.post(
            f"{self.base.rstrip('/')}/audio/speech",
            headers={"Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/json"},
            json={"model": self.model, "voice": voice or "alloy",
                  "input": text, "response_format": "pcm"},  # pcm = easiest to play/visualize
            timeout=60,
        )
        r.raise_for_status()
        return r.content, 24000   # OpenAI pcm is 24kHz int16
```
ElevenLabs/Gemini are sibling classes registered under
`cloud-elevenlabs` / `cloud-gemini` with provider-specific request shapes; the
registry treats them uniformly.

### Config reuse + storage
- Add settings keys (defaults block `web.py:851`): `stt_backend`,
  `tts_backend`, `voice_api_base`, `voice_api_provider`, `tts_voice_cloud`,
  `stt_model_cloud`, `tts_model_cloud`.
- **Keys**: store voice API keys in the **existing** `~/Jarvis/brain_keys.json`
  store (the `/brain` command already manages per-provider keys), under a
  `voice` namespace, so a user who set an OpenAI brain key can opt to reuse it
  for voice with one checkbox ("Use my chat-brain key for voice too"). Never
  log keys; never write them into shipped files (memory rule:
  no-personal-data-in-repo).
- The cloud backends fall back to `LOCAL_API_BASE`/`LOCAL_API_KEY` when no
  voice-specific override is set, exactly like the brain path.

### pip deps
```
requests        # likely already present; if not, use urllib to avoid a new dep
soundfile       # only if not already pulled by Nemotron path; else use wave stdlib
```
Prefer `urllib` + the stdlib `wave` module for the WAV helper to avoid adding
`requests`/`soundfile` to the base install. Cloud backends should be
**zero-new-dependency** if at all possible.

### Fallback chain
- STT: `["whisper", "cloud-openai"]` by default (local-first per project
  ethos). A user who picks cloud gets `["cloud-openai", "whisper"]`.
- TTS: `["kokoro", "cloud-openai"]`. Kokoro stays default (offline, free,
  zero VRAM). Cloud is opt-in.
- If a cloud call raises (no network on the IIT MITM SSL network — see memory
  `reference_iit_network_ssl_mitm.md`; or 401/timeout), `resolve()` already
  picked an available backend, but a **runtime** failure must also fall back:
  wrap the `transcribe`/`synth` call site in `web.py` with a try/except that
  retries the next backend in the chain and toasts the reason.

### Testing Goal 2
1. Offline: pick cloud backend with no key → `is_available()` False, GUI greys
   it, falls back to whisper/kokoro. No crash.
2. With a valid OpenAI key: `/api/stt` round-trips a recorded clip through
   `gpt-4o-mini-transcribe`; `/api/tts` plays `tts-1` audio.
3. Reuse path: set an OpenAI brain key, tick "use for voice", confirm voice
   works without re-entering the key.
4. Runtime-fallback: set a bad key → first call 401 → auto-falls to local,
   toast explains. (Honest-verification: this MUST fail under a build without
   the try/except fallback, to prove the fallback is real.)
5. SSL-MITM network: confirm the documented `--trusted-host` / cert guidance
   applies; cloud voice may simply be unavailable on the IIT network — surface
   that clearly rather than hanging.

---

## GOAL 3 — Reactive full-screen voice-mode visualizer

Make the `#voice-grid` 15×15 dot grid react to **live** mic amplitude (user
speaking) and **TTS** amplitude (assistant speaking), instead of the current
fixed sine wave that ignores audio (`startVoiceGridWave` `ui.html:8351`).

### The core obstacle (state it plainly)
TTS audio is rendered **server-side** through `sounddevice` (`voice.py:_play`),
so the **browser has no signal to visualize the assistant's voice today**. Two
options:

- **Option A (recommended): move TTS playback into the browser.** Add a
  `return_audio` mode to `/api/tts` (and the cloud TTS path) that returns
  PCM/WAV bytes instead of playing server-side. The GUI plays it via
  WebAudio, taps an `AnalyserNode`, and drives the grid from real amplitude.
  This also fixes the long-standing "Speak button silent" bug (open bug #3),
  because playback moves to the browser where failures are visible, and it
  makes cloud TTS trivial (cloud returns bytes anyway).
- **Option B (low-effort fallback): keep server-side playback, animate the
  grid from `is_speaking()` + a synthesized envelope.** No real amplitude, but
  the grid at least pulses only while TTS is actually active. Use this only if
  Option A's browser-playback migration is deferred.

Plan for **Option A**; it is the durable fix.

### Mic-side (already 90% there)
- `startRecording()` already computes `peak` and calls `voiceUpdate(peak)`
  (`ui.html:8223`), but `voiceUpdate` is inert (`:8417`). Replace the inert
  body with a real amplitude→grid mapper.
- Better: add an `AnalyserNode` to the recorder graph (and the barge-in graph
  at `:8163`) and read `getByteFrequencyData` / time-domain RMS each
  animation frame for a smooth visual rather than per-buffer peaks.

### New visualizer driver (replaces `startVoiceGridWave`)

```js
// Drive the 15x15 grid from a live amplitude source (mic OR tts analyser).
let voiceAnalyser = null, voiceAnimRAF = null;
const cx = (VOICE_GRID_COLS - 1) / 2, cy = (VOICE_GRID_ROWS - 1) / 2;
const dist = voiceDots.map((_, i) =>
  Math.hypot((i % VOICE_GRID_COLS) - cx, Math.floor(i / VOICE_GRID_COLS) - cy));

function startReactiveGrid(analyser) {
  voiceAnalyser = analyser;
  const buf = new Uint8Array(analyser.frequencyBinCount);
  function frame() {
    analyser.getByteTimeDomainData(buf);
    // RMS amplitude 0..1
    let s = 0; for (let i = 0; i < buf.length; i++) { const v=(buf[i]-128)/128; s+=v*v; }
    const amp = Math.min(1, Math.sqrt(s / buf.length) * 3);   // gain
    // Radius of the lit ring grows with amplitude; "hot" core at high amp.
    const radius = amp * (VOICE_GRID_COLS / 2);
    voiceDots.forEach((d, i) => {
      const lit = dist[i] <= radius;
      const hot = dist[i] <= radius * 0.45 && amp > 0.5;
      d.classList.toggle('on', lit && !hot);
      d.classList.toggle('hot', hot);
    });
    voiceAnimRAF = requestAnimationFrame(frame);
  }
  voiceAnimRAF = requestAnimationFrame(frame);
}
function stopReactiveGrid() {
  if (voiceAnimRAF) cancelAnimationFrame(voiceAnimRAF);
  voiceAnimRAF = voiceAnalyser = null;
  stopVoiceGridWave();   // settle to rest pattern (reuse existing :8377)
}
```

### Browser TTS playback + analyser (Option A)

```js
async function playTTSReactive(text) {
  const r = await fetch('/api/tts?return_audio=1', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ text }) });
  if (!r.ok) { /* toast + fall back to server-side speakText() */ return; }
  const pcm = await r.arrayBuffer();          // int16 mono @ sr from header
  const sr = parseInt(r.headers.get('X-Sample-Rate') || '24000', 10);
  const ctx = new (window.AudioContext||window.webkitAudioContext)();
  const f32 = int16ToFloat32(new Int16Array(pcm));
  const abuf = ctx.createBuffer(1, f32.length, sr); abuf.copyToChannel(f32, 0);
  const src = ctx.createBufferSource(); src.buffer = abuf;
  const analyser = ctx.createAnalyser(); analyser.fftSize = 256;
  src.connect(analyser); analyser.connect(ctx.destination);
  startReactiveGrid(analyser);
  src.onended = () => { stopReactiveGrid(); ctx.close(); };
  src.start();
}
```

### Wiring into the voice loop
- `enterVoiceMode()` (`ui.html:8389`): on mic start, build a recorder
  `AnalyserNode` and call `startReactiveGrid(micAnalyser)` so the grid reacts
  while the **user** talks.
- `voiceCycle()` (`:8430`) and `maybeFlushTTS()` (`:5380`): route assistant
  speech through `playTTSReactive()` (Option A) so the grid reacts to the
  **assistant**. Switch the analyser source between mic and TTS as the turn
  flips; `voiceSpeaking`/`is_speaking()` already track which side is active.
- Remove the `voiceUpdate` inert stub note ("we do NOT animate the grid for
  user audio", `:8415`) — that decision is being reversed.

### Backend change for Option A (`web.py:_tts`, `:2704`)
- Honor `?return_audio=1`: instead of `_voice.speak(...)`, call
  `tts_backends.resolve(...).synth(text, voice, speed)` to get `(bytes, sr)`,
  set `Content-Type: application/octet-stream` + `X-Sample-Rate` header, and
  return the bytes. Kokoro's `obj.create()` already yields samples
  (`voice.py:485`), so a `synth()` that returns bytes is a small refactor of
  the existing `_run`/`_play` split — pull synthesis out of the playback.
- Keep the no-arg `/api/tts` server-side-speakers path for the CLI / headless
  and as the Option-B fallback.

### Testing Goal 3
1. Mic reactivity: enter voice mode, speak — grid ring should grow/shrink with
   your volume; silence → rest pattern (single center dot, `:8385`).
2. TTS reactivity (Option A): trigger a reply — grid pulses in sync with the
   spoken audio; on `src.onended` it settles. Verify the Speak button now
   produces audible browser audio (fixes open bug #3 as a side effect).
3. Barge-in still works: talk over the assistant — TTS stops, grid flips back
   to mic-driven (`startBargeIn`/`handleRealtimeEvent('barge')` paths intact).
4. Degradation: with no mic permission, no crash; grid falls back to the
   time-based `startVoiceGridWave` so the overlay isn't dead.
5. Cross-check that moving playback to the browser doesn't break the
   server-side CLI TTS (no-arg `/api/tts` unchanged).

---

## Settings + onboarding exposure (all three goals)

### Settings → Voice (new controls)
- **STT backend** dropdown (`stt_backend`): "Local Whisper", "NVIDIA Nemotron",
  "Cloud (OpenAI-compatible)". Grey + label unavailable ones using the exact
  pattern already at `ui.html:7374` (reads `/api/voice/status.backends`),
  e.g. "NVIDIA Nemotron (not installed — Download)".
- **TTS backend** dropdown (`tts_backend`): "Kokoro (local)", "Piper (local)",
  "Cloud (OpenAI / ElevenLabs)". Cloud reveals voice + model fields.
- **Cloud voice config** sub-panel (revealed when a cloud backend is picked):
  API base, provider, model, voice id, and a "Use my chat-brain key" checkbox
  that reads from `brain_keys.json`.
- Wire `onchange` handlers next to the existing ones at `ui.html:7400`–`:7451`,
  each calling `saveSetting(...)` then `POST /api/voice/reload`.

### Onboarding (voice step `data-step="4"`, `ui.html:3494`)
- Keep the simple "talk + listen / text only" choice as-is for first-run
  simplicity.
- After "Yes", in the existing `#ob-voice-picker` (`:3514`), add a single
  unobtrusive "Engine: Local (recommended) · Cloud" toggle. Default Local.
  Nemotron is **not** surfaced in onboarding (it's an advanced, install-on-
  demand option) — only in Settings, to avoid a heavy download mid-onboarding.
- The "Test voice" button (`ob-voice-test`, `:3533`) should exercise the
  selected TTS backend so cloud-TTS users hear the real cloud voice before
  committing.

---

## Dependency summary

| Backend | New pip deps | Bundled? | LITE? |
|---|---|---|---|
| Whisper (existing) | — | yes (existing) | yes |
| Kokoro/Piper (existing) | — | yes (existing) | yes |
| Nemotron | `nemo_toolkit[asr]`, `soundfile` (heavy) | **no** (download-on-demand) | **no** |
| Cloud STT/TTS | none (use stdlib `urllib`+`wave`) | yes (code only) | yes |

---

## Recommended build order

1. **Backend registry + adapters** (Whisper/Kokoro wrappers). Pure refactor,
   no behavior change, no new deps. Ship + verify defaults unchanged.
2. **Cloud backends** (Goal 2). Highest value-per-effort, zero new deps,
   reuses existing key infra, works on the 8 GB box with no VRAM cost.
3. **Reactive visualizer** (Goal 3, Option A). Also fixes the silent-Speak bug.
4. **Nemotron** (Goal 1) — LAST, and only after a live benchmark on the actual
   RTX 5060 proves it beats faster-whisper at acceptable VRAM. May be cut if
   the numbers don't justify the dependency weight.

## Feasibility flags (the honest bits)

- **Nemotron on GPU + local LLM simultaneously is the real risk on 8 GB.**
  Plan for it to be cloud-brain-only or CPU-only. Do not ship a claim that it
  runs alongside the LLM until measured.
- **`nemo_toolkit` fights the small-bundle / LITE goal.** Keep it opt-in.
- **All Goal-1 specifics are UNVERIFIED** (web access was blocked). Confirm the
  repo id, license, ONNX availability, and streaming API against the live model
  card before writing the backend for real.
- **Cloud voice may be unusable on the IIT MITM-SSL network** — surface that
  instead of hanging.
