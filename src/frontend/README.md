# GravAI front end

A [Next.js](https://nextjs.org) app over the API: submit and watch jobs, follow
a recording's log while it sits in the meeting, read a meeting back speaker by
speaker with its audio, and edit the settings in `.env` without opening the file.

Three pages:

- **Jobs** (`/jobs`) — submit a recording or a transcription, watch what is
  running, follow a recording's log line by line, and end one either way (see
  below).
- **Recordings** (`/recordings`, `/recordings/[id]`) — every meeting recorded,
  and for one meeting: when it ran, the whole-meeting transcript from the mixed
  track, and per speaker their turns, their text and their audio.
- **Settings** (`/settings`) — the settings in `.env` that a person is expected
  to change: which whisper server to use, which Google account the recorder
  signs in as.

## The API call happens on the server, and that is the point

`app/api/[...path]/route.ts` forwards everything under `/api` to the API, using
the proxy in `lib/proxy.ts`. The browser fetches `/api/jobs` from this origin;
**this server** fetches `http://app:8000/jobs` over the Docker network and passes
the answer back, streaming the body rather than buffering it.

That is worth being explicit about, because the page used to work the other way
— static files behind nginx, calling the API from the browser — and two problems
came with it, both of which this removes:

- **The address had to be one a browser could resolve.** `app` is a Docker
  service name and means nothing outside the network, so the page had to be told
  a published host address like `http://localhost:8000`, which then had to change
  for every deployment. Here it is resolved by a process that *is* on the
  network, and `GRAVAI_API_URL` is read when the request is served, so one image
  serves any deployment.
- **Every call was cross-origin.** The page was on `:3000` and the API on
  `:8000`, so the browser refused whatever the API had not listed in
  `CORS_ALLOW_ORIGINS` — an allowlist that, on an API with no authentication,
  grants whoever is on it the right to start recordings, read every transcript
  and change the settings. Now the browser only ever talks to this origin, so
  that setting is empty and the API answers no cross-origin call at all.

`/docs`, `/redoc` and `/openapi.json` have route handlers of their own. Swagger's
HTML asks for the schema at the root of whichever origin served it, so forwarding
`/openapi.json` at the root — not under `/api` — is what makes the API docs work
from port 3000.

### Why not `rewrites()`

A `rewrites()` rule in `next.config.ts` is the obvious way to write this, and it
is wrong here: `next build` bakes a rewrite's destination into
`.next/routes-manifest.json`. `GRAVAI_API_URL` would be fixed when the image was
built while still sitting in `docker-compose.yaml` looking like something you
could change — setting it would silently do nothing. Read per request in
`lib/proxy.ts`, it is what it appears to be.

`lib/api.ts` holds every call and is the only place a URL to the API is built —
including the audio URLs, which go straight into an `<audio src>`. The proxy
forwards `Range` and returns `206` with the upstream's `Content-Range`
untouched, so seeking in a recording works as it would against the API
directly.

## Running it

```bash
npm install
GRAVAI_API_URL=http://localhost:8000 npm run dev
```

`make frontend-dev` from the repo root does the same, defaulting to a
`make run` API on port 8000. The dev server runs the same proxy, so the page
behaves as it does in the container.

In the container it is `next build` with `output: "standalone"`, which traces the
dependencies actually reached and emits a server that runs without
`node_modules` — see `Dockerfile.frontend`.

## How the pages are put together

- `app/` — one directory per route, plus `layout.tsx` for the topbar, the
  CAPTCHA banner and the toast provider. Routing is by path; there is no client
  router to configure.
- `components/` — the pieces the pages are assembled from.
- `lib/` — `api.ts` (every call), `types.ts` (the API's shapes, mirroring
  `gravai/jobs/models.py` and `gravai/api/schemas.py`), `format.ts`, and
  `usePolling.ts`.

Three things are worth knowing before changing them:

- **Views that watch work in flight poll**, because a recording runs for an hour
  and says nothing until it is over: jobs every 3s, recordings every 10s, one
  recording every 15s, the CAPTCHA banner every 15s. `usePolling` keeps the last
  successful answer, so a failed tick reports itself beside a table that is still
  there rather than replacing the page with an error.
- **A job's log only polls while its drawer is open**, and keeps itself scrolled
  to the bottom only if it was already there. Somebody who has scrolled up is
  reading.
- **Settings does not poll.** It would overwrite what is being typed. Revert
  refetches by hand.

Escaping is not something to remember here: JSX escapes by default, and
participant names, transcripts and error messages are all text somebody else
wrote. Nothing uses `dangerouslySetInnerHTML`.

## Stop is not cancel

The distinction the API draws is the one the page has to keep making, because
only one of them leaves a recording:

- **Stop** asks the recording to leave the meeting. Whatever was captured is
  finalized, sliced and transcribed as if everyone had hung up.
- **Cancel** kills the job. The wav headers are never closed and there is no
  recording at the end of it.

Both ask for confirmation, and the wording of each says which one it is.
