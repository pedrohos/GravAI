/** What the container healthcheck asks. Deliberately does not touch the API:
 *  it answers whether this server is up, not whether the API behind it is. */
export function GET() {
  return new Response("ok\n", { headers: { "Content-Type": "text/plain" } });
}
