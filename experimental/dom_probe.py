import argparse
import json
import time
from playwright.sync_api import sync_playwright


def _meeting_origin(meeting_url: str) -> str:
    parts = meeting_url.split("//", 1)
    if len(parts) != 2:
        return meeting_url
    scheme, rest = parts[0], parts[1]
    host = rest.split("/", 1)[0]
    return f"{scheme}//{host}"


def _collect_dom_snapshot(page) -> dict:
    script = """
    () => {
            const selectors = [
                "[data-is-speaking]",
                "[aria-label*='Speaking']",
                "[aria-label*='speaking']",
                "[aria-label*='Speaking with']",
                "[aria-live]",
                "[data-tid*='speaking']",
                "[data-tid*='speaking-indicator']",
                "[data-tid*='active-speaker']",
                "[data-tid*='participant']",
                "[data-tid*='participant-name']",
                "[data-tid*='display-name']",
                "[data-tid*='displayName']",
                "[data-tid*='roster']",
                "[data-tid*='people']",
                "[data-tid*='participant-avatar']",
                "[role='listitem']",
                "[role='list']",
            ];
      const seen = new Set();
      const items = [];
      for (const sel of selectors) {
        const nodes = Array.from(document.querySelectorAll(sel));
        for (const node of nodes) {
          if (seen.has(node)) continue;
          seen.add(node);
                    const attrs = {};
                    for (const attr of node.attributes) {
                        if (attr.name.startsWith("data-") || attr.name.startsWith("aria-") || attr.name === "role") {
                            attrs[attr.name] = attr.value;
                        }
                    }
          const text = (node.textContent || "").trim().slice(0, 120);
          const outer = node.outerHTML ? node.outerHTML.slice(0, 200) : "";
          items.push({
            tag: node.tagName,
            id: node.id || "",
            className: node.className || "",
            attrs,
            text,
            outer,
          });
        }
      }
      return items;
    }
    """
    items = []
    for frame in page.frames:
        try:
            frame_items = frame.evaluate(script)
        except Exception:
            continue
        for item in frame_items:
            item["frameUrl"] = frame.url
            items.append(item)
    return {"timestamp": time.time(), "items": items}


def _open_roster(page) -> None:
    # Best-effort: open the People/Roster panel to surface speaking indicators.
    try:
        page.get_by_role("button", name="People").click(timeout=2000)
        return
    except Exception:
        pass
    try:
        page.locator("#roster-button").click(timeout=2000)
        return
    except Exception:
        pass
    try:
        page.locator("[data-tid='roster-button']").click(timeout=2000)
    except Exception:
        pass


def run(meeting_url: str, output_path: str, duration: int, interval_ms: int, debug: bool) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            ignore_default_args=["--mute-audio"],
            args=[
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = browser.new_context(bypass_csp=True)
        context.grant_permissions([], origin=_meeting_origin(meeting_url))
        page = context.new_page()
        page.goto(meeting_url, wait_until="domcontentloaded")

        try:
            page.locator("prejoin-join-button").first.click(timeout=3000)
        except Exception:
            pass
        try:
            page.locator(".btn.primary").first.click(timeout=3000)
        except Exception:
            pass
        try:
            page.get_by_role("button", name="Continue without audio or video").click(timeout=3000)
        except Exception:
            pass

        try:
            input_box = page.locator('input[data-tid="prejoin-display-name-input"]')
            input_box.wait_for(timeout=30000)
        except Exception:
            input_box = page.locator("input").first

        input_box.click(timeout=10_000)
        input_box.fill("Bot de Grava\u00e7\u00e3o de Pedro Silva")

        try:
            page.get_by_role("button", name="Join now").click(timeout=5000)
        except Exception:
            for _ in range(4):
                page.keyboard.press("Tab")
            page.keyboard.press("Enter")

        if debug:
            page.screenshot(path="probe_joined.png")

        _open_roster(page)
        start = time.time()
        with open(output_path, "w", encoding="utf-8") as f:
            while time.time() - start < duration:
                snapshot = _collect_dom_snapshot(page)
                f.write(json.dumps(snapshot) + "\n")
                f.flush()
                time.sleep(interval_ms / 1000.0)

        context.close()
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DOM probe for Teams active speaker signals")
    parser.add_argument("--meeting_url", required=True)
    parser.add_argument("--output", default="dom_probe.log")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--interval_ms", type=int, default=500)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    run(
        meeting_url=args.meeting_url,
        output_path=args.output,
        duration=args.duration,
        interval_ms=args.interval_ms,
        debug=args.debug,
    )
