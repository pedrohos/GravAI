"""Exercise a running backend against either simulator and check recorded audio."""
from __future__ import annotations

import argparse
import subprocess
import time
from array import array

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://localhost:8000')
    parser.add_argument('--meeting-url', required=True, help='URL reachable by the backend browser')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--transcribe', action='store_true', help='Requires a configured Whisper service and speech fixtures')
    args = parser.parse_args()
    with httpx.Client(base_url=args.api, timeout=30) as client:
        endpoint = '/record_meeting_and_transcribe' if args.transcribe else '/record_meeting'
        response = client.post(endpoint, params={'meeting_url': args.meeting_url})
        response.raise_for_status()
        job = response.json()
        print(f"Submitted {job['id']}", flush=True)
        deadline = time.monotonic() + args.timeout
        while job['status'] not in ('succeeded', 'failed', 'cancelled'):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Job {job['id']} still {job['status']}; inspect /jobs/{job['id']}/log")
            time.sleep(1)
            response = client.get(f"/jobs/{job['id']}"); response.raise_for_status()
            job = response.json()
        if job['status'] != 'succeeded':
            raise RuntimeError(job.get('error') or job['status'])
        recording_id = job['recording_id']
        response = client.get(f'/recordings/{recording_id}/audio'); response.raise_for_status()
        decoded = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"],
            input=response.content, capture_output=True, check=True,
        )
        samples = array("h")
        samples.frombytes(decoded.stdout)
        if not samples or not any(samples):
            raise AssertionError('Recording has no audible samples')
        print(f"Recorded {len(samples)/48000:.2f}s of audio")
        response = client.get(f'/recordings/{recording_id}'); response.raise_for_status()
        recording = response.json()
        if not any(p['segments'] for p in recording['participants']):
            raise AssertionError('No participant speech segments were produced')
        print(f"Recording {recording_id}: {len(recording['participants'])} participant tracks")
        if args.transcribe:
            response = client.get(f'/recordings/{recording_id}/jobs'); response.raise_for_status()
            transcriptions = [j for j in response.json() if j['type'] == 'transcribe']
            if not transcriptions:
                raise AssertionError('No transcription job was created')
            for transcription in transcriptions:
                while transcription['status'] not in ('succeeded', 'failed', 'cancelled'):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Transcription did not finish')
                    time.sleep(1)
                    response = client.get(f"/jobs/{transcription['id']}"); response.raise_for_status()
                    transcription = response.json()
                if transcription['status'] != 'succeeded':
                    raise RuntimeError(transcription.get('error') or transcription['status'])
            print('Transcription completed')


if __name__ == '__main__':
    main()
