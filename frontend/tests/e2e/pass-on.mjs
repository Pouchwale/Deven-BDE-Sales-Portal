/**
 * Passing a lead on, in real Chrome: a lead held by Navya is passed to
 * Muskan from the lead dialog, leaves Navya's list, and shows in the
 * "Passed on" record with its counts.
 *
 * NEVER AGAINST THE REAL DATABASE - a disposable ENV=e2e backend with
 * `npm run export:backend` built in, set up as in single-origin.mjs:
 *
 *   E2E_APP_URL=http://127.0.0.1:8000 E2E_SEED_PASSWORD=<seed password> \
 *     node tests/e2e/pass-on.mjs
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
const SHOTS = process.env.E2E_SCREENSHOTS;
const LEAD = `Pass On Check ${Date.now()}`;

let failures = 0;
const check = (ok, label, detail = "") => {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  -> ${detail}`}`);
  if (!ok) failures += 1;
};

const health = await (await fetch(`${APP}/health`)).json();
if (health.env !== "e2e") {
  console.error(`Refusing: ${APP} is ENV=${health.env}, not a disposable e2e backend.`);
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
try {
  // Shail hands Navya a lead, through the API.
  const admin = await browser.newContext();
  const login = await admin.request.post(`${APP}/api/auth/login`, {
    data: { identifier: "shail", password: PASSWORD },
  });
  const csrf = (await login.json()).csrf_token;
  const people = await (await admin.request.get(`${APP}/api/users/actionable`)).json();
  const navya = people.find((person) => person.name === "Navya Rupawat");
  const created = await admin.request.post(`${APP}/api/leads`, {
    data: { name: LEAD, company_name: "Browser Check Ltd", assigned_to_user_id: navya.id },
    headers: { "X-CSRF-Token": csrf },
  });
  check(created.status() === 201, "lead assigned to Navya", String(created.status()));
  const leadId = (await created.json()).id;

  // Navya passes it to Muskan, in the page.
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
  page.on("response", (response) => {
    // 401 from /api/auth/me before sign-in is the app asking "who am I?".
    if (response.status() >= 400 && !response.url().endsWith("/api/auth/me")) {
      problems.push(`${response.status()} ${response.url()}`);
    }
  });

  // The counts before, so a re-run against the same database still checks
  // that this pass added exactly one to each.
  const countOf = (log, side, name) =>
    log[side].find((row) => row.name === name)?.count ?? 0;
  const navyaApi = await browser.newContext();
  await navyaApi.request.post(`${APP}/api/auth/login`, {
    data: { identifier: "navya", password: PASSWORD },
  });
  const before = await (await navyaApi.request.get(`${APP}/api/leads/handoffs`)).json();

  await page.goto(`${APP}/login`);
  await page.fill("#identifier", "navya");
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 30000 });

  await page.goto(`${APP}/leads?tab=my-work`);
  await page.getByRole("button", { name: new RegExp(LEAD) }).first().click();
  await page.getByRole("button", { name: "Pass on to my team" }).click();
  await page.locator("#pass-on-to").selectOption({ label: "Muskan Makhija" });
  await page.fill("#pass-on-reason", "Muskan already knows this buyer.");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/pass-on-dialog.png` });
  await page.getByRole("button", { name: "Pass on", exact: true }).click();
  await page.getByText("Lead passed on").first().waitFor({ timeout: 15000 });
  check(true, "toast: Lead passed on");
  await page.getByText(/assigned to Muskan Makhija/).first().waitFor({ timeout: 10000 });
  check(true, "dialog now says assigned to Muskan Makhija");
  check(
    (await page.getByText(/Passed on to Muskan Makhija by Navya Rupawat/).count()) > 0,
    "timeline shows the pass with its reason",
  );
  await page.keyboard.press("Escape");

  const lead = await (await context.request.get(`${APP}/api/leads/${leadId}`)).json();
  check(lead.assigned_to_name === "Muskan Makhija", "API: lead now held by Muskan", lead.assigned_to_name);

  await page.goto(`${APP}/leads?tab=my-work`);
  await page.getByText("Assigned leads").first().waitFor();
  check((await page.getByRole("button", { name: new RegExp(LEAD) }).count()) === 0, "gone from Navya's My work");

  await page.goto(`${APP}/leads?tab=passed-on`);
  await page.getByText("Passed-on leads").first().waitFor();
  await page.getByRole("button", { name: LEAD }).first().waitFor({ timeout: 15000 });
  check(true, "listed under Passed on");
  const body = await page.locator("main").innerText();
  const by = countOf(before, "passed_by", "Navya Rupawat") + 1;
  const to = countOf(before, "passed_to", "Muskan Makhija") + 1;
  check(
    new RegExp(`Passed on by[\\s\\S]*Navya Rupawat\\s*${by}\\b`).test(body),
    `count on the page: passed on by Navya ${by} (one more than before)`,
  );
  check(
    new RegExp(`Passed on to[\\s\\S]*Muskan Makhija\\s*${to}\\b`).test(body),
    `count on the page: passed on to Muskan ${to} (one more than before)`,
  );
  check(body.includes("Muskan already knows this buyer."), "reason shown in the record");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/passed-on-tab.png`, fullPage: true });

  // Muskan sees it too, and was told.
  const muskan = await browser.newContext();
  await muskan.request.post(`${APP}/api/auth/login`, {
    data: { identifier: "muskan", password: PASSWORD },
  });
  const log = await (await muskan.request.get(`${APP}/api/leads/handoffs`)).json();
  check(log.items.some((row) => row.lead_name === LEAD), "Muskan sees the pass in her record");
  const notes = await (await muskan.request.get(`${APP}/api/notifications`)).json();
  check(notes.items.some((n) => n.title === `Lead passed on to you: ${LEAD}`), "Muskan was notified");

  check(problems.length === 0, "no browser errors or failed requests", problems.join("; "));
} finally {
  await browser.close();
}

console.log(failures ? `\n${failures} check(s) FAILED` : "\nAll checks passed.");
process.exit(failures ? 1 : 0);
