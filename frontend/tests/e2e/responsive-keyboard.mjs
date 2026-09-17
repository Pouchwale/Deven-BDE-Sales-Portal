/**
 * The on-screen keyboard, faked.
 *
 * Headless Chrome has no soft keyboard, so `visualViewport` is replaced with
 * a controllable stand-in before the app boots. That is exactly what a real
 * keyboard does to the page: layout viewport unchanged, visual viewport
 * shorter, a resize event.
 */
import { chromium } from "playwright-core";

import { APP, requireSeedPassword } from "./support/session.mjs";

const PASSWORD = requireSeedPassword();

let pass = 0, fail = 0;
const check = (n, ok, d = "") => {
  if (ok) { pass++; console.log(`  ok   ${n}`); }
  else { fail++; console.log(`  FAIL ${n}${d ? ` - ${d}` : ""}`); }
};

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });

await page.addInitScript(() => {
  const listeners = { resize: [], scroll: [] };
  const fake = {
    height: window.innerHeight,
    offsetTop: 0,
    addEventListener: (type, fn) => listeners[type]?.push(fn),
    removeEventListener: (type, fn) => {
      const list = listeners[type];
      if (list) list.splice(list.indexOf(fn), 1);
    },
  };
  Object.defineProperty(window, "visualViewport", { value: fake, configurable: true });
  // The handle the test drives.
  window.__keyboard = (px) => {
    fake.height = window.innerHeight - px;
    listeners.resize.forEach((fn) => fn());
  };
  window.__listenerCount = () => listeners.resize.length;
});

await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
await page.fill('input[type="email"]', "owner@pouchwale.com");
await page.fill('input[type="password"]', PASSWORD);
await page.click('button[type="submit"]');
await page.waitForURL(/\/dashboard/, { timeout: 25000 });
await page.waitForTimeout(1500);

const launcher = page.getByRole("button", { name: "Ask the assistant" });
if (!(await launcher.count())) {
  console.log("  --   assistant is switched off; nothing to test");
  await browser.close();
  process.exit(0);
}

// ------------------------------------------------------- no listener idle
check("no viewport listener while the sheet is closed",
  (await page.evaluate(() => window.__listenerCount())) === 0);

await launcher.click();
const drawer = page.getByRole("dialog", { name: "Portal assistant" });
await drawer.waitFor({ state: "visible", timeout: 8000 });
await page.waitForTimeout(700);

check("listener attaches when the sheet opens",
  (await page.evaluate(() => window.__listenerCount())) > 0);

const before = await drawer.boundingBox();
const composerBefore = await page
  .getByRole("textbox", { name: "Message the assistant" })
  .boundingBox()
  .catch(() => null);

// ----------------------------------------------------- keyboard comes up
await page.evaluate(() => window.__keyboard(336));
await page.waitForTimeout(500);

const after = await drawer.boundingBox();
const composerAfter = await page
  .getByRole("textbox", { name: "Message the assistant" })
  .boundingBox()
  .catch(() => null);

const lifted = before && after ? Math.round(before.y + before.height - (after.y + after.height)) : 0;
check("the sheet lifts above the keyboard", lifted > 300, `moved ${lifted}px, wanted ~336`);

const visibleTop = 844 - 336;
const composerVisible =
  composerAfter !== null && composerAfter.y + composerAfter.height <= visibleTop + 2;
check("the input is above the keyboard line", composerVisible,
  composerAfter ? `input bottom ${Math.round(composerAfter.y + composerAfter.height)}, keyboard starts ${visibleTop}` : "no input");

// --------------------------------------------------------- keyboard away
await page.evaluate(() => window.__keyboard(0));
await page.waitForTimeout(500);
const restored = await drawer.boundingBox();
check("the sheet returns when the keyboard closes",
  restored !== null && Math.abs(restored.y + restored.height - 844) < 2,
  restored ? `bottom at ${Math.round(restored.y + restored.height)}` : "gone");

// --------------------------------------------------------- cleanup
await page.keyboard.press("Escape");
await page.waitForTimeout(600);
check("listener detaches when the sheet closes",
  (await page.evaluate(() => window.__listenerCount())) === 0);

console.log(`\n  ${pass} passed, ${fail} failed`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
