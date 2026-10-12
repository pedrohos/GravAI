import { proxy } from "@/lib/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Everything the page asks the API for arrives here. The API's routes sit at
 *  its root - /jobs, /recordings, /config, /captcha_challenges - so the prefix
 *  is stripped and the rest passed on unchanged. */
async function handler(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxy(request, `/${path.map(encodeURIComponent).join("/")}`);
}

export {
  handler as GET,
  handler as POST,
  handler as PUT,
  handler as PATCH,
  handler as DELETE,
  handler as HEAD,
};
