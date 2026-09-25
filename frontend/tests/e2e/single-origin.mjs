/**
 * The portal served by the backend alone - pages AND /api from one origin -
 * the way it runs on Render (backend/app/web.py). Drives real Chrome.
 *
 * NEVER AGAINST THE REAL DATABASE. Against a disposable e2e backend that has
 * `npm run export:backend` built into it:
 *
 *   $env:ENV = "e2e"; $env:DATABASE_URL = "<a throwaway database>"
 *   python -m app.db.migrate upgrade; python -m app.seeds.seed
 *   python -m uvicorn app.main:app --port 8000
 *
 *   E2E_APP_URL=http://127.0.0.1:8000 E2E_SEED_PASSWORD=<same password> \
 *     node tests/e2e/single-origin.mjs
 *
 * For every role: sign in through the page, open every page the sidebar
 * offers, and fail on any browser error or any failed request. Then the
 * customer page by its ?id= address, the old /customers/<id> redirect, a
 * wrong password, and sign-out.
 */
import { existsSync } from "node:fs";

import { chromium } from "playwright-core";

const APP = (process.env.E2E_APP_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const PASSWORD = process.env.E2E_SEED_PASSWORD;
if (!PASSWORD) {
  console.error("E2E_SEED_PASSWORD is not set.");
  process.exit(2);
}

const CHROME = [
  process.env.E2E_CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  `${process.env.LOCALAPPDATA ?? ""}/Google/Chrome/Application/chrome.exe`,
  "/usr/bin/google-chrome",
].find((path) => path && existsSync(path));

const ROLES = [
  { login: "superadmin", pages: ["/dashboard", "/references", "/customers", "/leads", "/feedback", "/team", "/notifications", "/profile", "/admin/users", "/admin/settings", "/admin/logs"] },
  { login: "shail", pages: ["/dashboard", "/references", "/leads", "/feedback", "/team", "/notifications", "/profile", "/admin/users"] },
  { login: "navya", pages: ["/dashboard", "/references", "/leads", "/feedback", "/team", "/notifications", "/profile"] },
  { login: "aastha", pages: ["/dashboard", "/references", "/leads", "/feedback", "/notifications", "/profile"] },
];

let failures = 0;
const check = (ok, label, detail = "") => {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  -> ${detail}`}`);
  if (!ok) failures += 1;
};

async function signIn(page, login, password) {
  await page.goto(`${APP}/login`);
  await page.fill("#identifier", login);
  await page.fill("#password", password);
  await page.click('button[type="submit"]');
}

const browser = await chromium.launch({ executablePath: CHROME, headless: process.env.E2E_HEADED !== "true" });
try {
  for (const role of ROLES) {
    console.log(`\n${role.login}`);
    const context = await browser.newContext();
    const page = await context.newPage();
    const problems = [];
    page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
    });
    page.on("response", (response) => {
      const url = response.url();
      // 401 from /api/auth/me before sign-in is the app asking "who am I?".
      if (response.status() >= 400 && !url.endsWith("/api/auth/me")) {
        problems.push(`HTTP ${response.status()} ${url.replace(APP, "")}`);
      }
    });
    page.on("requestfailed", (request) => {
      // Moving to the next page cancels the last one's prefetches: not a fault.
      if (request.failure()?.errorText.includes("ERR_ABORTED")) return;
      problems.push(`failed: ${request.url().replace(APP, "")} ${request.failure()?.errorText ?? ""}`);
    });

    await signIn(page, role.login, PASSWORD);
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 }).catch(() => {});
    check(page.url().endsWith("/dashboard"), "signs in and lands on the dashboard", page.url());

    for (const path of role.pages) {
      problems.length = 0;
      await page.goto(`${APP}${path}`);
      await page.waitForLoadState("networkidle");
      const onLogin = page.url().includes("/login");
      const visible = await page.locator("main").first().isVisible().catch(() => false);
      check(!onLogin && visible && problems.length === 0, `${path} renders cleanly`, [page.url(), ...problems].join(" | "));
    }

    if (role.login === "superadmin") {
      const list = await page.request.get(`${APP}/api/customers?page=1&page_size=1`);
      const first = list.ok() ? (await list.json()).items?.[0] : undefined;
      if (first) {
        problems.length = 0;
        await page.goto(`${APP}/customers/detail?id=${first.id}`);
        await page.waitForLoadState("networkidle");
        const name = first.customer_name ?? first.name ?? "";
        const shown = name ? await page.getByText(name).first().isVisible().catch(() => false) : true;
        check(shown && problems.length === 0, "customer page opens from /customers/detail?id=", problems.join(" | "));
        await page.goto(`${APP}/customers/${first.id}`);
        await page.waitForLoadState("networkidle");
        check(page.url().includes(`/customers/detail?id=${first.id}`), "old /customers/<id> link redirects", page.url());
      } else {
        console.log("  (no customers in this database - customer page not checked)");
      }
    }

    await context.clearCookies();
    await page.goto(`${APP}/dashboard`);
    await page.waitForURL(/\/login/, { timeout: 15_000 }).catch(() => {});
    check(page.url().includes("/login"), "without a session the portal sends you to sign in", page.url());
    await context.close();
  }

  console.log("\nwrong password");
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, "parth", "definitely-not-it");
  const alert = page.getByText("Incorrect username or password.");
  const shown = await alert.waitFor({ state: "visible", timeout: 15_000 }).then(() => true, () => false);
  check(shown, "says 'Incorrect username or password.'");
  await context.close();
} finally {
  await browser.close();
}

console.log(failures ? `\n${failures} check(s) FAILED` : "\nAll checks passed");
process.exit(failures ? 1 : 0);
