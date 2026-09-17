/**
 * Role-by-role browser verification of the reconciled portal.
 *
 * READ ONLY. It signs in, reads what each screen actually renders, and
 * compares that against the API. It changes no passwords and writes no data,
 * so it is safe to run against the live dev database.
 */
import { chromium } from "playwright-core";

import {
  APP,
  BROWSER_CHANNEL,
  apiCall,
  apiLogin,
  requireSeedPassword,
} from "./support/session.mjs";

const PASSWORD = requireSeedPassword();

const ROLES = [
  { who: "Portal Owner", email: "owner@pouchwale.com" },
  { who: "Shail Patel", email: "shail.patel@pouchwale.com" },
  { who: "Navya Rupawat", email: "navya.rupawat@pouchwale.com" },
  { who: "Ramanesh Nair", email: "ramanesh.nair@pouchwale.com" },
  { who: "Parth Fulvani", email: "parth.fulvani@pouchwale.com" },
];

/** Does the page actually show this number as a number?
 *
 * Deliberately not a regex: building one from a template literal is how the
 * first version of this file ended up testing for a BACKSPACE character and
 * reporting that every screen was wrong.
 */
function showsNumber(text, value) {
  return text
    .split(/[^0-9]+/)
    .includes(String(value));
}

const failures = [];
function check(label, condition, detail = "") {
  if (condition) console.log(`    ok   ${label}`);
  else {
    console.log(`    FAIL ${label} ${detail}`);
    failures.push(`${label} ${detail}`);
  }
}

async function apiFor(email) {
  const { auth, status } = await apiLogin(email, PASSWORD);
  if (!auth) throw new Error(`could not sign in as ${email} (status ${status})`);
  const get = async (path) => {
    const r = await apiCall(auth, path);
    return r.status >= 200 && r.status < 300 ? r.body : null;
  };
  return { get };
}

const browser = await chromium.launch({ channel: BROWSER_CHANNEL });

for (const { who, email } of ROLES) {
  console.log(`\n=== ${who} ===`);
  const api = await apiFor(email);
  const stats = await api.get("/api/leads/stats");
  const refs = await api.get("/api/references/stats");

  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));

  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#email", email);
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(dashboard|set-password)/, { timeout: 20000 });

  if (page.url().includes("set-password")) {
    console.log("    (forced password change — skipping, not touching it)");
    await context.close();
    continue;
  }

  // ---------------------------------------------------------- dashboard
  await page.waitForSelector("text=Open leads", { timeout: 20000 });
  await page.waitForTimeout(2500);   // the KPI numbers count up
  const dashText = await page.locator("body").innerText();

  check("no SAP wording on the dashboard",
    !/Invoiced in SAP|Last invoice|Latest invoices|SAP import/i.test(dashText),
    dashText.match(/Invoiced in SAP|Last invoice|Latest invoices|SAP import/i)?.[0] ?? "");

  check(`dashboard shows converted = ${stats.converted}`,
    showsNumber(dashText, stats.converted));

  // The team table, for the two shapes that have one.
  const hasTable = await page.locator("th:has-text('Open leads')").count();
  if (hasTable > 0) {
    const headers = await page.locator("table thead th").allInnerTexts();
    const joined = headers.join("|");
    check("team table has no Customers/Invoices/Last invoice/Feedback columns",
      !/Customers|Invoices|Last invoice|^Feedback$/i.test(joined), joined);
    check("team table has Open leads + Converted + Last activity",
      /Open leads/i.test(joined) && /Converted/i.test(joined) && /Last activity/i.test(joined),
      joined);
  }

  // ---------------------------------------------------------- references
  await page.goto(`${APP}/references`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("text=References completed", { timeout: 20000 });
  await page.waitForTimeout(2500);
  const refText = await page.locator("body").innerText();

  check(`references shows converted leads = ${refs.converted_leads}`,
    showsNumber(refText, refs.converted_leads));
  check("references explains why nothing is eligible",
    refs.eligible_accounts > 0 ||
      /awaiting sync|post-sale sync|10-day wait|No converted leads yet/i.test(refText),
    refText.slice(0, 160).replace(/\n/g, " "));
  check("no SAP code column in references",
    !/SAP code|invoiced in SAP/i.test(refText));
  check("filter bar is present",
    (await page.locator("select[aria-label='Reference']").count()) > 0);

  // ------------------------------------------------------------ feedback
  await page.goto(`${APP}/feedback?tab=pending`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("text=Pending requests", { timeout: 20000 });
  await page.waitForTimeout(2500);
  const fbText = await page.locator("body").innerText();
  check("pending queue explains itself",
    /Nobody is waiting|No synced post-sale records yet|Nothing is eligible yet|No converted leads yet|waiting on the post-sale sync/i.test(fbText)
      || (await page.locator("table").count()) > 0,
    fbText.slice(0, 160).replace(/\n/g, " "));

  await page.goto(`${APP}/feedback?tab=responses`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  check("customer reviews has a filter bar",
    (await page.locator("select[aria-label='Rating']").count()) > 0);

  // ---------------------------------------------------------- assigned leads
  await page.goto(`${APP}/leads`, { waitUntil: "domcontentloaded" });
  // Different roles land on different tabs here, and somebody with no leads
  // gets an empty state rather than a filter bar - so wait for the fetches to
  // finish rather than for any one element that only some of them render.
  await page.waitForLoadState("networkidle", { timeout: 30000 });
  await page.waitForTimeout(2500);
  const leadText = await page.locator("body").innerText();
  check(`leads page reports total ${stats.total}`,
    showsNumber(leadText, stats.total));

  // 401 (Unauthorized): the signed-out session probe on /login, by design.
  const real = consoleErrors.filter((e) => !/favicon|404 \(Not Found\)|401 \(Unauthorized\)/i.test(e));
  check("no console errors", real.length === 0, real.slice(0, 2).join(" | "));

  await context.close();
}

await browser.close();
console.log("\n" + (failures.length ? `FAILURES (${failures.length}):\n` + failures.join("\n") : "ALL CHECKS PASSED"));
process.exit(failures.length ? 1 : 0);
