import type { NextConfig } from "next";

/**
 * There is deliberately no `rewrites()` here.
 *
 * Forwarding /api to the API is what this front end exists to do differently
 * from the nginx image it replaced - see lib/proxy.ts - and a rewrite looks like
 * the way to write it. It is not: `next build` bakes a rewrite's destination
 * into .next/routes-manifest.json, so GRAVAI_API_URL would be fixed at build
 * time while still appearing in docker-compose.yaml as an environment variable
 * somebody could change. The proxy is a route handler for that reason.
 */
const nextConfig: NextConfig = {
  // Traces the dependencies actually reached and emits a server that runs
  // without node_modules, which is what Dockerfile.frontend copies out.
  output: "standalone",
};

export default nextConfig;
