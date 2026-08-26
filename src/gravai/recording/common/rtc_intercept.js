(() => {
  const WS_URL = "{{WS_URL}}";
  const WORKLET_CODE = {{WORKLET_CODE}};

  // Whether to publish one mixed track of everything this browser hears, on top
  // of the per-receiver ones.
  //
  // Meet needs it. It routes a handful of audio receivers and reuses them
  // between speakers, so a receiver is not a person, and slicing has nothing to
  // cut: it looks for a single `track_mainAudio*.wav` and cuts that per
  // participant using the speaking timeline from the VAD observer. Teams names
  // its own tracks `mainAudio-…` and needs no mix, so it leaves this false and
  // that path is byte-for-byte what it has always been.
  //
  // Compared as a string so a provider that forgets to substitute it gets no mix
  // rather than a syntax error at document start.
  const MAIN_MIX = "{{MAIN_MIX}}" === "true";

  // Built on first use, never at document start. This script is injected into
  // every frame, and registering a blob URL inside the opaque-origin frames
  // Google Meet creates while loading crashes the renderer outright - the page
  // dies during navigation, before anything can be recorded. By the time a track
  // arrives we are in a frame that owns a real origin, where this is safe.
  let workletUrl = null;
  function getWorkletUrl() {
    if (workletUrl === null) {
      workletUrl = URL.createObjectURL(
        new Blob([WORKLET_CODE], { type: "application/javascript" })
      );
    }
    return workletUrl;
  }

  const handlers = new Map();

  function openSocket(trackId, sampleRate, channels) {
    const url = new URL(WS_URL);
    url.searchParams.set("track", trackId);
    url.searchParams.set("sr", String(sampleRate));
    url.searchParams.set("ch", String(channels));
    const ws = new WebSocket(url.toString());
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "start", trackId, sampleRate, channels }));
    };
    return ws;
  }

  // Streams one worklet node's PCM to the audio server under `trackId`, holding
  // frames until the socket is open. Shared by the per-receiver tracks and by the
  // mix, which differ only in what feeds them.
  //
  // `level` is the counter watchLevel reads; the loudest sample of every chunk
  // that goes out lands there on its way past.
  function pipe(node, trackId, sampleRate, channels, level) {
    const ws = openSocket(trackId, sampleRate, channels);
    const queue = [];
    let wsReady = false;

    ws.addEventListener("open", () => {
      wsReady = true;
      console.log("[rtc-intercept] ws open", trackId);
      while (queue.length) {
        ws.send(queue.shift());
      }
    });
    ws.addEventListener("error", (event) => {
      console.log("[rtc-intercept] ws error", event);
    });

    node.port.onmessage = (event) => {
      const data = event.data;
      if (!data) return;
      let payload;
      if (data instanceof ArrayBuffer) {
        payload = data;
      } else if (data.buffer) {
        payload = data.buffer;
        // Every sixteenth sample, which at 48kHz is still 750 of them per chunk -
        // enough to tell digital silence from anything at all, and cheap enough
        // to sit in the path every frame takes.
        if (level) {
          for (let i = 0; i < data.length; i += 16) {
            const magnitude = Math.abs(data[i]);
            if (magnitude > level.peak) level.peak = magnitude;
          }
        }
      } else {
        return;
      }
      if (!wsReady) {
        queue.push(payload);
      } else {
        ws.send(payload);
      }
    };

    return ws;
  }

  // Every collector node is explicitly mono, so the worklet is handed one channel
  // and the `ch=1` announced to the audio server is what actually arrives.
  //
  // Left at the defaults the node takes whatever the source brings - two channels
  // for a remote WebRTC track - and posts them interleaved, while the server
  // still writes a mono header: the file comes out twice as long and plays back
  // in slow motion, which is what the tempo correction in ws_audio_server used to
  // undo. Deciding the channel count here removes both the doubling and the
  // correction.
  function monoCollector(ctx) {
    return new AudioWorkletNode(ctx, "pcm-collector", {
      channelCount: 1,
      channelCountMode: "explicit",
      channelInterpretation: "speakers",
    });
  }

  function createContext() {
    return (async () => {
      const ctx = new AudioContext({ sampleRate: 48000 });
      await ctx.audioWorklet.addModule(getWorkletUrl());
      if (ctx.state !== "running") {
        await ctx.resume();
      }
      return ctx;
    })();
  }

  // One context for everything when there is a mix to build: nodes can only
  // connect to nodes of the same AudioContext, so every receiver has to land in
  // the one the mixer lives in. The promise is stored rather than the context, so
  // two tracks arriving together cannot each build one.
  let sharedContext = null;
  function audioContext() {
    if (!MAIN_MIX) {
      return createContext();
    }
    if (sharedContext === null) {
      sharedContext = createContext();
    }
    return sharedContext;
  }

  function trackFlags(track) {
    if (!track) return "";
    return ` (enabled=${track.enabled} muted=${track.muted} state=${track.readyState})`;
  }

  // Reports when a track starts and stops carrying audio, with the flags that
  // explain it.
  //
  // Silence is the one failure here that leaves no trace of itself: the graph
  // keeps running, the sockets stay healthy and the files keep their full
  // length, so a recording of nothing is indistinguishable from a recording of a
  // quiet room until someone opens the wav. `enabled` is the flag to read first -
  // a conferencing client clears it on the receivers it is not currently routing
  // to a speaker, and a cleared track feeds silence to every consumer it has.
  const SILENCE_GRACE_MS = 15000;
  function watchLevel(trackId, level, track) {
    let audible = null;
    let quietSince = Date.now();
    return setInterval(() => {
      if (level.peak > 0) {
        level.peak = 0;
        quietSince = Date.now();
        if (audible !== true) {
          audible = true;
          console.log(`[rtc-intercept] ${trackId} is carrying audio${trackFlags(track)}`);
        }
        return;
      }
      if (audible !== false && Date.now() - quietSince >= SILENCE_GRACE_MS) {
        audible = false;
        console.log(
          `[rtc-intercept] ${trackId} has written nothing but silence for ` +
          `${SILENCE_GRACE_MS / 1000}s${trackFlags(track)}`
        );
      }
    }, 2000);
  }

  // The mixed track, built once and fed by every receiver afterwards. Several
  // sources connected to one node input sum, which is all a mixer is.
  let mainMix = null;
  function mainMixInput(ctx) {
    if (mainMix === null) {
      const node = monoCollector(ctx);
      // Nothing should be audible - the graph is pulled by the destination, and
      // a silent gain is what keeps it pulled without playing anything.
      const silence = new GainNode(ctx, { gain: 0 });
      node.connect(silence).connect(ctx.destination);

      // The name is load-bearing: slicing finds the track to cut by globbing
      // `track_mainAudio*.wav`, and reads its start and end from the sidecar key
      // that filename implies.
      const trackId = "mainAudio-" + String(Date.now() % 100000).padStart(5, "0");
      console.log("[rtc-intercept] mixing every receiver into", trackId);
      const level = { peak: 0 };
      pipe(node, trackId, ctx.sampleRate, 1, level);
      // No track of its own: the mix is silent exactly when every receiver
      // feeding it is, and each of those reports its own flags.
      watchLevel(trackId, level, null);
      mainMix = node;
    }
    return mainMix;
  }

  async function attachTrack(track) {
    if (track.kind !== "audio") return;
    if (handlers.has(track.id)) return;

    console.log("[rtc-intercept] attaching track", track.id);

    const ctx = await audioContext();

    const settings = track.getSettings ? track.getSettings() : {};
    const trackSampleRate = settings.sampleRate || null;
    console.log("[rtc-intercept] AudioContext sampleRate", ctx.sampleRate, "track sampleRate", trackSampleRate);

    // Tapped through a clone, never the receiver's own track.
    //
    // `enabled` belongs to the track object rather than to the stream behind it,
    // and clearing it makes that object hand silence to every consumer it has -
    // this tap included. Meet clears it constantly: it keeps a handful of audio
    // receivers and switches them between speakers, so the ones it is not routing
    // right now are switched off, and a bot that reads them writes full-length
    // wav files of zeros. Nothing fails on the way: the graph runs, the sockets
    // stay open, the sample counts are right, and whether a call comes back with
    // audio or with silence depends on which receivers Meet happened to leave on.
    //
    // A clone is fed by the same receiver but carries its own `enabled`, so what
    // the page does to its copy cannot reach ours.
    const tap = track.clone();
    tap.enabled = true;
    const stream = new MediaStream([tap]);

    // A remote track feeds WebAudio only while something is consuming it. Without
    // an element attached the graph runs and delivers zeros: full-length wav
    // files, correct sample counts, silence throughout - and it depends on
    // whether Meet happens to be playing that track itself, so the same code
    // captures audio on one call and nothing on the next. Muted, so the bot stays
    // silent in a container with no output device anyway.
    const sink = new Audio();
    sink.srcObject = stream;
    sink.muted = true;
    sink.play().catch(() => {});

    const source = new MediaStreamAudioSourceNode(ctx, { mediaStream: stream });
    const node = monoCollector(ctx);
    const gain = new GainNode(ctx, { gain: 0 });

    source.connect(node);
    node.connect(gain).connect(ctx.destination);

    // Mono because monoCollector says so, whatever the track carries.
    const level = { peak: 0 };
    const ws = pipe(node, track.id, ctx.sampleRate, 1, level);
    // Reported against the receiver's track, not the clone: the clone is the one
    // that stays on, so the flags worth having in the log are the ones the page
    // is setting.
    const levelTimer = watchLevel(track.id, level, track);

    if (MAIN_MIX) {
      source.connect(mainMixInput(ctx));
    }

    track.onended = () => {
      try {
        ws.send(JSON.stringify({ type: "stop", trackId: track.id }));
      } catch (e) {

      }
      try {
        ws.close();
      } catch (e) {

      }
      try {
        // Detached rather than torn down: with a mix the context belongs to every
        // receiver and to the mixer, so closing it here would end the recording
        // the moment any one participant's track stopped.
        source.disconnect();
        node.disconnect();
      } catch (e) {

      }
      if (!MAIN_MIX) {
        try {
          ctx.close();
        } catch (e) {

        }
      }
      try {
        clearInterval(levelTimer);
      } catch (e) {

      }
      try {
        sink.pause();
        sink.srcObject = null;
        // The receiver's track ended, so the clone has no source left to read;
        // stopping it releases the decoder this tap was holding open.
        tap.stop();
      } catch (e) {

      }
      handlers.delete(track.id);
    };

    // The sink and the clone are held here as well: dropped on the floor they
    // would be collected, and the track would go quiet again mid-call.
    handlers.set(track.id, { ctx, ws, node, sink, tap, levelTimer });
  }

  const OriginalRTCPeerConnection = window.RTCPeerConnection;
  window.RTCPeerConnection = new Proxy(OriginalRTCPeerConnection, {
    construct(target, args) {
      const pc = new target(...args);
      console.log("[rtc-intercept] RTCPeerConnection created");
      pc.addEventListener("track", (event) => {
        if (event && event.track) {
          attachTrack(event.track);
        }
      });
      return pc;
    },
  });
})();
