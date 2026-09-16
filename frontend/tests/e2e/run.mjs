/**
 * End-to-end walkthrough of the portal, driving real Chrome.
 *
 * NEVER AGAINST THE REAL DATABASE. This run creates leads ("Kiran Shah",
 * "Undo Me ...") and imports eight feedback responses, and the API has no way
 * to delete either. Pointed at the real database it left 58 test leads and 16
 * test responses behind, and the dashboard reported them as the company's
 * work. The preflight now refuses any backend that does not report ENV=e2e.
 *
 * Start a disposable backend on its own database file (PowerShell):
 *
 *   cd backend
 *   $env:ENV = "e2e"; $env:DATABASE_URL = "sqlite:///./e2e_portal.db"
 *   python -m app.db.migrate upgrade
 *   python -m app.seeds.seed
 *   python -m uvicorn app.main:app --port 8000
 *
 * and the frontend as usual:  npm run build && npm start   (port 3000)
 *
 *   node tests/e2e/run.mjs
 *
 * This run resets one account's password (Parth's) in order to exercise the
 * admin-reset -> forced-change loop. Afterwards, put everything back with:
 *
 *   cd backend && python -m app.seeds.seed --reset-passwords
 *
 * The run puts Parth's password back itself when it finishes - see
 * restoreSeedPassword at the bottom. The command above is the fallback for
 * when that cleanup could not run, and the preflight tells you if so.
 */
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const SHOTS = join(HERE, "screenshots");

const APP = process.env.E2E_APP_URL ?? "http://localhost:3000";
const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const SEED_PASSWORD = process.env.E2E_SEED_PASSWORD ?? "ChangeMe@123";

// Used for the forced-change journey only.
const TEMP_PASSWORD = "TempReset@2026";
const NEW_PASSWORD = "Portal@2026e2e";

// Set during the walkthrough so a feedback request has a link to carry, then
// cleared again at the end. Not a real form - nothing is ever submitted to it.
const FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSc-e2e/viewform";

const ADMIN = "shail.patel@pouchwale.com";
const SUPER_ADMIN = "owner@pouchwale.com";
const BDE = "parth.fulvani@pouchwale.com";

const CHROME_CANDIDATES = [
  process.env.E2E_CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  `${process.env.LOCALAPPDATA ?? ""}/Google/Chrome/Application/chrome.exe`,
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);

/* ------------------------------------------------------------ harness */
let passed = 0;
const failures = [];

function check(label, condition, detail = "") {
  if (condition) {
    passed += 1;
    console.log(`  PASS  ${label}`);
  } else {
    failures.push(`${label}${detail ? ` — ${detail}` : ""}`);
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

function section(name) {
  console.log(`\n${name}`);
}

async function shoot(page, name) {
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
}

async function signIn(page, email, password) {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.click('button[type="submit"]');
}

function isoDaysFromNow(days) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

/**
 * A file in the shape Google Forms produces.
 *
 * The real export has not been supplied (plan Q5), so this exercises the
 * importer against the structure it is built for: full-sentence headers, a
 * worded rating scale, and a per-department column pair. Production is
 * deliberately rated low enough to trip the alert.
 */
function feedbackCsv() {
  const headers = [
    "Timestamp",
    "Customer Name",
    "Company Name",
    "Mobile Number",
    "Email Address",
    "BDE / Salesperson who handled you",
    "Overall, how satisfied are you with our service?",
    "How would you rate our Sales team?",
    "Any comments about Sales?",
    "How would you rate our Production team?",
    "Any comments about Production?",
    "How would you rate our Quality team?",
    "Would you recommend us",
    "Any other comments or suggestions?",
  ];

  const stamp = `${new Date().toLocaleDateString("en-GB").replace(/\//g, "/")} 10:15:00`;
  const rows = Array.from({ length: 8 }, (_, index) => [
    stamp,
    `E2E Customer ${index + 1}`,
    `Company ${index + 1}`,
    "9800000000",
    `e2e${index + 1}@example.com`,
    "Parth Fulvani",
    "4 - Satisfied",
    "5 - Very Satisfied",
    "",
    "1 - Very Dissatisfied",
    "Dispatch was late again.",
    "4 - Satisfied",
    "Yes",
    "Overall fine.",
  ]);

  const escape = (cell) => (/[",\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell);
  return [headers, ...rows].map((row) => row.map(escape).join(",")).join("\n");
}

async function apiLogin(email, password) {
  const response = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return response.ok ? await response.json() : null;
}

/* ------------------------------------------------------------- checks */
async function preflight() {
  section("Preflight");
  for (const [name, url] of [
    ["backend", `${API}/health`],
    ["frontend", APP],
  ]) {
    try {
      const response = await fetch(url);
      check(`${name} reachable at ${url}`, response.ok, `status ${response.status}`);
    } catch (error) {
      check(`${name} reachable at ${url}`, false, error.message);
    }
  }
  if (failures.length) {
    console.error("\nServers are not up. Start them and re-run.");
    process.exit(1);
  }

  // Refuse the real database. Everything below writes, and nothing it writes
  // can be removed through the API - so the only safe place to run it is a
  // server that says, in so many words, that it is disposable.
  const health = await fetch(`${API}/health`).then((response) => response.json());
  if (health.env !== "e2e") {
    console.error(
      `\nRefusing to run: the backend at ${API} reports ENV=${health.env ?? "(none)"}.` +
        "\nThis walkthrough creates leads and feedback it cannot delete. Start a" +
        "\ndisposable backend with ENV=e2e on its own database - see the top of" +
        "\nthis file for the commands.\n",
    );
    process.exit(1);
  }
  check("the backend is a disposable e2e server", true);

  // This run changes a password on purpose, so a previous run can leave an
  // account on a password this one does not know. Say so plainly instead of
  // failing later with a mystery "Incorrect email or password".
  const stale = [];
  for (const email of [ADMIN, BDE]) {
    if (!(await apiLogin(email, SEED_PASSWORD))) stale.push(email);
  }
  if (stale.length) {
    console.error(
      `\nThe seeded password no longer works for: ${stale.join(", ")}` +
        "\nA previous end-to-end run changed it. Restore it with:" +
        "\n\n    cd backend && python -m app.seeds.seed --reset-passwords\n",
    );
    process.exit(1);
  }
  check("seeded credentials are intact", true);
}

async function main() {
  await preflight();

  const executablePath = CHROME_CANDIDATES.find((path) => existsSync(path));
  if (!executablePath) {
    console.error("No Chrome/Edge binary found. Set E2E_CHROME to one.");
    process.exit(1);
  }
  console.log(`\nUsing browser: ${executablePath}`);

  const browser = await chromium.launch({
    executablePath,
    headless: process.env.E2E_HEADED !== "true",
  });

  try {
    /* --------------------------------------------- 1. one-click sign-in */
    section("1. Sign in");
    let context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    let page = await context.newPage();

    await page.goto(APP, { waitUntil: "domcontentloaded" });
    await page.waitForURL("**/login", { timeout: 15000 });
    check("root redirects an anonymous visitor to /login", page.url().includes("/login"));
    await shoot(page, "01-login");

    // The seeded shortcuts sign in directly — no typing, no second step.
    await page.click(`[data-testid="quick-signin"][data-email="${ADMIN}"]`);
    await page.waitForURL("**/dashboard", { timeout: 15000 });
    check("one-click sign-in lands straight on the dashboard", true);
    check(
      "no forced password change while it is switched off",
      !page.url().includes("/set-password"),
    );

    /* ------------------------------------------------ 2. admin journey */
    section("2. Admin dashboard shows the real SAP numbers");
    await page.waitForSelector("text=Converted customers", { timeout: 15000 });

    const statValue = (label) =>
      page.getAttribute(`[data-testid="stat"][data-label="${label}"]`, "data-value");

    const customers = await statValue("Converted customers");
    const openLeads = await statValue("Open leads");
    const feedbackPending = await statValue("Feedback pending");
    check("17 converted customers on the dashboard", customers === "17", `saw ${customers}`);
    check("the leads module has its own KPI row", openLeads !== null, `saw ${openLeads}`);
    check(
      "the feedback module's pending queue has a KPI",
      feedbackPending !== null,
      `saw ${feedbackPending}`,
    );

    const body = await page.textContent("body");
    check("the reference module is on the dashboard", (body ?? "").includes("Reference Tracking"));
    check("the leads module is on the dashboard", (body ?? "").includes("Assigned Leads"));
    check("the feedback module is on the dashboard", (body ?? "").includes("Customer feedback"));
    // Import health and Latest invoices were removed from the dashboard on
    // request - the space went to the greeting, focus lines and coverage.
    check(
      "the dashboard leads with the greeting, not an import log",
      (body ?? "").includes("Good morning") ||
        (body ?? "").includes("Good afternoon") ||
        (body ?? "").includes("Good evening"),
    );
    check(
      "the reference-status chart is rendered",
      (body ?? "").includes("Reference status of converted customers"),
    );
    check("the lead pipeline chart is rendered", (body ?? "").includes("Lead pipeline"));
    await shoot(page, "03-dashboard-admin");

    await page.click('button[aria-label*="dark theme"]');
    await page.waitForTimeout(400);
    const isDark = await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    );
    check("theme toggle switches to dark", isDark);
    await shoot(page, "04-dashboard-dark");
    await page.click('button[aria-label*="light theme"]');
    await page.waitForTimeout(300);

    /* ----------------------------------------------- 3. customers page */
    section("3. Customers — the real SAP import");

    // Browsing the SAP book is Super Admin's alone now, so this section runs
    // in its own context. An Admin has no Customers link and the API refuses
    // them - both of which are asserted first, because that IS the rule.
    check(
      "an admin has no Customers link",
      (await page.locator('#portal-nav a[href="/customers"]').count()) === 0,
    );
    const adminBlocked = await page.evaluate(async (apiUrl) => {
      const token = window.localStorage.getItem("bde_portal_token");
      const response = await fetch(`${apiUrl}/api/customers`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return response.status;
    }, API);
    check("and the API refuses them directly", adminBlocked === 403, `got ${adminBlocked}`);

    const bookContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const book = await bookContext.newPage();
    await signIn(book, SUPER_ADMIN, SEED_PASSWORD);
    await book.waitForURL(/\/dashboard/, { timeout: 20000 });
    await book.click('a[href="/customers"]');
    await book.waitForURL("**/customers", { timeout: 15000 });
    await book.waitForSelector("table tbody tr", { timeout: 15000 });

    const rowCount = await book.locator("table tbody tr").count();
    check("17 customers listed for an admin", rowCount === 17, `saw ${rowCount}`);

    const customersText = await book.textContent("body");
    check(
      "SAP values are shown verbatim (Jagdamba dryfruits keeps its casing)",
      (customersText ?? "").includes("Jagdamba dryfruits"),
    );
    await shoot(page, "05-customers");

    await book.fill('input[aria-label="Search customers"]', "GULABS");
    await book.waitForTimeout(900);
    const searchCount = await book.locator("table tbody tr").count();
    check("search narrows to one customer", searchCount === 1, `saw ${searchCount}`);

    await book.click("table tbody tr a");
    await book.waitForURL(/\/customers\/[0-9a-f-]{36}/, { timeout: 15000 });
    await book.waitForSelector("text=Invoice lines", { timeout: 15000 });
    const detailText = await book.textContent("body");
    check("customer detail shows the SAP code", (detailText ?? "").includes("C2080"));
    check(
      "customer detail shows an invoice line",
      (detailText ?? "").includes("Nutty Berry Mania"),
    );
    await shoot(page, "06-customer-detail");

    await bookContext.close();

    /* ---------------------------------------------------- 4. team page */
    section("4. Team and org chart");
    await page.click('a[href="/team"]');
    await page.waitForURL("**/team", { timeout: 15000 });
    await page.waitForSelector("text=Shail Patel", { timeout: 15000 });
    await page.waitForSelector("text=people in your scope", { timeout: 15000 });
    const teamText = await page.textContent("body");
    check("org chart renders the admin", (teamText ?? "").includes("Shail Patel"));
    check("org chart reaches three levels deep", (teamText ?? "").includes("Parag Sharma"));
    // Active people only. The count moves whenever somebody is deactivated,
    // so this asserts the rule rather than today's number.
    const scopeCount = Number((teamText ?? "").match(/(\d+) people in your scope/)?.[1]);
    check(
      "the Team page counts people, excluding deactivated",
      scopeCount >= 1 && (teamText ?? "").includes("excluding deactivated"),
      `saw ${scopeCount}`,
    );
    await shoot(page, "07-team");

    /* -------------------------------------------------- 5. user admin */
    section("5. User administration");
    await page.click('a[href="/admin/users"]');
    await page.waitForURL("**/admin/users", { timeout: 15000 });
    await page.waitForSelector("table tbody tr", { timeout: 15000 });
    const adminRows = await page.locator("table tbody tr").count();
    // Defaults to active accounts; "Show deactivated" is the switch that
    // brings the rest back. An exact number here breaks on every leaver.
    check("the active roster is listed", adminRows >= 1, `saw ${adminRows}`);

    const adminText = await page.textContent("body");
    check(
      "the Super Admin row offers no actions to an Admin",
      (adminText ?? "").includes("Portal Owner"),
    );
    await shoot(page, "08-admin-users");

    // Rule 2 on the response: the role dropdown must not offer ADMIN.
    await page.click("text=Add person");
    await page.waitForSelector("#role", { timeout: 15000 });
    await page.waitForFunction(
      () => document.querySelectorAll("#role option").length > 1,
      null,
      { timeout: 15000 },
    );
    const roleOptions = await page.locator("#role option").allTextContents();
    check(
      "an Admin cannot grant the Admin role",
      !roleOptions.some((option) => option.trim() === "Admin"),
      roleOptions.join(", "),
    );
    check(
      "the dropdown offers Manager, BDE and Sales",
      ["Manager", "BDE", "Sales"].every((role) =>
        roleOptions.some((option) => option.trim() === role),
      ),
      roleOptions.join(", "),
    );
    await shoot(page, "09-add-person");
    await page.keyboard.press("Escape");

    /* ------------------------------- 6. admin reset -> forced change */
    // The forced password change is switched off for seeded accounts, but the
    // enforcement itself still matters: an admin reset must always demand a
    // new password. Trigger it the way it happens in real life.
    section("6. An admin reset forces the next sign-in to set a password");
    const resetStatus = await page.evaluate(
      async ({ apiUrl, email, password }) => {
        const token = window.localStorage.getItem("bde_portal_token");
        const list = await fetch(`${apiUrl}/api/users?search=${encodeURIComponent(email)}`, {
          headers: { Authorization: `Bearer ${token}` },
        }).then((response) => response.json());
        const target = list.items[0];
        const response = await fetch(`${apiUrl}/api/users/${target.id}/reset-password`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ new_password: password }),
        });
        return response.status;
      },
      { apiUrl: API, email: BDE, password: TEMP_PASSWORD },
    );
    check("admin resets a BDE's password", resetStatus === 200, `status ${resetStatus}`);

    /* --------------------------------------- 6b. reference tracking */
    section("6b. Reference Tracking — record an ask");
    await page.goto(`${APP}/references?tab=customers`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("table tbody tr", { timeout: 15000 });

    const referenceRows = await page.locator("table tbody tr").count();
    const referenceText = (await page.textContent("table")) ?? "";
    // Not an exact count any more. Converted leads join this book by design,
    // and the first page fills to the page size, so pinning a number here
    // just made the walkthrough fail every time somebody converted a lead.
    check(
      "the converted-customer book is here",
      referenceRows >= 17 && referenceText.includes("GULABS"),
      `saw ${referenceRows} rows`,
    );

    // The first row that is still ASKABLE - an account whose reference
    // conversation is finished shows "Completed" and has no Record ask, which
    // is the rule, not a missing button.
    const askable = page.getByRole("button", { name: "Record ask" }).first();
    check(
      "an askable account offers Record ask",
      (await page.getByRole("button", { name: "Record ask" }).count()) > 0,
    );
    await askable.click();
    await page.waitForSelector("#reference-form", { timeout: 15000 });

    // Three outcomes now. Two of them finish the conversation.
    const outcomes = await page.textContent("#reference-form");
    check(
      "all three outcomes are offered",
      ["Gave a reference", "Not shared", "Not right now"].every((o) =>
        (outcomes ?? "").includes(o),
      ),
    );

    // "Not right now" needs a date to come back on — that is the whole point.
    await page.getByText("Not right now").click();
    await page.fill("#ask-again", isoDaysFromNow(-3));
    await page.click('button[form="reference-form"]');
    await page.waitForSelector('[data-testid="toast"]', { timeout: 15000 });
    check("an ask is recorded against a customer", true);
    await shoot(page, "12-references");

    await page.goto(`${APP}/references?tab=follow-ups`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("table tbody tr", { timeout: 15000 });
    const followUpText = await page.textContent("body");
    check(
      "a back-dated ask lands in the follow-up queue as overdue",
      /\d+ days? overdue/.test(followUpText ?? ""),
    );

    /* ------------------------------------------- 6c. assigned leads */
    section("6c. Assigned Leads — assign one");
    await page.goto(`${APP}/leads?tab=all`, { waitUntil: "domcontentloaded" });
    await page.click("text=Assign a lead");
    await page.waitForSelector("#lead-form", { timeout: 15000 });
    await page.waitForFunction(
      () => document.querySelectorAll("#lead-assignee option").length > 1,
      null,
      { timeout: 15000 },
    );
    await page.fill("#lead-name", "Kiran Shah");
    await page.fill("#lead-company", "Shah Foods");
    await page.selectOption("#lead-assignee", { label: "Parth Fulvani" });
    await page.click('button[form="lead-form"]');
    await page.waitForSelector('[data-testid="toast"]', { timeout: 15000 });

    await page.goto(`${APP}/leads?tab=all`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("table tbody tr", { timeout: 15000 });
    const leadsText = await page.textContent("body");
    check("the lead appears in the pipeline", (leadsText ?? "").includes("Kiran Shah"));
    await shoot(page, "13-leads");

    /* ---------------------------------------------- 6d. feedback */
    section("6d. Feedback — dry run, then import");
    await page.goto(`${APP}/feedback?tab=import`, { waitUntil: "domcontentloaded" });
    await page.setInputFiles('input[type="file"]', {
      name: "google_form_responses.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(feedbackCsv(), "utf-8"),
    });

    await page.waitForSelector("text=Dry run", { timeout: 20000 });
    const dryRunText = await page.textContent("body");
    check("the dry run resolves the department columns", /Production/.test(dryRunText ?? ""));
    check(
      "the dry run shows the mapping before anything is written",
      (dryRunText ?? "").includes("submitted_at_source"),
    );
    await shoot(page, "14-feedback-dryrun");

    await page.click("text=Confirm and import");
    // A regex selector, not `text=Imported`: the plain form matches
    // case-insensitively and would be satisfied by the lowercase "imported"
    // in the KPI hint above, long before the result panel renders.
    await page.waitForSelector("text=/Imported \\d+ response/", { timeout: 30000 });
    const importedText = await page.textContent("body");
    check("the import reports what it created", /Imported \d+ response/.test(importedText ?? ""));
    // The END state, not the transition. An alert that is already open is not
    // raised a second time — correct behaviour — so asserting on the import
    // banner made this pass or fail depending on whether the walkthrough had
    // been run before.
    const openAlerts = await page.evaluate(async (apiUrl) => {
      const token = window.localStorage.getItem("bde_portal_token");
      return fetch(`${apiUrl}/api/feedback/alerts`, {
        headers: { Authorization: `Bearer ${token}` },
      }).then((response) => response.json());
    }, API);
    // Production is what THIS import drives below the threshold. Deliberately
    // not "and nothing else": sample feedback, if it is loaded, puts Dispatch
    // under too, and that is real data doing exactly what it should.
    check(
      "the department this import sank is on alert",
      openAlerts.some((alert) => alert.department_name === "Production"),
      JSON.stringify(openAlerts.map((alert) => alert.department_name)),
    );

    await page.goto(`${APP}/feedback?tab=analysis`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=Department average rating", { timeout: 15000 });
    await page.waitForSelector("table tbody tr", { timeout: 15000 });
    const analysisText = await page.textContent("body");
    check("department averages are shown", (analysisText ?? "").includes("Production"));
    check(
      "a department below the threshold is flagged",
      (analysisText ?? "").includes("Below threshold"),
    );
    await shoot(page, "15-feedback-analysis");

    /* ------------------- 6c2. the completed-customer timeline */
    section("6c2. A completed customer has a feedback timeline, not a pipeline");

    // The customer page is Super Admin's, so this whole section runs there.
    // What it is testing - the timeline, the message composer, the templates -
    // is reached from that page.
    const bookCtx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const book2 = await bookCtx2.newPage();
    await signIn(book2, SUPER_ADMIN, SEED_PASSWORD);
    await book2.waitForURL(/\/dashboard/, { timeout: 20000 });

    await book2.goto(`${APP}/customers`, { waitUntil: "domcontentloaded" });
    await book2.waitForSelector("table tbody tr", { timeout: 15000 });
    await book2.locator("table tbody tr a").first().click();
    await book2.waitForURL(/\/customers\/[0-9a-f-]{36}/, { timeout: 15000 });
    await book2.waitForSelector("text=Feedback timeline", { timeout: 15000 });

    const customerPageText = await book2.textContent("body");
    check(
      "a SAP account is labelled a completed customer",
      (customerPageText ?? "").includes("Completed customer"),
    );
    // Log something, then take it back.
    await book2.selectOption('select[aria-label="What happened"]', "CALL");
    await book2.fill('textarea[aria-label="Remark"]', "Logged by mistake.");
    await book2.click("text=Add to timeline");
    await book2.waitForSelector("text=Logged by mistake.", { timeout: 15000 });
    check("a remark can be logged on the customer timeline", true);
    await shoot(page, "17-customer-timeline");

    await book2.click('button[title="Take this entry back"]');
    await book2.waitForSelector("text=Undone", { timeout: 15000 });
    const undoneText = await book2.textContent("body");
    check(
      "undo marks the entry rather than deleting it",
      (undoneText ?? "").includes("Undone") &&
        (undoneText ?? "").includes("Logged by mistake."),
    );

    /* --------------- 6c2b. the ready-to-send request message */
    section("6c2b. A feedback request is composed, not typed");

    // wa.me is a real site. Nothing is ever SENT from here - opening the link
    // only pre-fills WhatsApp - but the walkthrough must not reach out to it
    // either, so the request is aborted and only the URL is inspected.
    let openedWhatsAppUrl = null;
    await bookCtx2.route("**://wa.me/**", (route) => {
      openedWhatsAppUrl = route.request().url();
      return route.abort();
    });

    // Start from a known state. A previous run that stopped early may have
    // left the form link set, and then "the portal explains the link is
    // missing" would fail for a reason that has nothing to do with the code.
    await book2.evaluate(async (apiUrl) => {
      const token = window.localStorage.getItem("bde_portal_token");
      await fetch(`${apiUrl}/api/admin/settings`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ values: { "company.feedback_form_url": "" } }),
      });
    }, API);
    await book2.reload({ waitUntil: "domcontentloaded" });
    await book2.waitForSelector('[data-testid="open-send-request"]', { timeout: 15000 });

    // With no form link configured the portal says so rather than sending a
    // message with a hole where the link should be.
    await book2.click('[data-testid="open-send-request"]');
    await book2.waitForSelector('[data-testid="msg-blocker"]', { timeout: 15000 });
    check(
      "a feedback request explains that no form link is configured",
      ((await book2.textContent('[data-testid="msg-blocker"]')) ?? "").includes(
        "No feedback form link is configured",
      ),
    );
    check(
      "and it cannot be sent while the link is missing",
      await book2.isDisabled('[data-testid="msg-send"]'),
    );
    // Scoped to the dialog on purpose. The timeline behind it may well say
    // "Google review requested" - those entries are history, and removing the
    // feature must not rewrite what already happened.
    const dialogText = (await book2.textContent('[role="dialog"]')) ?? "";
    check(
      "Google review is no longer offered as something to ask for",
      dialogText.toLowerCase().includes("google review") === false,
      dialogText.slice(0, 80),
    );
    await shoot(page, "17b-send-request");
    await book2.click("text=Cancel");

    /* ------- 6c2c. an admin supplies the link and rewords the message */
    section("6c2c. The form link and templates are editable without a deploy");

    const customerUrl = book2.url();
    await book2.goto(`${APP}/admin/settings`, { waitUntil: "domcontentloaded" });
    await book2.waitForSelector("text=Message templates", { timeout: 15000 });

    const originalTemplate = await book2.inputValue('[id="message.feedback_whatsapp"]');
    check(
      "the message templates are editable in Settings",
      originalTemplate.includes("{customer_name}") && originalTemplate.includes("{link}"),
    );
    check(
      "the feedback form link has its own field",
      (await book2.locator('[id="company.feedback_form_url"]').count()) === 1,
    );
    check(
      "and the Google review settings are gone",
      (await book2.locator('[id="company.google_review_url"]').count()) === 0 &&
        (await book2.locator('[id="message.review_whatsapp"]').count()) === 0,
    );
    await shoot(page, "18-message-templates");

    const reworded = "E2E reworded: {customer_name} — {link}";
    await book2.fill('[id="company.feedback_form_url"]', FORM_URL);
    await book2.fill('[id="message.feedback_whatsapp"]', reworded);
    await book2.click("text=Save changes");
    await book2.waitForSelector("text=Settings saved", { timeout: 15000 });

    // Back to the customer: the dialog must now offer the NEW wording, with
    // the link that was just configured already inside it.
    await book2.goto(customerUrl, { waitUntil: "domcontentloaded" });
    await book2.click('[data-testid="open-send-request"]');
    await book2.waitForFunction(
      () => (document.querySelector('[data-testid="msg-body"]')?.value ?? "").length > 0,
      { timeout: 15000 },
    );
    const composedBody = await book2.inputValue('[data-testid="msg-body"]');
    check(
      "a reworded template is what the next request offers",
      composedBody.startsWith("E2E reworded:"),
      composedBody.slice(0, 60),
    );
    check(
      "the feedback form link is already in the message",
      composedBody.includes(FORM_URL),
    );

    // Email offers a subject; WhatsApp does not.
    await book2.click('[data-testid="msg-email"]');
    await book2.waitForSelector('[data-testid="msg-subject"]', { timeout: 15000 });
    await page
      .waitForFunction(
        () =>
          (document.querySelector('[data-testid="msg-subject"]')?.value ?? "").length > 0,
        { timeout: 15000 },
      )
      .catch(() => {});
    check(
      "an email request carries a subject line",
      ((await book2.inputValue('[data-testid="msg-subject"]')) ?? "").length > 0,
    );

    await book2.click('[data-testid="msg-whatsapp"]');
    await book2.waitForFunction(
      () => (document.querySelector('[data-testid="msg-body"]')?.value ?? "").length > 0,
      { timeout: 15000 },
    );
    check(
      "switching back to WhatsApp drops the subject field",
      (await book2.locator('[data-testid="msg-subject"]').count()) === 0,
    );
    // Send stays disabled until the message for THIS channel has arrived.
    // Without that, pressing it in the gap opened the mail client while the
    // dialog said WhatsApp.
    await book2.waitForFunction(
      () => !document.querySelector('[data-testid="msg-send"]')?.disabled,
      { timeout: 15000 },
    );

    // Capture what the app hands to window.open, rather than chasing the
    // popup around. The thing under test is the LINK the portal builds - a
    // real popup racing a real navigation to a real site proves the same
    // point far less reliably, and wa.me must never actually be contacted.
    await book2.evaluate(() => {
      window.__openedUrl = null;
      window.open = (url) => {
        window.__openedUrl = String(url);
        return null;
      };
    });
    await book2.click('[data-testid="msg-send"]');
    await book2.waitForTimeout(600);
    openedWhatsAppUrl = await book2.evaluate(() => window.__openedUrl);
    await book2.waitForFunction(
      () => document.body.innerText.includes("Feedback requested"),
      { timeout: 20000 },
    );

    check(
      "opening it hands the message to WhatsApp, pre-filled",
      (openedWhatsAppUrl ?? "").startsWith("https://wa.me/91") &&
        (openedWhatsAppUrl ?? "").includes("?text="),
      openedWhatsAppUrl ?? "window.open was never called",
    );
    check(
      "the ask is logged on the customer's timeline",
      ((await book2.textContent("body")) ?? "").includes("Feedback requested"),
    );
    await shoot(page, "17c-request-logged");
    await bookCtx2.unroute("**://wa.me/**");

    // Put the original settings back - this walkthrough runs against real data.
    await book2.evaluate(
      async ({ apiUrl, template }) => {
        const token = window.localStorage.getItem("bde_portal_token");
        await fetch(`${apiUrl}/api/admin/settings`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            values: {
              "message.feedback_whatsapp": template,
              "company.feedback_form_url": "",
            },
          }),
        });
      },
      { apiUrl: API, template: originalTemplate },
    );
    const restored = await book2.evaluate(async (apiUrl) => {
      const token = window.localStorage.getItem("bde_portal_token");
      const body = await fetch(`${apiUrl}/api/admin/settings`, {
        headers: { Authorization: `Bearer ${token}` },
      }).then((response) => response.json());
      return body.values["message.feedback_whatsapp"];
    }, API);
    check("and the original wording is restored afterwards", restored === originalTemplate);

    await bookCtx2.close();

    /* ------------------------------------ 6c3. undo on a lead */
    section("6c3. A stage change is explicit, and needs a reason");
    // A lead of its own, so this journey does not depend on what an earlier
    // one left behind.
    const undoLeadName = `Undo Me ${Date.now()}`;
    const created = await page.evaluate(
      async ({ apiUrl, name }) => {
        const token = window.localStorage.getItem("bde_portal_token");
        const people = await fetch(`${apiUrl}/api/users/actionable`, {
          headers: { Authorization: `Bearer ${token}` },
        }).then((response) => response.json());
        const assignee = people.find((person) => person.name === "Parth Fulvani");
        const response = await fetch(`${apiUrl}/api/leads`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ name, assigned_to_user_id: assignee.id }),
        });
        return response.status;
      },
      { apiUrl: API, name: undoLeadName },
    );
    check("a fresh lead is created for the pipeline journey", created === 201, `status ${created}`);

    await page.goto(`${APP}/leads?tab=all`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("table tbody tr", { timeout: 15000 });
    await page.fill('input[aria-label="Search leads"]', undoLeadName);
    await page.waitForTimeout(1200);
    await page.locator(`button:has-text("${undoLeadName}")`).first().click();
    await page.waitForSelector("text=Move this lead", { timeout: 15000 });

    // Selecting is not saving, and saving needs a reason.
    const update = page.getByRole("button", { name: "Update lead" });
    check("Update lead starts disabled", await update.isDisabled());
    await page.getByRole("button", { name: "Contacted", exact: true }).click();
    check("still disabled with no remark", await update.isDisabled());
    await page.fill("#lead-remark", "Reached them on the second try.");
    await page.waitForTimeout(300);
    check("enabled once a remark is written", !(await update.isDisabled()));
    await update.click();
    await page.waitForSelector("text=NEW → CONTACTED", { timeout: 15000 });
    check("the stage change is recorded with its reason", true);
    await shoot(page, "18-lead-update");

    // No skipping: the only thing that follows CONTACTED is NURTURING.
    const nextText = await page.textContent('[role="dialog"]');
    check(
      "the pipeline offers only the next stage",
      (nextText ?? "").includes("Nurturing") && !(nextText ?? "").includes("Pre-qualified"),
    );

    // Undo is gone. An admin reopen is what replaced it, and a BDE has neither.
    check(
      "no Undo control remains on a lead",
      (await page.locator('button[title="Take this entry back"]').count()) === 0,
    );
    await page.keyboard.press("Escape");

    /* ------------------------------------ 6e. clickable dashboard */
    section("6e. Dashboard KPIs are links");
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="stat"]', { timeout: 15000 });

    const alerted = await page.textContent("body");
    check("the alert banner names the department", (alerted ?? "").includes("need attention") || (alerted ?? "").includes("needs attention"));
    await shoot(page, "16-dashboard-modules");

    await page.click('[data-testid="stat"][data-label="Follow-ups due"]');
    await page.waitForURL("**/references**", { timeout: 15000 });
    check("a KPI tile navigates to its module", page.url().includes("/references"));

    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="stat"][data-label="Open leads"]', {
      timeout: 15000,
    });
    await page.click('[data-testid="stat"][data-label="Open leads"]');
    await page.waitForURL("**/leads**", { timeout: 15000 });
    check("and so does the leads tile", page.url().includes("/leads"));

    // The trail from a converted lead: its KPI opens the queue it landed in.
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="stat"][data-label="Feedback pending"]', {
      timeout: 15000,
    });
    await page.click('[data-testid="stat"][data-label="Feedback pending"]');
    await page.waitForURL("**/feedback**", { timeout: 15000 });
    check(
      "the pending-feedback tile opens the queue",
      page.url().includes("tab=pending"),
      page.url(),
    );

    await context.close();

    /* -------------------------------------------- 7. scoping as a BDE */
    section("7. A BDE sets a password, then sees only their own work");
    context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    page = await context.newPage();

    await signIn(page, BDE, TEMP_PASSWORD);
    await page.waitForURL("**/set-password", { timeout: 15000 });
    check("a reset account is sent to the forced password change", true);
    await shoot(page, "02-set-password");

    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForURL("**/set-password", { timeout: 15000 });
    check("the portal stays locked until the password is set", true);

    await page.fill("#current", TEMP_PASSWORD);
    await page.fill("#next", NEW_PASSWORD);
    await page.fill("#confirm", NEW_PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL("**/login", { timeout: 15000 });
    check("setting the password returns you to sign-in", true);

    await signIn(page, BDE, NEW_PASSWORD);
    await page.waitForURL("**/dashboard", { timeout: 15000 });
    await page.waitForSelector("text=Converted customers", { timeout: 15000 });

    const navHtml = await page.innerHTML("aside");
    check("a BDE has no Team link", !navHtml.includes('href="/team"'));
    check("a BDE has no User admin link", !navHtml.includes('href="/admin/users"'));
    await shoot(page, "10-dashboard-bde");

    check("a BDE has no Customers link", !navHtml.includes('href="/customers"'));

    // The SAP book is Super Admin's. What a BDE still gets is their OWN
    // accounts, through Reference Tracking - which is the whole reason the
    // restriction stopped at the page rather than the customer row.
    await page.goto(`${APP}/references?tab=customers`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("table tbody tr", { timeout: 15000 });
    const parthRows = await page.locator("table tbody tr").count();
    check("a BDE still works the account he owns", parthRows === 1, `saw ${parthRows}`);
    const parthText = await page.textContent("body");
    check(
      "and it is the right one",
      (parthText ?? "").includes("SPLICECONN PRIVATE LIMITED"),
    );
    await shoot(page, "11-accounts-bde");

    // The API is the real boundary: ask it directly, not through the UI.
    const forbidden = await page.evaluate(async (apiUrl) => {
      const token = window.localStorage.getItem("bde_portal_token");
      const response = await fetch(`${apiUrl}/api/users`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return response.status;
    }, API);
    check("the API refuses a BDE the user directory (403)", forbidden === 403, `got ${forbidden}`);

    /* ------------------------- 7b. the modules are actually connected */
    section("7b. A converted lead reaches the feedback module, for its owner");

    // The analysis is department-scoped and stays shut...
    const analysisStatus = await page.evaluate(async (apiUrl) => {
      const token = window.localStorage.getItem("bde_portal_token");
      const response = await fetch(`${apiUrl}/api/feedback/analysis`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return response.status;
    }, API);
    // Feedback reading was opened to everyone: it is how the company sees its
    // own performance. What stays shut is the alert queue, which is a job.
    const alertStatus = await page.evaluate(async (apiUrl) => {
      const token = window.localStorage.getItem("bde_portal_token");
      const response = await fetch(`${apiUrl}/api/feedback/alerts`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return response.status;
    }, API);
    check(
      "a BDE can read how the company is scoring",
      analysisStatus === 200,
      `got ${analysisStatus}`,
    );
    check(
      "but not the alert queue (403)",
      alertStatus === 403,
      `got ${alertStatus}`,
    );

    // The pending queue is the BDE's own work, so that door is open too.
    check("a BDE has the feedback module in the nav", navHtml.includes('href="/feedback"'));

    await page.goto(`${APP}/feedback?tab=pending`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="stat"]', { timeout: 15000 });
    const pendingBody = await page.textContent("body");
    check(
      "the module opens on the pending queue rather than an error",
      !(pendingBody ?? "").includes("Could not load this"),
    );
    check(
      "the pending queue names what is still owed",
      (pendingBody ?? "").includes("Pending requests"),
    );
    await page.goto(`${APP}/feedback?tab=responses`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="stat"]', { timeout: 15000 });
    const reviewsBody = (await page.textContent("body")) ?? "";
    check(
      "and a BDE can read the customer reviews",
      reviewsBody.includes("Customer reviews") &&
        !reviewsBody.includes("Could not load this"),
    );
    await shoot(page, "17-feedback-pending-bde");

    /* ------------------------------- put the password back, always */
    //
    // This run deliberately drives the admin-reset journey, which leaves the
    // BDE on a different password. Leaving it that way meant the next person
    // to open the portal - usually whoever just ran this - was told
    // "Incorrect email or password" with no clue why, and a README line they
    // had no reason to be reading.
    //
    // So the walkthrough cleans up after itself: reset as the admin, complete
    // the forced change as the BDE, land back on the seed password. The same
    // two steps the journey above tests, run in reverse.
    await restoreSeedPassword(browser);
    await context.close();
  } finally {
    await browser.close();
  }

  /* ------------------------------------------------------------ report */
  console.log(`\n${"-".repeat(60)}`);
  console.log(`${passed} passed, ${failures.length} failed`);
  if (failures.length) {
    console.log("\nFailures:");
    for (const failure of failures) console.log(`  · ${failure}`);
    process.exit(1);
  }
  console.log(`Screenshots in ${SHOTS}`);
}

main().catch((error) => {
  console.error("\nE2E run crashed:", error);
  process.exit(1);
});


/**
 * Put the BDE back on the seed password.
 *
 * Uses the product's own two steps rather than reaching into the database: an
 * admin reset, then the forced change completed as that user.
 * `reset_password` always re-arms `must_change_password`, so the second half
 * is not optional - skipping it would leave the account locked out in a new
 * way rather than an old one.
 */
async function restoreSeedPassword(browser) {
  // Two contexts, not one. The admin's session lives in localStorage, so
  // going to /login on the same page bounces straight to /dashboard and the
  // form is never rendered - which is exactly how this failed the first time.
  const adminCtx = await browser.newContext();
  const bdeCtx = await browser.newContext();
  const admin = await adminCtx.newPage();
  const bde = await bdeCtx.newPage();
  try {
    await signIn(admin, ADMIN, SEED_PASSWORD);
    await admin.waitForURL("**/dashboard", { timeout: 15000 });
    await admin.evaluate(
      async ({ apiUrl, email, password }) => {
        const token = window.localStorage.getItem("bde_portal_token");
        const people = await fetch(`${apiUrl}/api/users?page_size=200`, {
          headers: { Authorization: `Bearer ${token}` },
        }).then((response) => response.json());
        const target = people.items.find((person) => person.email === email);
        await fetch(`${apiUrl}/api/users/${target.id}/reset-password`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ new_password: password }),
        });
      },
      { apiUrl: API, email: BDE, password: TEMP_PASSWORD },
    );

    await signIn(bde, BDE, TEMP_PASSWORD);
    await bde.waitForURL("**/set-password", { timeout: 15000 });
    await bde.fill("#current", TEMP_PASSWORD);
    await bde.fill("#next", SEED_PASSWORD);
    await bde.fill("#confirm", SEED_PASSWORD);
    await bde.click('button[type="submit"]');
    await bde.waitForURL("**/login", { timeout: 15000 });

    // Prove it rather than assume it.
    await signIn(bde, BDE, SEED_PASSWORD);
    await bde.waitForURL("**/dashboard", { timeout: 15000 });
    console.log(`\n  ${BDE} is back on the seed password.`);
  } catch (cause) {
    // Never fail the run over cleanup - say so loudly instead, with the
    // command that fixes it.
    try {
      await bde.screenshot({ path: join(SHOTS, "99-restore-failed.png") });
    } catch {
      /* diagnostics only */
    }
    console.log(
      `\n  COULD NOT restore ${BDE}'s password: ${cause}` +
        "\n  Run: cd backend && python -m app.seeds.seed --reset-passwords",
    );
  } finally {
    await adminCtx.close();
    await bdeCtx.close();
  }
}
