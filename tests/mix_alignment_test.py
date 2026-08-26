"""Guards the recorded tracks against drifting out of real time, silence, and
the wrong pitch.

The mix exists because Meet reuses its audio receivers between speakers, so
slicing cuts one mixed track per participant using the speaking timeline. That
only works if a second of call is a second of audio: slicing turns event
timestamps into offsets into this file, and a mix running at half rate puts every
offset past the end of it. The symptom is not an error - it is a participant
track full of silence, which is why this is worth a test.

Three traps it guards. Giving the mix node explicit mono options, which looks
right next to the ch=1 announced to the server, halves the frames it posts; the
mix has to be built with the same default node options as a per-receiver track.
A remote track only feeds WebAudio while an element is consuming it - without the
muted `Audio()` sink the graph runs and writes full-length files of zeros, which
reaches transcription as an empty call rather than as an error. And the page can
switch a receiver's track off at any moment - Meet does it whenever it stops
routing that receiver to a speaker - which silences every consumer of that track
object, so the tap has to read a clone that carries its own `enabled`.

Needs a browser and writes wavs, so it is opt-in:

    GRAVAI_TEST_BROWSER=1 uv run pytest tests/mix_alignment_test.py
"""

import array
import json
import os
import pathlib
import time
import wave

import pytest

from gravai.config.settings import get_settings
from gravai.recording.session_audio_server import EPHEMERAL_PORT, SessionAudioServer
from gravai.recording.utils import _load_text

pytestmark = pytest.mark.skipif(
    not os.environ.get("GRAVAI_TEST_BROWSER"),
    reason="GRAVAI_TEST_BROWSER is not set",
)

# Two tones over a peer connection inside one page, so `ontrack` fires and the
# intercept attaches to a genuine remote track rather than a synthesised one.
LOOPBACK = """
async () => {
  const ctx = new AudioContext({ sampleRate: 48000 });
  await ctx.resume();
  const dest = ctx.createMediaStreamDestination();
  // A single tone, so what comes out can be checked against the pitch that went
  // in: a track written at the wrong channel count plays back at twice the rate.
  const osc = ctx.createOscillator();
  osc.frequency.value = 440;
  const gain = ctx.createGain();
  gain.gain.value = 0.2;
  osc.connect(gain).connect(dest);
  osc.start();
  const pc1 = new RTCPeerConnection();
  const pc2 = new RTCPeerConnection();
  pc1.onicecandidate = (e) => e.candidate && pc2.addIceCandidate(e.candidate);
  pc2.onicecandidate = (e) => e.candidate && pc1.addIceCandidate(e.candidate);
  for (const track of dest.stream.getAudioTracks()) pc1.addTrack(track, dest.stream);
  const offer = await pc1.createOffer();
  await pc1.setLocalDescription(offer);
  await pc2.setRemoteDescription(offer);
  const answer = await pc2.createAnswer();
  await pc2.setLocalDescription(answer);
  await pc1.setRemoteDescription(answer);
  return true;
}
"""

# Keeps every remote track the page receives within reach, so a test can switch
# them off the way a conferencing client does.
WATCH_RECEIVED = """
(() => {
  window.__received = [];
  const Original = window.RTCPeerConnection;
  window.RTCPeerConnection = new Proxy(Original, {
    construct(target, args) {
      const pc = new target(...args);
      pc.addEventListener("track", (event) => {
        if (event.track) window.__received.push(event.track);
      });
      return pc;
    },
  });
})();
"""

RECORD_SECONDS = 12.0
SWITCH_OFF_AFTER = 4.0


def _peak(path: pathlib.Path, from_second: float = 0.0) -> float:
    with wave.open(str(path)) as wav:
        rate = wav.getframerate()
        samples = array.array("h")
        samples.frombytes(wav.readframes(wav.getnframes()))
    samples = samples[int(from_second * rate):]
    window = 24000
    peaks = [
        (sum(s * s for s in samples[i:i + window]) / window) ** 0.5 / 32768
        for i in range(0, max(0, len(samples) - window), window)
    ]
    return max(peaks) if peaks else 0.0


def _record_loopback(tmp_path, switch_off_after: float | None = None) -> str:
    """Records the loopback for a few seconds and returns the session directory.

    With `switch_off_after`, every track the page received is disabled that many
    seconds in, which is what silenced the tap before it read a clone.
    """
    from playwright.sync_api import sync_playwright

    settings = get_settings()
    worklet_js = _load_text(settings.AUDIO_WORKLET_JS_PATH)
    intercept_template = _load_text(settings.RTC_INTERCEPT_JS_PATH)
    output_dir = str(tmp_path)

    with SessionAudioServer(
        session_id="mix-alignment", output_dir=output_dir, host="127.0.0.1", port=EPHEMERAL_PORT
    ) as server:
        intercept_js = (
            intercept_template
            .replace("{{WS_URL}}", server.ws_url)
            .replace("{{WORKLET_CODE}}", json.dumps(worklet_js))
            .replace("{{MAIN_MIX}}", "true")
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                channel="chrome",
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    "--autoplay-policy=no-user-gesture-required",
                    # Chrome refuses a page on a public origin a socket to a local
                    # address; the recorder passes this for the same reason.
                    "--disable-features=LocalNetworkAccessChecks",
                ],
                ignore_default_args=["--mute-audio"],
            )
            context = browser.new_context()
            context.add_init_script(intercept_js)
            context.add_init_script(WATCH_RECEIVED)
            page = context.new_page()
            page.goto("https://example.com", wait_until="domcontentloaded")
            started = time.time()
            page.evaluate(LOOPBACK)
            if switch_off_after is None:
                page.wait_for_timeout(RECORD_SECONDS * 1000)
            else:
                page.wait_for_timeout(switch_off_after * 1000)
                disabled = page.evaluate(
                    "window.__received.map((t) => { t.enabled = false; return t.id; })"
                )
                assert disabled, "the page received no track to switch off"
                page.wait_for_timeout((RECORD_SECONDS - switch_off_after) * 1000)
            elapsed = time.time() - started
            context.close()
            browser.close()

    pathlib.Path(output_dir, "elapsed.txt").write_text(str(elapsed))
    return output_dir


def test_the_mix_runs_in_real_time(tmp_path):
    output_dir = _record_loopback(tmp_path)
    elapsed = float(pathlib.Path(output_dir, "elapsed.txt").read_text())

    sidecar = json.loads(pathlib.Path(output_dir, "session_sidecar.json").read_text())
    tracks = sidecar["tracks"]
    main = [key for key in tracks if key.startswith("mainAudio-")]
    assert main, f"the mix published no track; slicing globs for one: {list(tracks)}"

    for key, meta in tracks.items():
        with wave.open(str(pathlib.Path(output_dir, meta["wav_file"]))) as wav:
            seconds = wav.getnframes() / wav.getframerate()
        ratio = seconds / elapsed
        assert 0.8 < ratio < 1.2, (
            f"{key} holds {seconds:.1f}s of audio for {elapsed:.1f}s of recording "
            f"(ratio {ratio:.2f}). Slicing reads event timestamps as offsets into "
            f"the mix, so anything but real time silences the participant tracks."
        )

        peak = _peak(pathlib.Path(output_dir, meta["wav_file"]))
        assert peak > 0.01, (
            f"{key} is {seconds:.1f}s of silence. The loopback plays two tones, so "
            f"the tap is producing zeros - a remote track feeds WebAudio only while "
            f"an element consumes it."
        )


def test_the_tap_survives_the_page_switching_a_receiver_off(tmp_path):
    """Meet keeps a few audio receivers and switches the ones it is not routing
    off, which hands silence to every consumer of that track object. The tap
    reads a clone, whose `enabled` the page cannot reach."""
    output_dir = _record_loopback(tmp_path, switch_off_after=SWITCH_OFF_AFTER)
    sidecar = json.loads(pathlib.Path(output_dir, "session_sidecar.json").read_text())

    for key, meta in sidecar["tracks"].items():
        # A second past the switch, so the check reads only what was written
        # after the page gave up on that track.
        peak = _peak(pathlib.Path(output_dir, meta["wav_file"]), SWITCH_OFF_AFTER + 1.0)
        assert peak > 0.01, (
            f"{key} went silent when the page disabled the track it came from. "
            f"The tap has to read `track.clone()`, which carries its own "
            f"`enabled`, or a call is recorded as zeros with nothing failing."
        )


def _dominant_frequency(path: pathlib.Path) -> float:
    """Rate of zero crossings, which for a single tone is twice its frequency.

    Enough to tell 440Hz from 880Hz, which is the difference between a track
    written at the channel count it was recorded at and one written at half of
    it - the file plays twice as fast and an octave up.
    """
    with wave.open(str(path)) as wav:
        rate = wav.getframerate()
        samples = array.array("h")
        samples.frombytes(wav.readframes(wav.getnframes()))
    # The middle of the file, away from the ramp-up at either end.
    start = len(samples) // 4
    window = samples[start:start + rate] if len(samples) > start + rate else samples[start:]
    if len(window) < 2:
        return 0.0
    crossings = sum(
        1 for i in range(1, len(window))
        if (window[i - 1] < 0) != (window[i] < 0)
    )
    seconds = len(window) / rate
    return crossings / seconds / 2


def test_the_recorded_pitch_is_the_pitch_that_was_sent(tmp_path):
    """The loopback sends 440Hz; anything near 880 means the file is playing at
    double speed, which is what a mono header over interleaved stereo produces."""
    output_dir = _record_loopback(tmp_path)
    sidecar = json.loads(pathlib.Path(output_dir, "session_sidecar.json").read_text())

    for key, meta in sidecar["tracks"].items():
        frequency = _dominant_frequency(pathlib.Path(output_dir, meta["wav_file"]))
        assert 380 < frequency < 520, (
            f"{key} came back at about {frequency:.0f}Hz against the 440Hz sent. "
            f"Twice that is a track written with fewer channels than it carries."
        )
