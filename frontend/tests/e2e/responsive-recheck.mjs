import { chromium } from "playwright-core";

import { APP, requireSeedPassword } from "./support/session.mjs";

const PASSWORD = requireSeedPassword();
const browser = await chromium.launch({ channel: "chrome" });
const failures = [];

const SIZES = [
  { name: "phone 390", width: 390, height: 844 },
  { name: "phone 360", width: 360, height: 740 },
  { name: "tablet 768", width: 768, height: 1024 },
];
const PAGES = ["/dashboard", "/references", "/feedback?tab=responses", "/leads", "/team"];

for (const size of SIZES) {
  const context = await browser.newContext({
    viewport: { width: size.width, height: size.height },
    isMobile: size.width < 500,
    hasTouch: size.width < 500,
  });
  const page = await context.newPage();
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#identifier", "shail.patel@pouchwale.com");
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/dashboard/, { timeout: 20000 });

  console.log(`\n=== ${size.name} ===`);
  for (const path of PAGES) {
    await page.goto(APP + path, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(1200);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    const ok = overflow <= 1;
    console.log(`    ${ok ? "ok  " : "FAIL"} ${path} (h-overflow ${overflow}px)`);
    if (!ok) failures.push(`${size.name} ${path}: ${overflow}px`);
  }
  await context.close();
}
await browser.close();
console.log("\n" + (failures.length ? "FAILURES:\n" + failures.join("\n") : "NO HORIZONTAL OVERFLOW ANYWHERE"));
process.exit(failures.length ? 1 : 0);
