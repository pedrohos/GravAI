/**
 * The API call, made from this server rather than from the browser.
 *
 * This is the piece that replaced nginx. The page fetches `/api/...` on its own
 * origin; this forwards it to the API over the Docker network and hands the
 * answer back. The browser never learns where the API is, never resolves a
 * Docker service name it cannot see, and never makes a cross-origin request.
 *
 * It is a route handler rather than a `rewrites()` rule in next.config.ts,
 * which is the more obvious way to write it, because `next build` bakes a
 * rewrite's destination into .next/routes-manifest.json. GRAVAI_API_URL would
 * then be a build argument wearing an environment variable's clothes: setting it
 * in docker-compose.yaml would silently do nothing. Read here, it is what it
 * looks like - one image, pointed at whatever API it is given, decided when the
 * request is served.
 */

/** Read per request, so it is genuinely a runtime setting. */
const apiBase = () => (process.env.GRAVAI_API_URL ?? "http://app:8000").replace(/\/+$/, "");

/**
 * Headers that belong to this hop and not the next one.
 *
 * `accept-encoding` is dropped so the API answers uncompressed: fetch would
 * decode a compressed body transparently and leave content-length describing
 * the compressed one, which matters because the audio routes answer Range
 * requests and an <audio> element believes those headers.
 */
const STRIP_REQUEST = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "accept-encoding",
  "content-length",
]);

const STRIP_RESPONSE = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
]);

export async function proxy(request: Request, path: string): Promise<Response> {
  const incoming = new URL(request.url);
  const target = `${apiBase()}${path}${incoming.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIP_REQUEST.has(key.toLowerCase())) headers.set(key, value);
  });

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Streams the request body through rather than buffering it first.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
    } as RequestInit);
  } catch (error) {
    // The API being unreachable is not this server's failure to serve a page,
    // and the shape matches what the API itself returns so the page's own error
    // handling reads it without a special case.
    return Response.json(
      { detail: `The API at ${apiBase()} could not be reached: ${String(error)}` },
      { status: 502 }
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE.has(key.toLowerCase())) responseHeaders.set(key, value);
  });

  // The body is passed through as a stream, so a long recording is never held
  // in this server's memory and Range requests answer with 206 as they came.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
