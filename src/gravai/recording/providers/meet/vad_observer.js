// Reports who is speaking in a Google Meet call, into window.__vadEvents.
//
// Meet has no attribute that says "this person is talking". What it has is an
// animated microphone level indicator per participant tile, and the animation is
// driven entirely through the class attribute, which is rewritten about five
// times a second while they talk and left alone while they do not:
//
//     'IisKdb GF8M7d gjg47c …'  ->  'IisKdb GF8M7d wEsLMd …'  ->  'IisKdb … Oaajhc …'
//
// What is read here is the *rate of change*, not the values. Measured against a
// live call, the indicator mutated 5.5 times a second while someone spoke and
// 0.04 times a second while nobody did - and the tokens themselves are
// obfuscated, rotate between releases, and the element keeps base classes when
// idle, so "which class means speaking" has no stable answer while "it is
// animating right now" does.
//
// The structural part is `div[jsname="QgSmzd"]` inside a tile carrying
// `data-participant-id`. If Meet renames jsname, that selector is the one line
// to re-derive, and scripts/inspect_meet_dom.py re-derives it by correlating
// class mutations against inbound-rtp audio levels in a live call.
//
// Two things the consumer relies on:
//
//   classCount is 0 or 1, never a real count. src/gravai/slicing/slice.py asserts
//   that a participant only ever reports two distinct counts - start and end -
//   and Meet's animation would otherwise report four or five.
//
//   window.__vadEvents is read fresh on every push, because the recorder drains
//   it by reassigning it to a new array.
(() => {
  const INDICATOR_SELECTOR = 'div[jsname="QgSmzd"]';
  const TILE_SELECTOR = '[data-participant-id], [data-requested-participant-id]';

  // The animation pauses between words. Without a hold-off, one sentence becomes
  // a dozen start/stop pairs and the slicer cuts the audio into fragments.
  const SILENCE_HOLD_MS = 600;

  // A level animation runs at roughly 5 mutations a second, so a lone class
  // rewrite is the tile re-rendering rather than someone speaking. Two within
  // this window is the cheapest thing that tells them apart.
  const MIN_MUTATIONS = 2;
  const BURST_WINDOW_MS = 500;

  const install = () => {
    if (window.top !== window) return;          // runs in every frame; only the top one has tiles
    if (window.__meetVadInstalled) return;
    window.__meetVadInstalled = true;

    // participantId -> { speaking, timer, name, indicators: Map<Element, boolean> }
    const participants = new Map();

    const ownText = (element) =>
      Array.from(element.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent.trim())
        .filter(Boolean)
        .join(' ');

    // Meet renders the display name twice inside the tile, once in a span and
    // once in a div. That repetition is what separates it from the labels,
    // tooltips and material-icon ligatures that also live there.
    const participantName = (tile) => {
      const spanTexts = new Set();
      for (const span of tile.querySelectorAll('span')) {
        const text = ownText(span);
        if (text) spanTexts.add(text);
      }
      for (const div of tile.querySelectorAll('div')) {
        const text = ownText(div);
        if (text && spanTexts.has(text)) return text;
      }
      for (const text of spanTexts) return text;
      return null;
    };

    const emit = (participantId, name, speaking) => {
      if (!Array.isArray(window.__vadEvents)) window.__vadEvents = [];
      const timestamp = Date.now();
      window.__vadEvents.push({
        type: 'voice-level',
        data: {
          id: participantId,
          participantName: name || participantId,
          // Binary on purpose - see the note at the top.
          classCount: speaking ? 1 : 0,
          className: speaking ? 'speaking' : '',
          timestamp: timestamp,
          tagName: 'DIV',
        },
        timestamp: timestamp,
      });
    };

    // Each mutation restarts the silence countdown, so speech ends when the
    // animation stops rather than when any particular class appears.
    const update = (participantId, entry) => {
      const now = Date.now();
      entry.recent = entry.recent.filter((t) => now - t < BURST_WINDOW_MS);
      entry.recent.push(now);

      if (entry.timer) clearTimeout(entry.timer);
      if (!entry.speaking && entry.recent.length >= MIN_MUTATIONS) {
        entry.speaking = true;
        emit(participantId, entry.name, true);
      }
      if (entry.speaking) {
        entry.timer = setTimeout(() => {
          entry.timer = null;
          entry.speaking = false;
          entry.recent = [];
          emit(participantId, entry.name, false);
        }, SILENCE_HOLD_MS);
      }
    };

    const onIndicatorMutated = (indicator) => {
      // A participant has more than one of these - the tile and the thumbnail -
      // and they animate together, so they all feed one participant's timeline.
      const tile = indicator.closest(TILE_SELECTOR);
      if (!tile) return;
      const participantId =
        tile.getAttribute('data-participant-id') ||
        tile.getAttribute('data-requested-participant-id');
      if (!participantId) return;

      let entry = participants.get(participantId);
      if (!entry) {
        entry = { speaking: false, timer: null, name: null, recent: [] };
        participants.set(participantId, entry);
      }
      // The name renders after the tile does, so it is re-read until found.
      if (!entry.name) entry.name = participantName(tile);
      update(participantId, entry);
    };

    new MutationObserver((records) => {
      for (const record of records) {
        if (record.attributeName !== 'class') continue;
        const target = record.target;
        if (!target.matches || !target.matches(INDICATOR_SELECTOR)) continue;
        onIndicatorMutated(target);
      }
    }).observe(document.body, {
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    });

    // Whoever is already on screen when the observer starts, so a participant
    // who never speaks is still known to have been present.
    window.__vadSnapshotRoster = function () {
      if (!Array.isArray(window.__vadEvents)) window.__vadEvents = [];
      const roster = [];
      for (const tile of document.querySelectorAll(TILE_SELECTOR)) {
        const participantId =
          tile.getAttribute('data-participant-id') ||
          tile.getAttribute('data-requested-participant-id');
        if (!participantId) continue;
        roster.push({ id: participantId, participantName: participantName(tile) });
      }
      return roster;
    };
  };

  if (document.body) install();
  else document.addEventListener('DOMContentLoaded', install, { once: true });
})();
