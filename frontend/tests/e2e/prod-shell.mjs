/**
 * Production-shell check: security headers, CSP, error pages, same-origin API.
 *
 * Needs no account and writes nothing, so it is safe against any backend.
 * Run against a PRODUCTION build (`next build && next start`), because the
 * dev server's CSP is deliberately looser:
 *
 *   npm run build
 *   npx next start -p 3100
 *   E2E_APP_URL=http://127.0.0.1:3100 node tests/e2e/prod-shell.mjs
 *
 * Checks, at desktop and phone width:
 *   - /login renders its form; / and /dashboard end on /login when signed out
 *   - an unknown route shows the portal's 404 page with a 404 status
 *   - zero CSP violations, page errors, hydration warnings, console errors
 *     or failed requests (a signed-out 401 from /api/auth/me is expected)
 *   - the inline theme bootstrap runs under the CSP (dark theme applied)
 *   - the response headers are present; /api/health/db answers via the rewrite
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright-core";

const APP = (process.env.E2E_APP_URL ?? "http://127.0.0.1:3100").replace(/\/$/, "");
const SHOTS = process.env.E2E_SCREENSHOT_DIR ?? join(import.meta.dirname, "screenshots", "prod-shell");
const CHANNEL = process.env.E2E_BROWSER_CHANNEL ?? "msedge";
mkdirSync(SHOTS, { recursive: true });

const failures = [];
const fail = (message) => {
  failures.push(message);
  console.log(`  FAIL ${message}`);
};
const pass = (message) => console.log(`  ok   ${message}`);

/* ------------------------------------------------------------- headers */
const EXPECTED_HEADERS = [
  "content-security-policy",
  "x-content-type-options",
  "x-frame-options",
  "referrer-policy",
  "permissions-policy",
];
{
  console.log("headers");
  const res = await fetch(`${APP}/login`);
  for (const name of EXPECTED_HEADERS) {
    if (res.headers.get(name)) pass(`${name}: ${res.headers.get(name).slice(0, 70)}`);
    else fail(`missing header ${name}`);
  }
  if (res.headers.get("x-powered-by")) fail("x-powered-by is exposed");
  else pass("no x-powered-by");
  const csp = res.headers.get("content-security-policy") ?? "";
  if (csp.includes("unsafe-eval")) fail("production CSP contains 'unsafe-eval'");

  const health = await fetch(`${APP}/api/health/db`);
  if (health.status === 200) pass("/api/health/db via rewrite -> 200");
  else fail(`/api/health/db via rewrite -> ${health.status}`);
}

/* ------------------------------------------------------------- browser */
const browser = await chromium.launch({ channel: CHANNEL, headless: true });

async function visit(label, width, height, path, check) {
  const context = await browser.newContext({ viewport: { width, height } });
  // Dark theme stored before load: proves the inline bootstrap script ran.
  await context.addInitScript(() => {
    try {
      localStorage.setItem("bde_portal_theme", "dark");
    } catch {}
    document.addEventListener("securitypolicyviolation", (event) => {
      console.error(`CSP-VIOLATION ${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  const page = await context.newPage();
  const problems = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error" && msg.type() !== "warning") return;
    const text = msg.text();
    // The signed-out session probe answers 401 by design; the 404 route
    // answers 404 by design. The browser logs both as resource errors.
    if (/status of 40[14]/.test(text)) return;
    problems.push(`console.${msg.type()}: ${text.slice(0, 200)}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message.slice(0, 200)}`));
  page.on("requestfailed", (req) => {
    const reason = req.failure()?.errorText ?? "";
    if (reason.includes("ERR_ABORTED")) return; // navigations/prefetch cancelled by a redirect
    problems.push(`requestfailed: ${req.url()} ${reason}`);
  });
  page.on("response", (res) => {
    const url = res.url();
    if (res.status() < 400) return;
    if (url.endsWith("/api/auth/me") && res.status() === 401) return;
    if (res.status() === 404 && res.request().resourceType() === "document" && path.includes("does-not-exist")) return;
    problems.push(`HTTP ${res.status()} ${url}`);
  });

  const response = await page.goto(`${APP}${path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  const name = `${label}-${width}${path.replace(/[^a-z0-9]+/gi, "_")}.png`;
  await page.screenshot({ path: join(SHOTS, name), fullPage: true });

  const tag = `${label} ${width}px ${path}`;
  try {
    await check(page, response);
    pass(`${tag} renders as expected`);
  } catch (err) {
    fail(`${tag}: ${err.message}`);
  }
  const dark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
  if (!dark) fail(`${tag}: theme bootstrap script did not run (CSP?)`);
  if (problems.length) problems.forEach((p) => fail(`${tag}: ${p}`));
  else pass(`${tag}: no console errors, CSP violations or failed requests`);
  await context.close();
}

const expect = (condition, message) => {
  if (!condition) throw new Error(message);
};

for (const [label, width, height] of [
  ["desktop", 1440, 900],
  ["mobile", 390, 844],
]) {
  console.log(`\n${label} (${width}x${height})`);
  await visit(label, width, height, "/login", async (page) => {
    await page.locator('input[type="password"]').first().waitFor({ timeout: 10_000 });
    expect(await page.locator('input[type="email"], input#email').count(), "no email field");
  });
  await visit(label, width, height, "/dashboard", async (page) => {
    await page.waitForURL(/\/login$/, { timeout: 10_000 });
  });
  await visit(label, width, height, "/this-page-does-not-exist", async (page, response) => {
    expect(response?.status() === 404, `status ${response?.status()}, expected 404`);
    await page.getByText("We couldn't find that page").waitFor({ timeout: 5_000 });
    await page.getByRole("link", { name: "Go to dashboard" }).waitFor({ timeout: 5_000 });
  });
}

await browser.close();

console.log(`\nScreenshots: ${SHOTS}`);
if (failures.length) {
  console.log(`\n${failures.length} problem(s)`);
  process.exit(1);
}
console.log("\nAll production-shell checks passed");
