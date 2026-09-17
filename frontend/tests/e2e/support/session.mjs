/**
 * Shared configuration and session handling for the e2e scripts.
 *
 * Sign-in returns no token in its JSON body: the session secret arrives only
 * in the HttpOnly `bde_session` cookie, with a readable `bde_csrf` cookie
 * beside it. Scripts that call the API directly send both cookies back plus
 * the `X-CSRF-Token` header (the double-submit the browser does), which is
 * exactly what a signed-in page sends.
 *
 * Configuration comes from the environment. There is deliberately NO default
 * password: a password written into a script is a password every copy of the
 * repository knows.
 *
 *   E2E_APP_URL        the portal (Next)            default http://localhost:3000
 *   E2E_API_URL        the backend (FastAPI)         default http://localhost:8000
 *   E2E_SEED_PASSWORD  the seeded accounts' password (required)
 *   E2E_BROWSER_CHANNEL  playwright channel          default msedge
 */

export const APP = (process.env.E2E_APP_URL ?? "http://localhost:3000").replace(/\/$/, "");
export const API = (process.env.E2E_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const BROWSER_CHANNEL = process.env.E2E_BROWSER_CHANNEL ?? "msedge";

export const SESSION_COOKIE = "bde_session";
export const CSRF_COOKIE = "bde_csrf";

/** The seeded password, or a clear exit. Never a fallback value. */
export function requireSeedPassword() {
  const value = process.env.E2E_SEED_PASSWORD;
  if (!value) {
    console.error(
      "E2E_SEED_PASSWORD is not set.\n" +
        "Set it to the SEED_PASSWORD the target backend was seeded with. There is no default.",
    );
    process.exit(2);
  }
  return value;
}

/** Cookie values from a fetch Response's Set-Cookie headers. */
function cookiesFrom(response) {
  const jar = {};
  const lines =
    typeof response.headers.getSetCookie === "function"
      ? response.headers.getSetCookie()
      : [response.headers.get("set-cookie") ?? ""];
  for (const line of lines) {
    const [pair] = line.split(";");
    const index = pair.indexOf("=");
    if (index > 0) jar[pair.slice(0, index).trim()] = pair.slice(index + 1).trim();
  }
  return jar;
}

/**
 * POST /api/auth/login. Returns { status, body, auth } where `auth` is null
 * on failure, or { session, csrf } taken from the Set-Cookie headers.
 */
export async function apiLogin(email, password, base = API) {
  const response = await fetch(`${base}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await response.json().catch(() => ({}));
  const jar = cookiesFrom(response);
  const auth =
    response.ok && jar[SESSION_COOKIE]
      ? { session: jar[SESSION_COOKIE], csrf: jar[CSRF_COOKIE] ?? body.csrf_token ?? "" }
      : null;
  return { status: response.status, body, auth };
}

/** Headers that authenticate a request the way a signed-in browser does. */
export function authHeaders(auth) {
  if (!auth) return {};
  return {
    Cookie: `${SESSION_COOKIE}=${auth.session}; ${CSRF_COOKIE}=${auth.csrf}`,
    "X-CSRF-Token": decodeURIComponent(auth.csrf),
  };
}

/** fetch against the API with a session. Returns { status, body, text }. */
export async function apiCall(auth, path, init = {}, base = API) {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(auth),
      ...(init.headers ?? {}),
    },
  });
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  return { status: response.status, body, text };
}

/**
 * fetch from INSIDE a signed-in page: same-origin, cookies attached by the
 * browser, CSRF header read from the readable cookie. Returns { status, body }.
 */
export async function pageApi(page, path, init = {}) {
  return page.evaluate(
    async ({ path, init, csrfCookie }) => {
      const match = document.cookie.match(new RegExp(`(?:^|; )${csrfCookie}=([^;]*)`));
      const csrf = match ? decodeURIComponent(match[1]) : "";
      const response = await fetch(path, {
        ...init,
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": csrf } : {}),
          ...(init.headers ?? {}),
        },
      });
      const text = await response.text();
      let body = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        body = null;
      }
      return { status: response.status, body };
    },
    { path, init, csrfCookie: CSRF_COOKIE },
  );
}
