import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

/**
 * Where the Next server forwards `/api/*` when the browser calls it on the
 * portal's own origin. Server-side only - it never reaches the browser.
 *
 * NOTE: rewrites are resolved when `next build` runs and are written into the
 * build output, so set this BEFORE building (the deploy scripts do). In a
 * production deployment behind Caddy, `/api/*` is proxied straight to uvicorn
 * and never reaches Next at all; this rewrite is what makes `next dev` and a
 * bare `next start` work same-origin.
 */
const BACKEND_INTERNAL_URL = (process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

/**
 * On a hosting platform (Render, Vercel, any CI) the localhost default can
 * never be right: the backend is a different service. Without this check the
 * build succeeds and every sign-in fails at runtime with ECONNREFUSED
 * 127.0.0.1:8000 - so fail the build instead, saying what to set.
 */
const onHostingPlatform = Boolean(process.env.RENDER || process.env.VERCEL || process.env.CI);
if (onHostingPlatform && !process.env.BACKEND_INTERNAL_URL?.trim()) {
  throw new Error(
    "BACKEND_INTERNAL_URL is not set. Set it on this service to the backend's URL " +
      "(e.g. https://your-backend.onrender.com) and redeploy - it is baked in at build time.",
  );
}

/**
 * An explicit cross-origin API (NEXT_PUBLIC_API_URL) has to be allowed to
 * receive fetches. When the portal is served same-origin - the recommended
 * setup - this is empty and `connect-src 'self'` covers everything.
 */
function apiOrigin(): string | null {
  const configured = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (!configured) return null;
  try {
    return new URL(configured).origin;
  } catch {
    return null;
  }
}

/**
 * Content-Security-Policy.
 *
 * Without nonces on purpose: every page is a client component and the theme
 * bootstrap in `src/lib/theme.tsx` is an inline <script>, so a nonce would
 * force dynamic rendering of every route for no gain in an app that renders
 * no user-supplied HTML. What it does:
 *   - scripts, styles, fonts and images only from this origin (plus inline
 *     scripts/styles, which Next's bootstrap and recharts need, and data:/blob:
 *     images);
 *   - fetches (including the assistant's SSE stream) only to this origin;
 *   - no framing, no plugins, no <base> hijack, forms post only here.
 * Dev adds 'unsafe-eval' (React Refresh / source maps), the HMR websocket and
 * the LAN backend on :8000 used before same-origin API calls.
 */
function contentSecurityPolicy(): string {
  const connect = ["'self'"];
  const origin = apiOrigin();
  if (origin) connect.push(origin);
  if (isDev) connect.push("ws:", "wss:", "http://localhost:8000", "http://*:8000");

  const directives = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connect.join(" ")}`,
    "media-src 'self'",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy() },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  // Only a production build: HSTS pinned on a developer's localhost is
  // painful to undo. Browsers ignore it over plain http anyway.
  ...(isDev
    ? []
    : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Optional separate build directory, so a production build (e.g. the e2e
  // stack) can run beside `next dev` without clobbering its `.next`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  poweredByHeader: false,
  // No floating Next.js "N" badge on the portal, in development either.
  devIndicators: false,
  // Next's gzip holds back small writes until its buffer fills, which turned
  // the assistant's SSE stream (proxied through the /api rewrite below) into
  // one lump at the end - measured: 5 events 1 s apart arrived together after
  // 5 s. Caddy compresses in production (and sends /api straight to uvicorn),
  // so nothing is lost by switching it off here.
  compress: false,
  experimental: {
    // How long the /api rewrite waits for the backend. Next's default is 30 s,
    // but a sleeping free-tier backend takes up to a minute to wake, so the
    // first sign-in after a quiet spell failed with "Failed to proxy".
    proxyTimeout: 120_000,
  },
  // Type errors and lint errors fail the build by default in Next 16, which
  // is what we want; there is nothing to override here.

  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },

  // Same-origin API. The browser calls /api/... on the portal's own origin and
  // Next forwards it to FastAPI, so the session cookie is first-party and no
  // CORS is involved. `/health` is the backend's liveness probe.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_INTERNAL_URL}/api/:path*` },
      { source: "/health", destination: `${BACKEND_INTERNAL_URL}/health` },
    ];
  },

  // Dev only. Next refuses to serve its /_next/* dev assets to a request whose
  // Host is not one it was told to expect: the page returns 200 and then
  // renders nothing, because every chunk 404s. Listing the private LAN ranges
  // is what lets a phone on the same Wi-Fi actually load the portal.
  // Production builds ignore this setting entirely.
  allowedDevOrigins: ["10.*.*.*", "172.16.*.*", "192.168.*.*", "*.local"],
};

export default nextConfig;
