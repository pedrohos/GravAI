"""Drives a page the way a person drives one, because Meet checks.

The Google Meet's `CreateMeetingDevice` answers 403 to an automated click, but
answers 200 if there are a few `mousemove` events crossed the page first.

Playwright's `click()` locator's button click (which follows the button directly
then clicks) triggers 403, while a 200 can be achieved by creating a flow of
move the pointer events + about 0.2-0.7s of wait between the events (mouse movement,
keystrokes and click). Thus human input needs to be simulated. These thresholds
might change over time so it may be necessary to adjust the parameters in the future.
"""

from __future__ import annotations

import random
import re


def pause(page, low: float, high: float) -> None:
    """A wait of no particular length, which is what a person's waits are."""
    page.wait_for_timeout(random.uniform(low, high) * 1000)


def type_like_a_person(page, text: str) -> None:
    """Types into whatever has focus, one key at a time, at a rate that wanders.

    `fill()` sets the value in one assignment and fires a single input event: a
    name that appears whole, with no keydown behind it. This produces the key
    events a keyboard produces, spaced around ~110ms with the occasional longer
    gap, rather than the fixed delay `press_sequentially` would give.
    """
    for char in text:
        page.keyboard.type(char)
        delay = max(0.045, random.gauss(0.11, 0.035))
        if char == " ":
            delay += random.uniform(0.05, 0.2)
        if random.random() < 0.08:
            # Thinking, or looking at the keyboard.
            delay += random.uniform(0.3, 0.9)
        page.wait_for_timeout(delay * 1000)


def click_if_present(page, name: str, timeout: float = 3000, roles=("button", "link")) -> bool:
    """Best-effort click on a control that may not be on this screen at all.

    Through the pointer like everything else here: a click that arrives with no
    path behind it is refused (see the note at the top), and since it is not
    known how early that judgement is formed, no click in these flows is made
    the cheap way - not even the ones dismissing a banner.

    `name` is matched case-insensitively against the accessible name, across
    each of `roles` in turn - Google renders the declining half of a dialog as
    plain blue text, which is a link about as often as it is a button.
    """
    pattern = re.compile(name, re.I)
    for role in roles:
        try:
            control = page.get_by_role(role, name=pattern).first
            move_and_click(page, control, timeout=timeout)
            return True
        except Exception:
            continue
    return False


def move_and_click(page, locator, timeout: float | None = None) -> None:
    """Sends the pointer to the control along a path, then clicks it.

    The press is `page.mouse.click` at the point the pointer already reached, not
    `locator.click()`. The former passes, the latter does not. Ending the path with
    Playwright's own click - which re-aims at the element's centre and runs its
    actionability checks on the way - is refused at `CreateMeetingDevice` exactly
    like a click with no path at all.

    `timeout` is passed to the measurement, so a caller probing for a button that
    may not be there fails as fast as it asked to rather than blocking on the
    page default.
    """
    limits = {} if timeout is None else {"timeout": timeout}
    box = locator.bounding_box(**limits)
    if not box:
        # Nothing to aim at - not visible, or gone between the two calls.
        locator.click(**limits)
        return

    # Somewhere inside the control rather than dead centre: a person aims at a
    # button, not at its centroid.
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    for _ in range(random.randint(1, 2)):
        page.mouse.move(
            x + random.uniform(-120, 120), y + random.uniform(-90, 90),
            steps=random.randint(6, 14),
        )
        pause(page, 0.05, 0.2)
    page.mouse.move(x, y, steps=random.randint(8, 20))
    # Hovering before pressing, which is also what gives Meet's own hover
    # handlers a chance to run.
    pause(page, 0.12, 0.45)
    page.mouse.click(x, y)
