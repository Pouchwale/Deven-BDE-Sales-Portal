/**
 * Set-a-password and permanent-delete, driven as a Super Admin.
 *
 * Writes, so it creates its own throwaway account and deletes it again. It
 * never touches a real person's password.
 */
import { chromium } from "playwright-core";

import {
  APP,
  BROWSER_CHANNEL,
  apiCall,
  apiLogin,
  requireSeedPassword,
} from "./support/session.mjs";

const SEED = requireSeedPassword();
const failures = [];
const check = (label, ok, detail = "") => {
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(label);
};

const owner = (await apiLogin("owner@pouchwale.com", SEED)).auth;
if (!owner) {
  console.error("Could not sign in as owner@pouchwale.com with E2E_SEED_PASSWORD.");
  process.exit(1);
}
const throwaway = `qa.temp.${Date.now()}@pouchwale.com`;
const initialPassword = `Temp${Date.now().toString().slice(-8)}abc`;
const created = (
  await apiCall(owner, "/api/users", {
    method: "POST",
    body: JSON.stringify({
      name: "QA Throwaway",
      email: throwaway,
      password: initialPassword,
      confirm_password: initialPassword,
      role: "BDE",
    }),
  })
).body;
console.log(`created throwaway ${created?.id}`);
// Signing in writes an audit row, and an account with an audit trail cannot be
// deleted (by design). Step 1 signs the first throwaway in, so the delete
// journey uses a second one that never signs in.
const cleanEmail = `qa.clean.${Date.now()}@pouchwale.com`;
const cleanCreated = await apiCall(owner, "/api/users", {
  method: "POST",
  body: JSON.stringify({
    name: "QA Clean Throwaway",
    email: cleanEmail,
    password: initialPassword,
    confirm_password: initialPassword,
    role: "BDE",
  }),
});
console.log(`created clean throwaway (status ${cleanCreated.status})`);

const browser = await chromium.launch({ channel: BROWSER_CHANNEL });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
await page.fill("#email", "owner@pouchwale.com");
await page.fill("#password", SEED);
await page.click('button[type="submit"]');
await page.waitForURL(/dashboard/, { timeout: 20000 });

async function openMenu(name, term = name) {
  await page.goto(`${APP}/admin/users`, { waitUntil: "domcontentloaded" });
  await page.fill("input[type='search']", term);
  await page.waitForTimeout(1800);
  const rows = await page.locator(`button[aria-label^="Actions for ${name}"]`).count();
  if (rows !== 1) throw new Error(`expected exactly one "${name}" row, found ${rows}`);
  await page.click(`button[aria-label^="Actions for ${name}"]`);
}

/* ---------------------------------------- 1. set a typed password */
// The server never invents a password any more, so there is nothing to
// generate or show: the administrator types it twice and it is never echoed.
console.log("\n1. Change password — typed twice, never shown");
await openMenu("QA Throwaway", throwaway);
await page.click('[role="menuitem"]:has-text("Change password")');
await page.waitForSelector('[role="dialog"] #new-password', { timeout: 10000 });
const dialog = page.locator('[role="dialog"]');
check("no generate option is offered", !(await dialog.innerText()).includes("Generate"));
check("it warns the portal cannot show a password",
  (await dialog.innerText()).includes("one-way hashes"));
const typed = `Qa${Date.now().toString().slice(-8)}Set`;
await page.fill("#new-password", typed);
await page.fill("#confirm-password", typed);
await page.click('button[form="reset-form"]');
await page.waitForSelector('[role="dialog"] #new-password', { state: "detached", timeout: 15000 });
check("the password is not displayed anywhere afterwards",
  !(await page.locator("body").innerText()).includes(typed));

// It must actually work as a password.
const asUser = await apiLogin(throwaway, typed);
check("the new password signs in", asUser.status === 200, `status ${asUser.status}`);
check("and forces a change", asUser.body.must_change_password === true);

/* ------------------------------- 2. delete is refused for a real person */
console.log("\n2. Delete — refused for somebody with history");
await openMenu("Parth Fulvani");
const menu = await page.locator('[role="menu"]').innerText();
check("Super Admin sees Delete permanently", menu.includes("Delete permanently"));
await page.click('[role="menuitem"]:has-text("Delete permanently")');
await page.waitForSelector('[role="dialog"]', { timeout: 10000 });
await page.click('button:has-text("Delete for good")');
await page.waitForTimeout(2500);
const refusal = await page.locator('[role="dialog"]').innerText();
check("the refusal says the account has history", refusal.includes("has history"), refusal.slice(0, 90));
check("and lists what is attached", /\d+ in \w+/.test(refusal));
check("and offers deactivate instead", refusal.includes("Deactivate instead"));
await page.keyboard.press("Escape");

/* ------------------------------- 3. delete works on a clean account */
console.log("\n3. Delete — allowed for an account with no history");
await openMenu("QA Clean Throwaway", cleanEmail);
await page.click('[role="menuitem"]:has-text("Delete permanently")');
await page.waitForSelector('[role="dialog"]', { timeout: 10000 });
await page.click('button:has-text("Delete for good")');
await page.waitForTimeout(2500);
const gone = (await apiCall(owner, "/api/users?page_size=200&include_inactive=true")).body;
check("the account is gone entirely",
  !gone.items.some((u) => u.email === cleanEmail));

// The signed-in throwaway has an audit trail now: deactivate, not delete.
if (created?.id) {
  const off = await apiCall(owner, `/api/users/${created.id}`, { method: "DELETE" });
  console.log(`deactivated the signed-in throwaway (status ${off.status})`);
}

const real = errors.filter((e) => !/favicon|404|Failed to load resource/i.test(e));
check("no console errors", real.length === 0, real.slice(0, 2).join(" | "));

await browser.close();
console.log("\n" + (failures.length ? `FAILURES (${failures.length}): ${failures.join(", ")}` : "ALL CHECKS PASSED"));
process.exit(failures.length ? 1 : 0);
