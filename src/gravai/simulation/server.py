"""Run with python -m gravai.simulation.server --scenario scenarios/basic.json."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from playwright.async_api import async_playwright
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    frequency: float = Field(default=440, gt=0, lt=20000)
    # Optional speech fixture; paths are relative to the scenario file.
    audio: str | None = None
    turns: list[tuple[float, float]] = Field(default_factory=list)
    # None means present for the entire meeting; [] means never present.
    presence: list[tuple[float, float]] | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration: float = Field(default=30, gt=0, le=7200)
    admission_delay: float = Field(default=1, ge=0, le=290)
    deny: bool = False
    end_reason: Literal["host", "removed", "left"] = "host"
    teams_shadow_dom: bool = True
    participants: list[Participant] = Field(min_length=1)
    # Disable all media during these intervals while signaling stays connected.
    outages: list[tuple[float, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        ids = [p.id for p in self.participants]
        if len(set(ids)) != len(ids) or any(not i for i in ids):
            raise ValueError("Participant ids must be unique and nonempty")
        schedules = [self.outages]
        for p in self.participants:
            schedules.extend([p.turns, p.presence or []])
        for intervals in schedules:
            previous_end = 0
            for start, end in intervals:
                if not 0 <= start < end <= self.duration:
                    raise ValueError("Intervals must satisfy 0 <= start < end <= duration")
                if start < previous_end:
                    raise ValueError("Intervals must be sorted and nonoverlapping")
                previous_end = end
        return self

    def expected_turns(self) -> dict[str, list[tuple[float, float]]]:
        """Audible intervals before codec buffering and observer/slicer padding."""
        result = {}
        for participant in self.participants:
            intervals = []
            presence = participant.presence if participant.presence is not None else [(0, self.duration)]
            for start, end in participant.turns:
                for joined, left in presence:
                    pieces = [(max(start, joined), min(end, left))]
                    for outage_start, outage_end in self.outages:
                        pieces = [(a, b) for low, high in pieces
                                  for a, b in [(low, min(high, outage_start)),
                                               (max(low, outage_end), high)] if a < b]
                    intervals.extend((a, b) for a, b in pieces if a < b)
            result[participant.id] = intervals
        return result


def create_app(scenario_path: Path, artifacts: Path, port: int, channel: str,
               simulated_provider: str = "meet") -> FastAPI:
    scenario_path = scenario_path.resolve()
    scenario = Scenario.model_validate_json(scenario_path.read_text())
    if simulated_provider not in ("meet", "teams"):
        raise ValueError("Provider must be meet or teams")
    scenario_hash = hashlib.sha256(scenario.model_dump_json().encode()).hexdigest()
    fixtures = {p.id: (scenario_path.parent / p.audio).resolve()
                for p in scenario.participants if p.audio}
    for fixture_path in fixtures.values():
        if not fixture_path.is_file():
            raise ValueError(f"Missing audio fixture: {fixture_path}")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        artifacts.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel=channel, headless=True,
                args=["--autoplay-policy=no-user-gesture-required"])
            app.state.browser = browser
            try:
                yield
            finally:
                await browser.close()

    app = FastAPI(title="GravAI meeting simulator", lifespan=lifespan)

    @app.get("/healthz")
    def health() -> dict[str, Any]:
            return {"status": "ok", "provider": simulated_provider, "scenario": scenario.model_dump()}

    @app.get("/client.js")
    def client() -> FileResponse:
        return FileResponse(Path(__file__).with_name("client.js"), media_type="text/javascript")

    @app.get("/fixture/{participant_id}")
    def fixture(participant_id: str) -> FileResponse:
        from fastapi import HTTPException
        if participant_id not in fixtures:
            raise HTTPException(404)
        return FileResponse(fixtures[participant_id])

    @app.get("/sender")
    def sender() -> HTMLResponse:
        return HTMLResponse('<script src="/client.js"></script>')

    @app.get("/simulated-meeting/{provider}/{meeting_id}")
    def meeting(provider: Literal["meet", "teams"], meeting_id: str) -> HTMLResponse:
        if provider != simulated_provider:
            from fastapi import HTTPException
            raise HTTPException(404, "This environment serves " + simulated_provider)
        if provider == "teams":
            return HTMLResponse('''<!doctype html><title>Simulated Teams</title>
            <body data-provider="teams"><h1>Teams local meeting</h1><main id="room">
            <prejoin-join-button role="button" tabindex="0"
              onclick="this.hidden=true;document.getElementById('web').hidden=false">Join on the web</prejoin-join-button>
            <button id="web" class="btn primary" hidden
              onclick="this.hidden=true;document.getElementById('continue').hidden=false">Continue on this browser</button>
            <button id="continue" hidden onclick="this.hidden=true;document.getElementById('form').hidden=false">Continue without audio or video</button>
            <div id="form" hidden><label>Your name <input data-tid="prejoin-display-name-input"></label>
            <button id="join">Join now</button></div></main><script src="/client.js"></script>''')
        return HTMLResponse('''<!doctype html><title>Simulated Meet</title>
        <body data-provider="meet">
        <h1>GravAI local meeting</h1><main id="room">
        <label>Your name <input aria-label="Your name"></label>
        <button onclick="this.textContent='Turn on microphone'">Turn off microphone</button>
        <button onclick="this.textContent='Turn on camera'">Turn off camera</button>
        <button id="join">Ask to join</button></main><script src="/client.js"></script>''')

    @app.websocket("/signal")
    async def signal(ws: WebSocket) -> None:
        await ws.accept()
        run_id = str(uuid4())
        provider = ws.query_params.get("provider", "meet")
        if provider != simulated_provider:
            await ws.close(code=1008)
            return
        trace = []
        started = time.monotonic()
        page = None
        reader = None

        def log(kind: str, data: Any) -> None:
            trace.append({"elapsed": time.monotonic() - started, "kind": kind, "data": data})

        async def send(data: Any) -> None:
            log("server", data)
            await ws.send_json(data)

        async def receive_stats() -> None:
            while True:
                log("client", await ws.receive_json())

        try:
            log("join", await asyncio.wait_for(ws.receive_json(), timeout=30))
            log("provider", provider)
            await asyncio.sleep(scenario.admission_delay)
            if scenario.deny:
                await send({"type": "denied"})
                return
            page = await app.state.browser.new_page()
            await page.expose_function("traceConnection", lambda data: log("sender_connection", data))
            await page.goto(f"http://127.0.0.1:{port}/sender")
            await page.wait_for_function("() => !!window.makeOffer")
            offer = await page.evaluate("s => window.makeOffer(s)", scenario.model_dump())
            await send({"type": "offer", "sdp": offer, "scenario": scenario.model_dump(), "run_id": run_id})
            async def receive_answer() -> dict[str, Any]:
                while True:
                    message = await ws.receive_json()
                    log("client", message)
                    if message.get("type") == "answer":
                        return message
                    if message.get("type") != "connection":
                        raise ValueError("Expected SDP answer or connection event")
            answer = await asyncio.wait_for(receive_answer(), timeout=30)
            await page.evaluate("s => window.acceptAnswer(s)", answer["sdp"])
            reader = asyncio.create_task(receive_stats())
            await send({"type": "admitted"})
            lead_in = await page.evaluate("() => window.startMedia()")
            await asyncio.sleep(lead_in)
            epoch = time.monotonic()
            last = None
            while time.monotonic() - epoch < scenario.duration:
                if reader.done():
                    await reader
                elapsed = time.monotonic() - epoch
                outage = any(a <= elapsed < b for a, b in scenario.outages)
                present = [p.id for p in scenario.participants if p.presence is None
                           or any(a <= elapsed < b for a, b in p.presence)]
                active = [p.id for p in scenario.participants if p.id in present
                          and not outage and any(a <= elapsed < b for a, b in p.turns)]
                if (active, present) != last:
                    await send({"type": "activity", "active": active, "present": present, "elapsed": elapsed})
                    last = (active, present)
                await asyncio.sleep(.1)
            log("sender_stats", await page.evaluate("() => window.collectStats()"))
            await send({"type": "ended", "reason": scenario.end_reason})
            # Allow final receiver stats to arrive before tearing down transport.
            await asyncio.sleep(.3)
        except (WebSocketDisconnect, asyncio.TimeoutError) as exc:
            log("disconnected", str(exc))
        except Exception as exc:
            log("error", str(exc))
            try:
                await send({"type": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            if reader:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if page:
                await page.close()
            (artifacts / f"{run_id}.json").write_text(json.dumps({
                "schema_version": 1, "run_id": run_id, "provider": provider,
                "scenario_sha256": scenario_hash, "expected_turns": scenario.expected_turns(),
                "browser_version": app.state.browser.version,
                "scenario": scenario.model_dump(), "trace": trace,
            }, indent=2))
            try:
                await ws.close()
            except RuntimeError:
                pass

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--artifacts", type=Path, default=Path("tmp/simulation"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--provider", choices=["meet", "teams"], default="meet")
    args = parser.parse_args()
    if args.scenario is None:
        args.scenario = Path("scenarios") / args.provider / "basic.json"
    uvicorn.run(create_app(args.scenario, args.artifacts, args.port, args.channel, args.provider),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
