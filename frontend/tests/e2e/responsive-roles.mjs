/**
 * Responsive QA per role: does each person's own version of each page work at
 * phone, tablet and desktop width?
 *
 * Read-only. A role that renders extra columns (an admin's team table) is the
 * one most likely to overflow, so the roles are chosen to cover that.
 */
import { chromium } from "playwright-core";

import { APP, requireSeedPassword } from "./support/session.mjs";

const PW = requireSeedPassword();

const ROLES = [
  { who: "Super Admin", email: "owner@pouchwale.com" },
  { who: "Admin", email: "shail.patel@pouchwale.com" },
  { who: "BDE manager", email: "navya.rupawat@pouchwale.com" },
  { who: "Sales manager", email: "ramanesh.nair@pouchwale.com" },
  { who: "Field BDE", email: "parth.fulvani@pouchwale.com" },
];

const SIZES = [
  { name: "mobile 390", width: 390, height: 844, mobile: true },
  { name: "tablet 768", width: 768, height: 1024, mobile: false },
  { name: "desktop 1440", width: 1440, height: 900, mobile: false },
];

const PAGES = ["/dashboard", "/leads", "/references", "/feedback", "/team", "/admin/users"];

const findings = [];
const browser = await chromium.launch({ channel: "chrome" });

for (const role of ROLES) {
  console.log(`\n=== ${role.who} ===`);
  for (const size of SIZES) {
    const context = await browser.newContext({
      viewport: { width: size.width, height: size.height },
      isMobile: size.mobile,
      hasTouch: size.mobile,
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));

    await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
    await page.fill("#identifier", role.email);
    await page.fill("#password", PW);
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 25000 });

    const broken = [];
    for (const path of PAGES) {
      await page.goto(APP + path, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
      await page.waitForTimeout(900);

      // A page this role may not open is not a failure - it redirects or
      // says so. An overflowing page is.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      if (overflow > 1) broken.push(`${path} overflows ${overflow}px`);

      const text = await page.locator("body").innerText();
      if (/Could not load this|Something went wrong|Application error/i.test(text)) {
        broken.push(`${path} shows an error state`);
      }
      // The page must render something, not a blank shell.
      if (text.trim().length < 40) broken.push(`${path} rendered almost nothing`);
    }

    const real = errors.filter((e) => !/favicon|404/i.test(e));
    if (real.length) broken.push(`console: ${real[0].slice(0, 60)}`);

    if (broken.length) {
      findings.push({ role: role.who, size: size.name, broken });
      console.log(`  FAIL ${size.name}: ${broken.join("; ")}`);
    } else {
      console.log(`  ok   ${size.name}: all ${PAGES.length} pages render, no overflow`);
    }
    await context.close();
  }
}

await browser.close();
console.log();
console.log(findings.length === 0
  ? "RESPONSIVE QA PASSED FOR EVERY ROLE"
  : `${findings.length} responsive findings`);
for (const f of findings) console.log(`  [P4] ${f.role} @ ${f.size}: ${f.broken.join("; ")}`);
process.exit(findings.length ? 1 : 0);
