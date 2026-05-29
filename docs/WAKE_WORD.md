# Wake word — how it works, how to tune it

The "Hey Jarvis" listener that pops the chat window when called.

---

## What it does

Background thread that lives inside the tray process. Listens to your default mic; when you say a wake phrase, fires `_open_desktop_window()` which brings Hearth to the front.

**Toggle it on:** right-click the tray icon → click **Wake word: off**. It flips to **on**. Click again to disable. Or launch the tray with auto-on:
```powershell
python -m hearth.tray --open --wake
```

---

## How it stays cheap on resources

Three layers of gating make it near-free at idle:

| Gate | What it does | Cost at idle |
|---|---|---|
| **1. Audio block read** | `sounddevice.InputStream` at 16kHz, 100ms blocks | ~0.5% CPU (one-shot audio capture) |
| **2. Energy threshold** | Each block: compute RMS. If quiet (`< 0.012`), skip everything else. Just buffer empty + go back to sleep. | Effectively zero — no model inference |
| **3. Whisper transcribe** | ONLY when energy crossed threshold AND a sustained quiet tail (silence > 0.7s) follows. So one transcription per spoken utterance, not constant. | Brief CPU spike (~150ms per utterance on `base.en`) |

**Net:** if you're not talking, the listener uses ~0% CPU. If you ARE talking, one whisper inference per utterance.

Default Whisper model is `base.en` (74M params, ~150ms per second of audio on CPU). On `cuda` it's 5-10× faster. Set `JARVIS_STT_DEVICE=cuda` env var or use the Settings panel.

---

## Default wake phrases

```
"jarvis"
"hey jarvis"
"hi jarvis"
"yo jarvis"
"wake up jarvis"
"jarvis wake up"
```

Matched after normalizing (lowercase, strip punctuation, collapse spaces). Any of these in the transcribed utterance fires the wake action.

**Customize:**
```powershell
$env:JARVIS_WAKE_PHRASES = "jarvis,hearth,computer"   # comma-separated
```

Or override at code level in `hearth/wake.py` `DEFAULT_WAKE_PHRASES`.

---

## Tuning constants

In `hearth/wake.py` at the top:

```python
SAMPLE_RATE      = 16000        # whisper's native rate; don't change
WINDOW_SEC       = 2.5          # rolling audio buffer
ENERGY_THRESHOLD = 0.012        # RMS to wake the transcriber
SILENCE_TAIL_S   = 0.7          # quiet after voice to consider utterance done
COOLDOWN_S       = 4.0          # don't re-fire within N seconds of last wake
```

**Common tweaks:**

| Problem | Fix |
|---|---|
| Wake never fires even when you talk loud | Lower `ENERGY_THRESHOLD` to `0.006` |
| Random TV noise triggers wake | Raise `ENERGY_THRESHOLD` to `0.020` |
| Cuts off long utterances | Raise `WINDOW_SEC` to `4.0` |
| Fires multiple times in quick succession | Raise `COOLDOWN_S` to `8.0` |
| Misses "Jarvis" if said fast | Try `tiny.en` whisper model (faster, less accurate) via Settings |

---

## What happens when wake fires

1. `hearth/wake.py` → `WakeListener._maybe_wake()` matches a phrase
2. Calls the `on_wake(phrase)` callback registered by the tray
3. Tray callback: `print(f"[hearth.tray] wake: '{phrase}'")` + `_open_desktop_window()`
4. Sets `_last_wake_ts = time.time()` to start the cooldown timer
5. Cooldown prevents re-fires for `COOLDOWN_S` seconds (default 4s)

Logs land at `~/Jarvis/logs/hearth_tray.log` when running from the bundled exe (no console), or stdout in dev mode.

---

## Architectural notes

We deliberately did NOT use:

- **openWakeWord / Porcupine** — both work, but adding another model + license / activation flow felt heavy when whisper is already loaded for STT. Energy gate + whisper poll on detected voice is "free enough."
- **Always-on whisper poll** — would be 10-30% CPU constant. Energy gate is the key.
- **Cloud wake word** — defeats local-first.

If you want a real keyword-spotter model (better in noisy environments), `openwakeword` is a 2-hour port — replace `WakeListener._loop()`'s energy gate with the OpenWakeWord predict loop.

---

## Wake word vs voice mode — different things

| Wake word | Voice mode |
|---|---|
| Lives in the **tray process** | Lives in the **chat window** |
| Always-on when toggled (background thread) | Only active while the voice overlay is showing |
| Fires window-open action on match | Full conversational STT/TTS loop |
| One whisper call per utterance | Whisper + Kokoro running per turn |
| Match against fixed phrases | Send all transcribed text as a chat message |

Use wake word to summon the app. Use voice mode to actually have a conversation.

---

## What still doesn't work

- **No "Jarvis, what's my GPU temp" via wake word** — wake word opens the window, then you have to tap mic in voice mode. Two-step. A future version could auto-enter voice mode + push the transcribed wake utterance as the first message, but that needs careful handling of false positives (a TV saying "jarvis" shouldn't query your GPU).
- **Mac / Linux** — listener uses `sounddevice` (cross-platform) + whisper (cross-platform), so the audio side works. But the wake action calls `_open_desktop_window()` which currently relies on PyWebView's Windows path. Port for v0.6.

---

## Stopping wake cleanly

- Right-click tray → toggle off
- Quit the tray app entirely (right-click → Quit)
- `taskkill /F /IM Hearth.exe` (nuclear)

Wake state is **session-only** — it doesn't persist across tray restarts. If you want it always-on, edit `hearth/tray.py` to default `wake_state["on"] = True` and call `_start_wake()` in `main()` unconditionally. Or add `--wake` to the `Hearth.lnk` Startup shortcut's arguments via `hearth/install_shortcuts.py`.
