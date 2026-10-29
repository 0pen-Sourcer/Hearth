// The sound of a voice call with the PC: the mic as 16 kHz frames for the PC's
// own speech recognizer, and the PC's own voice played back. Voice mode in the
// desktop window and the call screen in the phone app both use this file; the
// phone app keeps a copy (hearth-relay scripts/sync_call_audio.py).
//
// The voice plays through a WebRTC loop inside the page (one peer connection
// sending to another, into an <audio> element), because Chromium cancels echo
// only for sound that arrives that way: played straight from Web Audio, the
// mic would hear the speaker and the PC would answer itself.
var CallAudio = (function () {
  const RATE = 16000, FRAME = 960;    // the mic goes as 60 ms frames at the rate the recognizer takes
  const LEAD = 0.12;                  // a reply starts this far ahead of now, to ride out jitter
  const MIC = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };

  // Standard base64 (the PC reads the audio with it).
  function std64(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  function from64(text) {
    const s = atob(text);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  // Each output sample is the mean of the input samples it covers: enough of a
  // low-pass for speech, and the same code here and in the worklet.
  const DOWN = `
    this.ratio = sampleRate / ${RATE}; this.acc = 0; this.cnt = 0; this.pos = 0;
    this.out = new Int16Array(${FRAME}); this.n = 0;
    this.take = (ch, emit) => {
      for (let i = 0; i < ch.length; i++) {
        this.acc += ch[i]; this.cnt++; this.pos += 1;
        if (this.pos >= this.ratio) {
          this.pos -= this.ratio;
          const v = this.acc / this.cnt; this.acc = 0; this.cnt = 0;
          this.out[this.n++] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
          if (this.n === ${FRAME}) { emit(this.out); this.out = new Int16Array(${FRAME}); this.n = 0; }
        }
      }
    };`;
  const WORKLET = `class InglenMic extends AudioWorkletProcessor {
    constructor() { super(); ${DOWN} }
    process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.take(ch, (f) => this.port.postMessage(f.buffer, [f.buffer])); return true; }
  }
  registerProcessor('inglen-mic', InglenMic);`;
  function Down(sampleRate) { new Function('sampleRate', DOWN).call(this, sampleRate); }

  const can = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia
    && (window.AudioContext || window.webkitAudioContext));

  function constraints(deviceId) {
    return { audio: Object.assign({}, MIC, deviceId ? { deviceId: { exact: deviceId } } : {}) };
  }

  function level(an) {
    if (!an) return 0;
    const d = new Float32Array(an.fftSize);
    an.getFloatTimeDomainData(d);
    let s = 0;
    for (let i = 0; i < d.length; i++) s += d[i] * d[i];
    return Math.min(1, Math.sqrt(s / d.length) * 5);
  }

  async function loopback(stream, el) {
    const a = new RTCPeerConnection(), b = new RTCPeerConnection();
    const close = () => { try { a.close(); } catch {} try { b.close(); } catch {} };
    try {
      a.onicecandidate = (e) => { if (e.candidate) b.addIceCandidate(e.candidate).catch(() => {}); };
      b.onicecandidate = (e) => { if (e.candidate) a.addIceCandidate(e.candidate).catch(() => {}); };
      const track = new Promise((res) => { b.ontrack = (e) => res(e.streams[0] || new MediaStream([e.track])); });
      for (const t of stream.getAudioTracks()) a.addTrack(t, stream);
      await a.setLocalDescription(await a.createOffer());
      await b.setRemoteDescription(a.localDescription);
      const answer = await b.createAnswer();
      await b.setLocalDescription(answer);
      // Nothing but this device in between: the voice goes at full quality, not phone-call quality.
      await a.setRemoteDescription({ type: 'answer', sdp: answer.sdp.replace(/(a=fmtp:\d+ [^\r\n]*)/, '$1;maxaveragebitrate=128000') });
      await new Promise((res, rej) => {
        const t = setTimeout(() => rej(new Error('loop')), 5000);
        const look = () => {
          if (a.connectionState === 'connected') { clearTimeout(t); res(); }
          else if (a.connectionState === 'failed') { clearTimeout(t); rej(new Error('loop')); }
        };
        a.onconnectionstatechange = look; look();
      });
      el.srcObject = await track;
      await el.play();
      return { a, b, el };
    } catch (e) { close(); throw e; }
  }

  // One call's sound. Make it on a click or tap so it may play; then mic(),
  // out(el) and frames(fn), in that order.
  function make() {
    const AC = window.AudioContext || window.webkitAudioContext;
    const A = { ctx: new AC({ latencyHint: 'interactive' }), stream: null, micAn: null, outAn: null, gain: null,
                aec: false, loop: null, t: 0, sources: new Set(), node: null, src: null, closed: false };
    A.ctx.resume().catch(() => {});

    // The microphone, echo cancelled. Throws when it isn't allowed or isn't there.
    A.mic = async (deviceId) => {
      A.stream = await navigator.mediaDevices.getUserMedia(constraints(deviceId));
      return A.stream;
    };

    // Where the PC's voice plays: the loop where the browser has it, else straight out.
    A.out = async (el) => {
      const ctx = A.ctx;
      A.gain = ctx.createGain();
      A.outAn = ctx.createAnalyser(); A.outAn.fftSize = 512;
      A.gain.connect(A.outAn);
      A.aec = false;
      if (window.RTCPeerConnection && el) {
        const dest = ctx.createMediaStreamDestination();
        A.gain.connect(dest);
        try { A.loop = await loopback(dest.stream, el); A.aec = true; }
        catch { A.gain.disconnect(dest); A.loop = null; }
      }
      if (!A.aec) A.gain.connect(ctx.destination);     // no loop: it plays, and headphones keep it out of the mic
    };

    // The mic as 60 ms frames of 16-bit PCM at 16 kHz, each handed to fn(Uint8Array).
    A.frames = async (fn) => {
      const ctx = A.ctx;
      A.src = ctx.createMediaStreamSource(A.stream);
      A.micAn = ctx.createAnalyser(); A.micAn.fftSize = 512;
      A.src.connect(A.micAn);
      const quiet = ctx.createGain(); quiet.gain.value = 0; quiet.connect(ctx.destination);
      let node = null;
      if (ctx.audioWorklet && window.AudioWorkletNode) {
        const url = URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' }));
        try {
          await ctx.audioWorklet.addModule(url);
          node = new AudioWorkletNode(ctx, 'inglen-mic');
          node.port.onmessage = (e) => fn(new Uint8Array(e.data));
        } catch { node = null; } finally { URL.revokeObjectURL(url); }
      }
      if (!node) {                                    // an older browser: the same, on the main thread
        node = ctx.createScriptProcessor(4096, 1, 1);
        const d = new Down(ctx.sampleRate);
        node.onaudioprocess = (e) => d.take(e.inputBuffer.getChannelData(0), (f) => fn(new Uint8Array(f.buffer)));
      }
      A.src.connect(node); node.connect(quiet);
      A.node = node;
    };

    // Another microphone (or the same one again, after it stopped), without ending the call.
    A.remic = async (deviceId) => {
      const s = await navigator.mediaDevices.getUserMedia(constraints(deviceId));
      if (A.closed) { s.getTracks().forEach(t => t.stop()); return; }
      if (A.stream) A.stream.getTracks().forEach(t => t.stop());
      A.stream = s;
      if (A.src) A.src.disconnect();
      A.src = A.ctx.createMediaStreamSource(s);
      A.src.connect(A.micAn); A.src.connect(A.node);
    };

    // A frame of the PC's voice: {sr, data} with data base64 16-bit PCM.
    A.down = (m) => {
      if (A.closed || !m.data || !A.gain) return;
      const bytes = from64(m.data);
      const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1);
      if (!pcm.length) return;
      const buf = A.ctx.createBuffer(1, pcm.length, m.sr || 24000);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
      const s = A.ctx.createBufferSource();
      s.buffer = buf; s.connect(A.gain);
      const now = A.ctx.currentTime;
      if (A.t < now + 0.02) A.t = now + LEAD;
      s.start(A.t);
      A.t += buf.duration;
      A.sources.add(s);
      s.onended = () => A.sources.delete(s);
    };

    // Stop what is playing and what is queued to play.
    A.flush = () => {
      for (const s of A.sources) { try { s.stop(); } catch {} }
      A.sources.clear();
      A.t = 0;
    };

    A.playing = () => !A.closed && A.t > A.ctx.currentTime + 0.01;
    A.micLevel = () => level(A.micAn);
    A.outLevel = () => level(A.outAn);
    A.ended = () => !!A.stream && A.stream.getAudioTracks().some(t => t.readyState === 'ended');
    A.label = () => { const t = A.stream && A.stream.getAudioTracks()[0]; return t ? t.label : ''; };

    A.close = () => {
      if (A.closed) return;
      A.closed = true;
      A.flush();
      try { A.stream && A.stream.getTracks().forEach(t => t.stop()); } catch {}
      try { A.node && A.node.disconnect(); } catch {}
      if (A.loop) { try { A.loop.a.close(); A.loop.b.close(); } catch {} A.loop.el.srcObject = null; }
      try { A.ctx.close(); } catch {}
    };
    return A;
  }

  // A second of quiet as frames, so the recognizer hears a sentence end after mute.
  function quietSecond() {
    const z = std64(new Uint8Array(FRAME * 2));
    return Array.from({ length: 16 }, () => z);
  }

  return { RATE, FRAME, MIC, can, make, std64, from64, quietSecond };
})();
