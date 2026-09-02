import { proxy } from "@/lib/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** The API's own documentation, served from this origin. It needs /openapi.json
 *  at this root too - the page asks for the schema relative to whichever origin
 *  served it - which is why that has a route of its own. */
export const GET = (request: Request) => proxy(request, "/docs");
