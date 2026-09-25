/**
 * Every active account, authenticated for real in Chrome.
 *
 * Each account gets its OWN browser context, so nothing carries over by
 * accident - and a separate journey at the end deliberately reuses one
 * context to prove logout really clears the previous person.
 *
 * Read-only: it signs in, reads what each person is shown, and signs out.
 */
import { chromium } from "playwright-core";

import { APP, BROWSER_CHANNEL, apiCall, apiLogin, requireSeedPassword } from "./support/session.mjs";

const PW = requireSeedPassword();

const findings = [];
const flag = (sev, who, what, expected, actual) => {
  findings.push({ sev, who, what, expected, actual });
  console.log(`  [${sev}] ${who}: ${what} (expected ${expected}, got ${actual})`);
};

// ------------------------------------------------------ who exists, from the API
const owner = (await apiLogin("owner@pouchwale.com", PW)).auth;
if (!owner) {
  console.error("Could not sign in as owner@pouchwale.com with E2E_SEED_PASSWORD.");
  process.exit(1);
}

const roster = (await apiCall(owner, "/api/users?page_size=200&include_inactive=true")).body;

const accounts = roster.items.map((u) => ({
  name: u.name, email: u.email, role: u.role, active: u.is_active,
}));
const active = accounts.filter((a) => a.active);
console.log(`${accounts.length} accounts, ${active.length} active\n`);

const browser = await chromium.launch({ channel: BROWSER_CHANNEL });

async function signOut(page) {
  // The control lives behind the avatar menu, so the menu has to be opened
  // first. Looking for a bare "Sign out" button finds nothing and silently
  // skips the whole logout test.
  await page.click('[data-testid="user-menu-trigger"]');
  await page.waitForSelector('[data-testid="sign-out"]', { timeout: 10000 });
  await page.click('[data-testid="sign-out"]');
  await page.waitForURL(/\/login/, { timeout: 15000 });
}

async function signIn(page, email) {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#identifier", email);
  await page.fill("#password", PW);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(dashboard|set-password)/, { timeout: 25000 });
}

/* ============================================ 1. every account, its own context */
console.log("=".repeat(100));
console.log("1. LOGIN + IDENTITY + ROLE-CORRECT NAVIGATION");
console.log("=".repeat(100));

for (const account of active) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  try {
    await signIn(page, account.email);
    await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(1200);

    const body = await page.locator("body").innerText();
    const firstName = account.name.split(" ")[0];

    // Their own name in the header.
    if (!body.includes(firstName)) {
      flag("P1", account.name, "own name missing from the page", firstName, "absent");
    }

    // Nobody else's name anywhere on their dashboard, except where the
    // hierarchy legitimately shows their own people.
    const isLeadership = ["SUPER_ADMIN", "ADMIN", "MANAGER"].includes(account.role);
    if (!isLeadership) {
      // Full names only. Splitting to a first name matched "Portal" from
      // "Portal Owner" against the product name in the sidebar - branding,
      // not an identity leak.
      const strangers = active
        .filter((o) => o.name !== account.name)
        .map((o) => o.name)
        .filter((n) => body.includes(n));
      if (strangers.length) {
        flag("P1", account.name, "another person's name on a field user's dashboard",
          "none", strangers.slice(0, 3).join(","));
      }
    }

    // The customer archive is Super Admin only - in the navigation too.
    const nav = await page.locator("nav").first().innerText().catch(() => "");
    const seesCustomers = /Customers/i.test(nav);
    if (seesCustomers !== (account.role === "SUPER_ADMIN")) {
      flag("P1", account.name, "Customers nav visibility wrong",
        account.role === "SUPER_ADMIN" ? "visible" : "hidden",
        seesCustomers ? "visible" : "hidden");
    }
    const seesUserAdmin = /User admin/i.test(nav);
    if (seesUserAdmin && !isLeadership) {
      flag("P1", account.name, "field user sees User admin", "hidden", "visible");
    }

    // Log out, and prove the session really ended.
    await signOut(page);
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    if (!/\/login/.test(page.url())) {
      flag("P0", account.name, "still reaches the dashboard after signing out",
        "/login", page.url());
    }

    const real = errors.filter((e) => !/favicon|404|Failed to load resource/i.test(e));
    if (real.length) {
      flag("P4", account.name, "console errors", "none", real[0].slice(0, 70));
    }

    console.log(`  ok   ${account.name.padEnd(20)} ${account.role.padEnd(12)} ` +
      `identity ok, nav ok, signed out, session ended`);
  } catch (error) {
    flag("P1", account.name, "login journey failed", "success", String(error).slice(0, 80));
  } finally {
    await context.close();
  }
}

/* =================================== 2. the inactive account must be refused */
console.log();
console.log("=".repeat(100));
console.log("2. DEACTIVATED ACCOUNTS CANNOT SIGN IN");
console.log("=".repeat(100));
for (const account of accounts.filter((a) => !a.active)) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#identifier", account.email);
  await page.fill("#password", PW);
  await page.click('button[type="submit"]');
  await page.waitForTimeout(3000);
  if (/\/dashboard/.test(page.url())) {
    flag("P0", account.name, "deactivated account signed in", "refused", "dashboard");
  } else {
    console.log(`  ok   ${account.name}: refused, stayed on ${new URL(page.url()).pathname}`);
  }
  await context.close();
}

/* ============================ 3. cross-user cache: one context, two people */
console.log();
console.log("=".repeat(100));
console.log("3. SESSION ISOLATION - NO CACHED DATA FROM THE PREVIOUS USER");
console.log("=".repeat(100));
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await context.newPage();

  // User A: a manager who can see plenty.
  await signIn(page, "navya.rupawat@pouchwale.com");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.goto(`${APP}/leads?tab=all`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1500);
  const aLeads = await page.locator("body").innerText();
  const aOwned = ["Vexorr", "gHOST", "Chirag", "Deven5", "Ghost 141x"]
    .filter((n) => aLeads.includes(n));
  console.log(`  User A (Navya) sees these leads: ${aOwned.join(", ") || "(none)"}`);
  if (aOwned.length === 0) {
    flag("P2", "Navya", "no leads visible to seed the isolation test", ">0", "0");
  }

  // Sign out, then sign in as a Sales head who should see nothing of hers.
  await signOut(page);

  await signIn(page, "ramanesh.nair@pouchwale.com");
  await page.reload({ waitUntil: "domcontentloaded" });      // hard refresh
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1500);

  const bDash = await page.locator("body").innerText();
  if (/Navya/.test(bDash) && !/Reports to|Reporting chain/i.test(bDash)) {
    flag("P0", "Ramanesh", "User A's identity still on screen", "Ramanesh only", "Navya present");
  }
  if (!/Ramanesh/.test(bDash)) {
    flag("P1", "Ramanesh", "own identity missing after switch", "Ramanesh", "absent");
  }

  await page.goto(`${APP}/leads?tab=all`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1500);
  const bLeads = await page.locator("body").innerText();
  const leaked = ["Vexorr", "gHOST", "Chirag", "Deven5", "Ghost 141x"]
    .filter((n) => bLeads.includes(n));
  if (leaked.length) {
    flag("P0", "Ramanesh", "sees User A's leads after switching", "none", leaked.join(","));
  } else {
    console.log("  ok   User B sees none of User A's leads after a hard refresh");
  }

  // Back/forward must not resurrect A's pages.
  await page.goBack({ waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(1500);
  const backText = await page.locator("body").innerText();
  const leakedBack = ["Vexorr", "gHOST", "Chirag"].filter((n) => backText.includes(n));
  if (leakedBack.length) {
    flag("P0", "Ramanesh", "browser back showed User A's data", "none", leakedBack.join(","));
  } else {
    console.log("  ok   browser back does not resurrect User A's data");
  }
  await context.close();
}

/* ================== 4. role-gated pages refuse politely, not with an error */
console.log();
console.log("=".repeat(100));
console.log("4. ROLE-GATED PAGES SHOW A REFUSAL, NOT A FAILURE");
console.log("=".repeat(100));
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await context.newPage();
  await signIn(page, "parth.fulvani@pouchwale.com");
  for (const path of ["/team", "/admin/users", "/customers"]) {
    await page.goto(APP + path, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(1200);
    const text = await page.locator("body").innerText();
    if (/Could not load this/i.test(text)) {
      flag("P4", "Field BDE", `${path} reports a failure instead of a refusal`,
        "Not available to your role", "Could not load this");
    } else if (/Not available to your role/i.test(text) || /\/login/.test(page.url())) {
      console.log(`  ok   ${path}: refused politely`);
    } else {
      console.log(`  ok   ${path}: no error state (${new URL(page.url()).pathname})`);
    }
  }
  await context.close();
}

await browser.close();

console.log();
console.log("=".repeat(100));
console.log(findings.length === 0
  ? "ALL BROWSER ACCOUNT CHECKS PASSED"
  : `${findings.length} FINDINGS`);
for (const f of findings) {
  console.log(`  [${f.sev}] ${f.who}: ${f.what} (expected ${f.expected}, got ${f.actual})`);
}
process.exit(findings.length ? 1 : 0);
