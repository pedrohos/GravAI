/* Real browser ICE, DTLS, SRTP/Opus and RTCP; no injected VAD events. */
let pc;
function observeConnection(report) {
  for (const event of ['connectionstatechange', 'iceconnectionstatechange',
                       'icegatheringstatechange', 'signalingstatechange']) {
    pc.addEventListener(event, () => report({type:'connection', event,
      connectionState:pc.connectionState, iceConnectionState:pc.iceConnectionState,
      iceGatheringState:pc.iceGatheringState, signalingState:pc.signalingState}));
  }
}
async function gather() {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(Error('ICE gathering timed out')), 15000);
    pc.addEventListener('icegatheringstatechange', () => {
      if (pc.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
    });
  });
}
window.collectStats = async () => {
  const stats = [];
  for (const report of (await pc.getStats()).values()) {
    if (['inbound-rtp', 'outbound-rtp', 'candidate-pair', 'transport', 'codec'].includes(report.type))
      stats.push(report);
  }
  return stats;
};
window.makeOffer = async scenario => {
  pc = new RTCPeerConnection({iceServers: []});
  observeConnection(data => window.traceConnection?.(data));
  const ctx = new AudioContext({sampleRate: 48000});
  await ctx.resume();
  const voices = [];
  for (const p of scenario.participants) {
    const destination = ctx.createMediaStreamDestination();
    const gain = ctx.createGain(); gain.gain.value = 0;
    gain.connect(destination);
    let source;
    if (p.audio) {
      const response = await fetch(`/fixture/${encodeURIComponent(p.id)}`);
      if (!response.ok) throw Error(`Audio fixture unavailable: ${p.id}`);
      source = ctx.createBufferSource();
      source.buffer = await ctx.decodeAudioData(await response.arrayBuffer());
    } else {
      source = ctx.createOscillator(); source.frequency.value = p.frequency;
    }
    source.connect(gain);
    const track = destination.stream.getAudioTracks()[0];
    pc.addTrack(track, destination.stream);
    voices.push({p, source, gain, track});
  }
  window.startMedia = () => {
    const epoch = ctx.currentTime + .1;
    for (const {p, source, gain, track} of voices) {
      source.start(epoch);
      for (const [start, end] of p.turns) {
        gain.gain.setValueAtTime(p.audio ? 1 : .15, epoch + start);
        gain.gain.setValueAtTime(0, epoch + end);
      }
      source.stop(epoch + scenario.duration);
      // Models a media blackout, not UDP packet loss or ICE disconnection.
      const tick = setInterval(() => {
        const t = ctx.currentTime - epoch;
        track.enabled = !scenario.outages.some(([a,b]) => a <= t && t < b)
          && (p.presence === null || p.presence.some(([a,b]) => a <= t && t < b));
        if (t >= scenario.duration) clearInterval(tick);
      }, 20);
    }
    return Math.max(0, epoch - ctx.currentTime);
  };
  await pc.setLocalDescription(await pc.createOffer());
  await gather();
  return pc.localDescription.toJSON();
};
window.acceptAnswer = async sdp => {
  await pc.setRemoteDescription(sdp);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('WebRTC connection timed out')), 20000);
    const check = () => {
      if (pc.connectionState === 'connected') {clearTimeout(timer); resolve();}
      if (pc.connectionState === 'failed') {clearTimeout(timer); reject(Error('WebRTC failed'));}
    };
    pc.addEventListener('connectionstatechange', check); check();
  });
};
const join = document.getElementById('join');
if (join) join.onclick = async () => {
  const provider = document.body.dataset.provider;
  const name = document.querySelector('input').value;
  document.getElementById('room').textContent = 'Asking to join…';
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/signal?provider=${provider}`);
  let active = [], indicators = new Map(), tiles = new Map(), statsTimer;
  const animation = setInterval(() => {
    if (provider !== 'meet') return;
    for (const id of active) {
      const indicator = indicators.get(id);
      if (indicator) indicator.className = `level-${Math.floor(performance.now()/200)%4}`;
    }
  }, 200);
  let finished = false;
  function finish(text, tag = 'span', id = '') {
    if (finished) return;
    finished = true;
    clearInterval(animation); clearInterval(statsTimer);
    if (pc) pc.close();
    ws.close();
    const message = document.createElement(tag); message.textContent = text; message.id = id;
    document.getElementById('room').replaceChildren(message);
  }
  ws.onopen = () => ws.send(JSON.stringify({type:'join', name}));
  ws.onmessage = async event => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'denied') return provider === 'teams'
        ? finish('Your request to join was denied', 'h1', 'calling-retry-screen-title')
        : finish('Someone denied your request to join');
      if (msg.type === 'error') return finish(`Simulation failed: ${msg.message}`);
      if (msg.type === 'offer') {
        pc = new RTCPeerConnection({iceServers: []});
        observeConnection(data => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data));
        });
        pc.ontrack = event => {
          const audio = document.createElement('audio'); audio.autoplay = true;
          audio.srcObject = new MediaStream([event.track]); document.body.append(audio);
          audio.play().catch(error => console.error(error));
        };
        await pc.setRemoteDescription(msg.sdp);
        await pc.setLocalDescription(await pc.createAnswer()); await gather();
        ws.send(JSON.stringify({type:'answer', sdp:pc.localDescription.toJSON()}));
        const room = document.getElementById('room'); room.textContent = '';
        for (const p of msg.scenario.participants) {
          const tile = document.createElement('section'); tile.dataset.participantId = p.id;
          const span = document.createElement('span'); span.textContent = p.name;
          const label = document.createElement('div'); label.textContent = p.name;
          const indicator = document.createElement('div'); indicator.setAttribute('jsname','QgSmzd');
          indicator.id = p.id;
          if (provider === 'teams') {
            tile.setAttribute('data-tid', p.name);
            indicator.setAttribute('data-tid', 'voice-level-stream-outline');
            indicator.className = 'level idle';
            const root = msg.scenario.teams_shadow_dom ? tile.attachShadow({mode:'open'}) : tile;
            root.append(span, label, indicator);
          } else tile.append(span, label, indicator);
          if (p.presence === null || p.presence.some(([a,b]) => a === 0 && b > 0)) room.append(tile);
          tiles.set(p.id, tile); indicators.set(p.id, indicator);
        }
        statsTimer = setInterval(async () => {
          if (ws.readyState === WebSocket.OPEN)
            ws.send(JSON.stringify({type:'stats', reports:await window.collectStats()}));
        }, 1000);
      }
      if (msg.type === 'admitted') {
        const leave = document.createElement('button'); leave.textContent = 'Leave call';
        leave.onclick = () => finish(provider === 'teams' ? 'Did you leave by mistake?' : "You've left the meeting");
        document.getElementById('room').append(leave);
      }
      if (msg.type === 'activity') {
        const room = document.getElementById('room');
        for (const [id, tile] of tiles) {
          if (msg.present.includes(id)) {
            if (!tile.isConnected) room.append(tile);
          } else tile.remove();
        }
        active = msg.active;
        if (provider === 'teams') for (const [id, indicator] of indicators)
          indicator.className = active.includes(id) ? 'level idle speaking' : 'level idle';
      }
      if (msg.type === 'ended') {
        ws.send(JSON.stringify({type:'stats', reports:await window.collectStats()}));
        if (provider === 'teams') finish(msg.reason === 'removed'
          ? "You've been removed from this meeting" : 'Did you leave by mistake?', msg.reason === 'removed' ? 'h1' : 'span');
        else finish(msg.reason === 'removed' ? "You've been removed from the meeting"
          : msg.reason === 'left' ? "You've left the meeting" : 'Your host ended the meeting for everyone');
      }
    } catch (error) { console.error(error); finish(`Simulation failed: ${error.message}`); }
  };
  ws.onclose = () => finish(provider === 'teams' ? 'Did you leave by mistake?' : "You've left the meeting");
};
