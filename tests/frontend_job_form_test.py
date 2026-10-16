"""Browser regressions. Run with FRONTEND_TEST_URL=http://localhost:3001 pytest tests/frontend_job_form_test.py."""

import asyncio
import os

import pytest
from playwright.async_api import async_playwright

BASE_URL = os.environ.get("FRONTEND_TEST_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="Requires a running frontend")


@pytest.mark.parametrize("failure_at", ["validation", "submission", None])
def test_job_submission_waits_for_validation_and_reports_errors(failure_at):
    asyncio.run(_exercise_submission(failure_at))


async def _exercise_submission(failure_at):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        submissions = []
        validations = []
        reason = "Recording directory already exists: /tmp/existing_tracks"

        async def api_route(route):
            request = route.request
            path = request.url.split("/api", 1)[1]
            if path == "/jobs/recording-directory":
                await route.fulfill(json={"path": "/tmp/new_tracks"})
            elif path == "/jobs/recording-directory/validate":
                validations.append(request.post_data_json)
                # Keep blur validation pending when the submit event fires.
                await asyncio.sleep(0.3)
                if failure_at == "validation":
                    await route.fulfill(status=400, json={"detail": reason})
                else:
                    await route.fulfill(json={"path": "/tmp/new_tracks"})
            elif path == "/jobs" and request.method == "POST":
                submissions.append(request.post_data_json)
                await asyncio.sleep(0.3)
                if failure_at == "submission":
                    await route.fulfill(status=400, json={"detail": reason})
                else:
                    await route.fulfill(status=202, json={"id": "test-job"})
            else:
                await route.fulfill(json=[])

        await page.route("**/api/**", api_route)
        await page.goto(f"{BASE_URL}/jobs")
        await page.wait_for_function("document.querySelector('#job-recording-dir')?.value === '/tmp/new_tracks'")
        await page.locator("#job-target").fill("https://meet.google.com/abc-defg-hij")
        await page.locator("#job-recording-dir").focus()
        # Blur followed immediately by submission reproduces the click sequence.
        await page.evaluate("""() => {
            document.querySelector('#job-recording-dir').blur();
            document.querySelector('.job-form').requestSubmit();
            document.querySelector('.job-form').requestSubmit();
        }""")
        if failure_at:
            await page.locator("#toast.bad").wait_for()
            assert await page.locator("#toast").inner_text() == reason
            assert await page.locator("#toast").is_visible()
            if failure_at == "validation":
                assert await page.locator("#job-recording-dir-error").inner_text() == reason
                assert await page.locator("#job-recording-dir").get_attribute("aria-invalid") == "true"
                assert submissions == []
        else:
            await page.locator("#toast.ok").wait_for()
        await page.wait_for_function("!document.querySelector('button[type=submit]').disabled")
        assert len(validations) == 1
        assert len(submissions) == (0 if failure_at == "validation" else 1)
        if submissions:
            assert submissions[0]["tracks_output_dir"] == "/tmp/new_tracks"
        await browser.close()
