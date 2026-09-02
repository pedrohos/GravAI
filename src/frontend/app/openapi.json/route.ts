import { proxy } from "@/lib/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** The schema, at this origin's root, because that is where the /docs page
 *  looks for it. Not under /api for the same reason. */
export const GET = (request: Request) => proxy(request, "/openapi.json");
