"""Fast schema checks plus opt-in real browser transport/observer checks."""
import asyncio
import json
import os
from pathlib import Path
import socket

import pytest
from pydantic import ValidationError

from gravai.simulation.server import Scenario, create_app


@pytest.mark.parametrize('interval', [[-1, 2], [2, 1], [0, 31]])
def test_invalid_intervals(interval):
    with pytest.raises(ValidationError):
        Scenario.model_validate({'participants': [{'id': 'a', 'name': 'Alice', 'turns': [interval]}]})


def test_duplicate_participants():
    with pytest.raises(ValidationError):
        Scenario.model_validate({'participants': [{'id': 'a', 'name': 'Alice'}] * 2})


def test_bundled_scenarios():
    for path in Path('scenarios').glob('*/*.json'):
        Scenario.model_validate_json(path.read_text())


@pytest.mark.parametrize('turns', [[[2, 4], [3, 5]], [[4, 5], [1, 2]]])
def test_ambiguous_audio_schedules_rejected(turns):
    with pytest.raises(ValidationError):
        Scenario.model_validate({'participants': [{'id': 'a', 'name': 'Alice', 'turns': turns}]})


def test_expected_audio_excludes_absence_and_outage():
    scenario = Scenario.model_validate({'duration': 10, 'outages': [[4, 6]],
        'participants': [{'id': 'a', 'name': 'Alice', 'turns': [[0, 10]],
                          'presence': [[2, 8]]},
                         {'id': 'b', 'name': 'Bob', 'turns': [[0, 10]], 'presence': []}]})
    assert scenario.expected_turns() == {'a': [(2, 4), (6, 8)], 'b': []}


def test_misspelled_scenario_setting_rejected():
    with pytest.raises(ValidationError):
        Scenario.model_validate({'admisson_delay': 2, 'participants': [{'id': 'a', 'name': 'Alice'}]})


@pytest.mark.skipif(os.getenv('GRAVAI_SIM_BROWSER_TESTS') != '1', reason='Opt-in browser integration')
@pytest.mark.parametrize('provider', ['meet', 'teams'])
def test_real_webrtc_and_production_observer(tmp_path, provider):
    asyncio.run(_browser_check(tmp_path, provider))


async def _browser_check(tmp_path, provider):
    import uvicorn
    from playwright.async_api import async_playwright
    scenario_path = tmp_path / 'scenario.json'
    scenario_path.write_text(json.dumps({'duration': 5, 'admission_delay': 0,
        'participants': [{'id': 'alice', 'name': 'Alice', 'turns': [[1, 3]],
                          'presence': [[0.5, 4]]}]}))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    channel = os.getenv('GRAVAI_SIM_BROWSER_CHANNEL', 'chrome')
    app = create_app(scenario_path, tmp_path / 'artifacts', port, channel, provider)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if task.done():
                await task
                raise AssertionError('Server failed to start')
            if server.started:
                break
            await asyncio.sleep(.1)
        else:
            raise AssertionError('Server startup timeout')
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel=channel, headless=True,
                args=['--autoplay-policy=no-user-gesture-required'])
            try:
                page = await browser.new_page()
                observer = Path(f'src/gravai/recording/providers/{provider}/vad_observer.js').read_text()
                await page.add_init_script(observer)
                await page.goto(f'http://127.0.0.1:{port}/simulated-meeting/{provider}/test')
                if provider == 'teams':
                    await page.locator('prejoin-join-button').click()
                    await page.locator('.btn.primary').click()
                    await page.get_by_role('button', name='Continue without audio or video').click()
                await page.locator('input').fill('Bot test')
                await page.locator('#join').click()
                await page.get_by_text('Did you leave by mistake?' if provider == 'teams'
                    else 'Your host ended the meeting for everyone').wait_for(timeout=30000)
                events = await page.evaluate('window.__vadEvents')
                levels = [e['data']['classCount'] for e in events if e['data']['participantName'] == 'Alice']
                assert len(set(levels)) == 2, events
            finally:
                await browser.close()
        for _ in range(30):
            files = list((tmp_path / 'artifacts').glob('*.json'))
            if files:
                break
            await asyncio.sleep(.1)
        trace = json.loads(files[0].read_text())['trace']
        assert not [e for e in trace if e['kind'] == 'error'], trace
        inbound = [r for e in trace if e['kind'] == 'client' and e['data'].get('type') == 'stats'
                   for r in e['data']['reports'] if r['type'] == 'inbound-rtp']
        assert any(r.get('packetsReceived', 0) > 0 for r in inbound), trace
        assert any(r.get('totalAudioEnergy', 0) > 0 for r in inbound), trace
        assert any(e['kind'] == 'sender_connection' and e['data']['connectionState'] == 'connected'
                   for e in trace), trace
        roster = [e['data']['present'] for e in trace if e['kind'] == 'server'
                  and e['data'].get('type') == 'activity']
        assert roster[0] == [] and roster[-1] == [] and ['alice'] in roster, roster
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=30)
