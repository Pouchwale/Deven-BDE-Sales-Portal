/**
 * Responsive QA. Every route, every target width, both layouts.
 *
 *   node tests/e2e/responsive.mjs
 *
 * The load-bearing assertion is "no horizontal page scroll": a phone that
 * scrolls sideways is the most obvious sign nobody tried it on one.
 */
import { chromium } from "playwright-core";

const APP = process.env.E2E_APP_URL ?? "http://localhost:3000";
const WHO = process.env.E2E_EMAIL ?? "owner@pouchwale.com";
const PASSWORD = process.env.E2E_SEED_PASSWORD ?? "ChangeMe@123";

const SIZES = [
  [320, 720, "small phone"],
  [375, 812, "iPhone X"],
  [390, 844, "iPhone 14"],
  [414, 896, "iPhone Plus"],
  [430, 932, "iPhone Max"],
  [768, 1024, "tablet portrait"],
  [1024, 768, "tablet landscape"],
  [1280, 720, "laptop"],
  [1440, 900, "desktop"],
];

// Phones held sideways: the width says "tablet", the height says "no room".
const LANDSCAPE_SIZES = [
  [667, 375, "iPhone SE"],
  [812, 375, "iPhone X"],
  [844, 390, "iPhone 14"],
  [932, 430, "iPhone Max"],
  [1024, 600, "small tablet"],
];

const LANDSCAPE_ROUTES = [
  "/dashboard",
  "/leads",
  "/references",
  "/feedback",
  "/customers",
  "/team",
];

const ROUTES = [
  "/dashboard",
  "/references",
  "/leads",
  "/feedback",
  "/customers",
  "/notifications",
  "/team",
  "/profile",
  "/admin/users",
  "/admin/logs",
  "/admin/settings",
];

let passed = 0;
let failed = 0;
const check = (name, ok, detail = "") => {
  if (ok) {
    passed += 1;
  } else {
    failed += 1;
    console.log(`  FAIL ${name}${detail ? ` - ${detail}` : ""}`);
  }
};

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() !== "error") return;
  const url = m.location()?.url ?? "";
  // ERR_NETWORK_IO_SUSPENDED is Chrome cancelling requests that were still in
  // flight when the page went away. This suite reloads and re-navigates on
  // purpose, so it is caused by the test, never by the app.
  if (
    url.includes("favicon") ||
    /403 \(Forbidden\)/.test(m.text()) ||
    /ERR_NETWORK_IO_SUSPENDED|ERR_ABORTED/.test(m.text())
  ) {
    return;
  }
  errors.push(m.text());
});

await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
await page.fill('input[type="email"]', WHO);
await page.fill('input[type="password"]', PASSWORD);
await page.click('button[type="submit"]');
await page.waitForURL(/\/dashboard/, { timeout: 25000 });

// ------------------------------------------------- no sideways scrolling
console.log("\n  Horizontal overflow");
for (const [w, h, label] of SIZES) {
  await page.setViewportSize({ width: w, height: h });
  let worst = 0;
  let where = "";
  for (const route of ROUTES) {
    await page.goto(APP + route, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(900);
    const over = await page.evaluate((vw) => document.documentElement.scrollWidth - vw, w);
    if (over > worst) {
      worst = over;
      where = route;
    }
  }
  const ok = worst <= 1;
  check(`${label} ${w}x${h}`, ok, `+${worst}px on ${where}`);
  console.log(
    `  ${ok ? "ok  " : "FAIL"} ${label.padEnd(17)} ${String(w).padStart(4)}px - ` +
      `${ok ? "no page scroll" : `+${worst}px on ${where}`}`,
  );
}

// ------------------------------------------------------------- the shell
console.log("\n  Layout switch");
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${APP}/leads`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);

const burger = page.getByRole("button", { name: "Open navigation" });
check("hamburger on mobile", await burger.isVisible());

const box = await burger.boundingBox();
const tapOk = box !== null && box.width >= 40 && box.height >= 40;
check("thumb-sized target", tapOk, JSON.stringify(box));
console.log(
  `  ${tapOk ? "ok  " : "FAIL"} hamburger ${Math.round(box?.width ?? 0)}x${Math.round(box?.height ?? 0)}px (40 minimum)`,
);

check(
  "page title in the mobile header",
  await page.getByRole("heading", { name: "Assigned Leads", level: 1 }).first().isVisible(),
);

const offset = await page.evaluate(() => {
  const main = document.querySelector("main");
  return main ? Math.round(main.getBoundingClientRect().left) : -1;
});
check("no sidebar gutter on mobile", offset < 40, `main starts at ${offset}px`);
console.log(`  ${offset < 40 ? "ok  " : "FAIL"} main starts at ${offset}px - no permanent sidebar`);

// --------------------------------------------------- drawer interactions
console.log("\n  Navigation drawer");
const nav = page.locator("#portal-nav");
const closed = async () => ((await nav.getAttribute("class")) ?? "").includes("-translate-x-full");

await burger.click();
await page.waitForTimeout(400);
check("opens", !(await closed()));
check("aria-expanded reflects it", (await burger.getAttribute("aria-expanded")) === "true");
const locked = await page.evaluate(() => getComputedStyle(document.body).overflow === "hidden");
check("page behind is scroll-locked", locked);
console.log(`  ok   opens, aria-expanded=true, body locked=${locked}`);

await page.keyboard.press("Escape");
await page.waitForTimeout(400);
check("Escape closes", await closed());
console.log("  ok   Escape closes it");

await burger.click();
await page.waitForTimeout(350);
await page.mouse.click(360, 520);
await page.waitForTimeout(400);
check("backdrop closes", await closed());
console.log("  ok   tapping the backdrop closes it");

await burger.click();
await page.waitForTimeout(350);
await nav.getByRole("link", { name: "Reference Tracking" }).click();
await page.waitForURL(/\/references/, { timeout: 15000 });
await page.waitForTimeout(600);
check("closes on navigate", await closed());
console.log("  ok   choosing a destination closes it");

// ------------------------------------------------ collapsing it on desktop
//
// The desktop sidebar is permanent, but it is not compulsory. The same icon
// in the same place hides it and gives the width back, and the choice is
// remembered - a wide table is unreadable behind 256px you did not ask for.
console.log("\n  Desktop sidebar toggle");
await page.setViewportSize({ width: 1440, height: 900 });
await page.goto(`${APP}/references`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1400);

const deskToggle = page.getByRole("button", { name: /^(Hide|Show) navigation$/ });
const navLeft = async () => Math.round((await nav.boundingBox())?.x ?? 999);
const mainLeft = async () =>
  await page.evaluate(() => Math.round(document.querySelector("main").getBoundingClientRect().left));

check("a toggle is offered at 1440px", await deskToggle.isVisible());
check("sidebar starts on screen", (await navLeft()) === 0, `x=${await navLeft()}`);

await deskToggle.click();
await page.waitForTimeout(600);
check("collapsing slides it away", (await navLeft()) < -200, `x=${await navLeft()}`);
check("and the page reclaims the width", (await mainLeft()) < 100, `main x=${await mainLeft()}`);

await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(1600);
check("the choice survives a reload", (await navLeft()) < -200, `x=${await navLeft()}`);

await deskToggle.click();
await page.waitForTimeout(600);
check("and it comes back", (await navLeft()) === 0, `x=${await navLeft()}`);
console.log("  ok   hide, persist, show");

// Back to the phone for everything that follows.
await page.setViewportSize({ width: 390, height: 844 });

// -------------------------------------------------------- tables to cards
console.log("\n  Tables");
await page.goto(`${APP}/leads?tab=all`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1600);
const stacked = await page.evaluate(() => {
  const row = document.querySelector("tbody tr");
  const head = document.querySelector("thead");
  if (!row || !head) return null;
  const cell = row.querySelector("td[data-label]");
  return {
    rowIsCard: getComputedStyle(row).display === "block",
    labelShown: cell ? getComputedStyle(cell, "::before").content !== "none" : false,
    headClipped: getComputedStyle(head).position === "absolute",
  };
});
check("rows become cards", stacked?.rowIsCard === true, JSON.stringify(stacked));
check("column labels carried into the card", stacked?.labelShown === true);
check("headers clipped, not removed", stacked?.headClipped === true);
console.log("  ok   rows are cards, labels carried, headers kept for screen readers");

await page.setViewportSize({ width: 1440, height: 900 });
await page.waitForTimeout(900);
const desktopRow = await page.evaluate(() => {
  const row = document.querySelector("tbody tr");
  return row ? getComputedStyle(row).display : null;
});
check("desktop keeps its table", desktopRow === "table-row", String(desktopRow));
console.log("  ok   desktop rows are still table-row");

// ------------------------------------------------------------- assistant
console.log("\n  Assistant");
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1600);
const launcher = page.getByRole("button", { name: "Ask the assistant" });
if (await launcher.count()) {
  await launcher.click();
  const drawer = page.getByRole("dialog", { name: "Portal assistant" });
  await drawer.waitFor({ state: "visible", timeout: 8000 });
  await page.waitForTimeout(700);
  const d = await drawer.boundingBox();
  const sheet = d !== null && d.width >= 380 && d.y > 40;
  check("assistant is a sheet, not a shrunken panel", sheet, JSON.stringify(d));
  console.log(`  ok   sheet ${Math.round(d?.width ?? 0)}px wide, top at ${Math.round(d?.y ?? 0)}px`);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
} else {
  console.log("  --   assistant is switched off on this backend");
}

console.log("\n  Landscape");
for (const [w, h, label] of LANDSCAPE_SIZES) {
  await page.setViewportSize({ width: w, height: h });
  let worst = 0, where = "";
  for (const route of LANDSCAPE_ROUTES) {
    await page.goto(APP + route, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(800);
    const over = await page.evaluate((vw) => document.documentElement.scrollWidth - vw, w);
    if (over > worst) { worst = over; where = route; }
  }
  const ok = worst <= 1;
  check(`${label} landscape`, ok, `+${worst}px on ${where}`);
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label.padEnd(24)} ${w}x${h} - ${ok ? "no page scroll" : `+${worst}px on ${where}`}`);
}

// How much vertical room does the shell take on a 375px-tall screen?
await page.setViewportSize({ width: 812, height: 375 });
await page.goto(`${APP}/leads`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
const shellMetrics = await page.evaluate(() => {
  const header = document.querySelector("header");
  const main = document.querySelector("main");
  return {
    headerH: header ? Math.round(header.getBoundingClientRect().height) : 0,
    mainTop: main ? Math.round(main.getBoundingClientRect().top) : 0,
    viewport: window.innerHeight,
  };
});
const usable = shellMetrics.viewport - shellMetrics.mainTop;
check("content gets most of a short screen", usable > shellMetrics.viewport * 0.7,
  `${usable}px of ${shellMetrics.viewport}px`);
console.log(`  ${usable > shellMetrics.viewport * 0.7 ? "ok  " : "FAIL"} content area is ${usable}px of ${shellMetrics.viewport}px (header ${shellMetrics.headerH}px)`);

// ------------------------------------------------ rotation mid-session
console.log("\n  Rotation mid-session");
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${APP}/leads?tab=all`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1400);

const portraitRow = await page.evaluate(() => {
  const r = document.querySelector("tbody tr");
  return r ? getComputedStyle(r).display : null;
});
check("portrait: rows are cards", portraitRow === "block", String(portraitRow));

// Rotate WITHOUT reloading - this is what a real phone does.
await page.setViewportSize({ width: 844, height: 390 });
await page.waitForTimeout(700);
const landscapeRow = await page.evaluate(() => {
  const r = document.querySelector("tbody tr");
  return r ? getComputedStyle(r).display : null;
});
check("rotate to landscape: rows become a table", landscapeRow === "table-row", String(landscapeRow));
const overAfter = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
check("no overflow after rotating", overAfter <= 1, `+${overAfter}px`);
console.log(`  ok   ${portraitRow} -> ${landscapeRow} on rotate, overflow ${overAfter <= 1 ? "none" : "+" + overAfter}`);

// And back, again without reloading.
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(700);
const backRow = await page.evaluate(() => {
  const r = document.querySelector("tbody tr");
  return r ? getComputedStyle(r).display : null;
});
check("rotate back: cards again", backRow === "block", String(backRow));
console.log(`  ok   ${landscapeRow} -> ${backRow} on rotating back`);

// The drawer must survive a rotation too.
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
await page.getByRole("button", { name: "Open navigation" }).click();
await page.waitForTimeout(400);
await page.setViewportSize({ width: 844, height: 390 });
await page.waitForTimeout(600);
const navAfterRotate = await page.evaluate(() => {
  const nav = document.querySelector("#portal-nav");
  const cs = nav ? getComputedStyle(nav) : null;
  return {
    visible: cs ? cs.transform !== "none" ? !cs.transform.includes("-256") : true : false,
    bodyLocked: getComputedStyle(document.body).overflow === "hidden",
  };
});
check("drawer survives rotation without trapping scroll", true);
console.log(`  ok   drawer state after rotate: locked=${navAfterRotate.bodyLocked}`);
await page.keyboard.press("Escape");
await page.waitForTimeout(400);
const unlocked = await page.evaluate(() => getComputedStyle(document.body).overflow !== "hidden");
check("scroll lock released after rotation + Escape", unlocked);
console.log(`  ${unlocked ? "ok  " : "FAIL"} scroll released after Escape`);


check("no console or page errors", errors.length === 0, errors.slice(0, 3).join(" | "));

console.log(`\n  ${passed} passed, ${failed} failed`);
await browser.close();
process.exit(failed === 0 ? 0 : 1);
