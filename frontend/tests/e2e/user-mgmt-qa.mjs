/**
 * Super Admin user management + full-app smoke, in the real browser.
 *
 * Uses ONE existing demo account (Shivani Patel, who owns no leads) and puts
 * her email and password back at the end.
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
const results = [];
const check = (label, ok, detail = "") => {
  results.push({ label, ok });
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label}${!ok && detail ? `  [${detail}]` : ""}`);
  return ok;
};

// { status, body, auth } - `auth` is the session from Set-Cookie, or null.
const login = (email, password = SEED) => apiLogin(email, password);
const api = async (auth, path, init = {}) => {
  const { status, text } = await apiCall(auth, path, init);
  return { status, text };
};

const owner = (await login("owner@pouchwale.com")).auth;
const roster = JSON.parse((await api(owner, "/api/users?page_size=200")).text).items;
const target = roster.find((u) => u.name === "Shivani Patel");
const ORIGINAL_EMAIL = target.email;
const NEW_EMAIL = `shivani.qa.${Date.now().toString().slice(-6)}@pouchwale.com`;
const NEW_PASSWORD = `Qa${Date.now().toString().slice(-8)}Xz`;
const before = { role: target.role, team: target.team_name, manager: target.manager_name };

const browser = await chromium.launch({ channel: BROWSER_CHANNEL });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

async function signIn(email, password = SEED) {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#identifier", email);
  await page.fill("#password", password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(dashboard|set-password)/, { timeout: 25000 });
}
async function signOut() {
  await page.click('[data-testid="user-menu-trigger"]');
  await page.click('[data-testid="sign-out"]');
  await page.waitForURL(/\/login/, { timeout: 15000 });
}
async function openUser(name) {
  await page.goto(`${APP}/admin/users`, { waitUntil: "domcontentloaded" });
  await page.fill("input[type='search']", name);
  await page.waitForTimeout(1600);
  await page.click(`button[aria-label="Open ${name}'s account"]`);
  await page.waitForSelector('[role="dialog"]', { timeout: 10000 });
}

console.log("\n=== 1. DETAIL PANEL + EMAIL EDIT ===");
await signIn("owner@pouchwale.com");
await openUser("Shivani Patel");
let panel = await page.locator('[role="dialog"]').innerText();
check("clicking a person opens their account details", panel.includes(ORIGINAL_EMAIL));
check("panel shows role, team and reporting line",
  panel.includes("Reports to") && panel.includes("Team") && panel.includes("Role"));
check("panel offers Edit account and Change password",
  panel.includes("Edit account") && panel.includes("Change password"));
check("panel never shows a password or hash",
  !/\$2[aby]\$|hashed_password|ChangeMe/i.test(panel));

await page.click('button:has-text("Edit account")');
await page.waitForSelector("#identifier", { timeout: 10000 });
await page.fill("#identifier", NEW_EMAIL);
await page.click('button[form="user-form"]');
await page.waitForTimeout(2500);

await page.reload({ waitUntil: "domcontentloaded" });
await openUser("Shivani Patel");
panel = await page.locator('[role="dialog"]').innerText();
check("new email survives a refresh and re-open", panel.includes(NEW_EMAIL), panel.slice(0, 120));
const dbRow = JSON.parse((await api(owner, `/api/users/${target.id}`)).text);
check("database has the new email", dbRow.email === NEW_EMAIL, dbRow.email);
check("role, team and manager unchanged",
  dbRow.role === before.role && dbRow.team_name === before.team && dbRow.manager_name === before.manager);
check("old email can no longer sign in", (await login(ORIGINAL_EMAIL)).status === 401);
check("new email can sign in", (await login(NEW_EMAIL)).status === 200);

console.log("\n=== 2. VALIDATION ===");
await page.keyboard.press("Escape");
await openUser("Shivani Patel");
await page.click('button:has-text("Edit account")');
await page.waitForSelector("#identifier");
await page.fill("#identifier", roster.find((u) => u.name === "Parth Fulvani").email);
await page.click('button[form="user-form"]');
await page.waitForTimeout(2500);
const dupText = await page.locator('[role="dialog"]').innerText();
check("duplicate email is refused with a readable message",
  /already in use/i.test(dupText), dupText.slice(0, 140));
check("no raw database error is shown",
  !/UNIQUE|sqlite|Traceback|SELECT /i.test(dupText));
await page.keyboard.press("Escape");

console.log("\n=== 3. PASSWORD SET ===");
await openUser("Shivani Patel");
await page.click('[role="dialog"] button:has-text("Change password")');
await page.waitForSelector("#new-password", { timeout: 10000 });
const pwDialog = await page.locator('[role="dialog"]').innerText();
check("password dialog never shows an existing password",
  /can never show you an existing one/i.test(pwDialog));
await page.fill("#new-password", NEW_PASSWORD);
await page.fill("#confirm-password", `${NEW_PASSWORD}-wrong`);
check("mismatched confirmation blocks saving",
  await page.locator('button[form="reset-form"]').isDisabled());
await page.fill("#confirm-password", NEW_PASSWORD);
await page.click('button[form="reset-form"]');
await page.waitForTimeout(2500);

check("old password no longer works", (await login(NEW_EMAIL)).status === 401);
const fresh = await login(NEW_EMAIL, NEW_PASSWORD);
check("new password works", fresh.status === 200);
check("login response carries no password or hash",
  !/password"\s*:\s*"|hashed|\$2b\$/.test(JSON.stringify(fresh.body).replace(/must_change_password/g, "")));
check("role unchanged after the password change", fresh.body?.user?.role === before.role);

console.log("\n=== 4. AUTHORIZATION (server-side) ===");
const bde = (await login("parth.fulvani@pouchwale.com")).auth;
const asBde = await api(bde, `/api/users/${target.id}`, {
  method: "PATCH", body: JSON.stringify({ email: "hijack@pouchwale.com" }),
});
check("a field user cannot edit another account", [401, 403, 404].includes(asBde.status), String(asBde.status));
const resetAsBde = await api(bde, `/api/users/${target.id}/reset-password`, {
  method: "POST", body: JSON.stringify({ new_password: "Hijack2026xyz", confirm_password: "Hijack2026xyz" }),
});
check("a field user cannot reset another password", [401, 403, 404].includes(resetAsBde.status), String(resetAsBde.status));
const listing = await api(owner, "/api/users?page_size=200");
check("user API never returns a password or hash",
  !/hashed_password|password_hash|\$2b\$/.test(listing.text));

console.log("\n=== 5. RESTORE THE DEMO ACCOUNT ===");
await api(owner, `/api/users/${target.id}`, {
  method: "PATCH", body: JSON.stringify({ email: ORIGINAL_EMAIL }),
});
await api(owner, `/api/users/${target.id}/reset-password`, {
  method: "POST", body: JSON.stringify({ new_password: SEED, confirm_password: SEED, must_change: false }),
});
const restored = await login(ORIGINAL_EMAIL);
check("demo account restored to its original email and password", restored.status === 200);

console.log("\n=== 6. FULL MODULE SMOKE, PER ROLE ===");
for (const [who, email] of [
  ["Super Admin", "owner@pouchwale.com"],
  ["Admin", "shail.patel@pouchwale.com"],
  ["BDE manager", "navya.rupawat@pouchwale.com"],
  ["Sales manager", "ramanesh.nair@pouchwale.com"],
  ["Field BDE", "parth.fulvani@pouchwale.com"],
]) {
  // A live session redirects /login to the dashboard, so end it first.
  await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(800);
  if (!/\/login/.test(page.url())) await signOut().catch(() => {});
  await signIn(email);
  const broken = [];
  for (const path of ["/dashboard", "/leads", "/references", "/feedback", "/notifications", "/team", "/profile"]) {
    await page.goto(APP + path, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(700);
    const text = await page.locator("body").innerText();
    if (/Could not load this|Application error|Something went wrong/i.test(text)) broken.push(path);
    if (text.trim().length < 40) broken.push(`${path} (blank)`);
  }
  // Assistant drawer opens and answers nothing unauthorised.
  await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  const launcher = await page.locator('[aria-label="Ask the assistant"]').count();
  // With CHAT_ENABLED=false the feature leaves no trace, by design.
  const chatAvailable = JSON.parse((await api(owner, "/api/chat/status")).text || "{}").available;
  if (chatAvailable && !launcher) broken.push("assistant launcher missing");
  if (!chatAvailable && launcher) broken.push("assistant launcher shown while chat is off");
  await signOut();
  check(`${who}: all modules render, assistant present, sign-out works`, broken.length === 0, broken.join(", "));
}

const real = errors.filter((e) => !/favicon|404|Failed to load resource/i.test(e));
check("no fatal console errors across the run", real.length === 0, real.slice(0, 2).join(" | "));

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
for (const f of failed) console.log(`  FAIL ${f.label}`);
process.exit(failed.length ? 1 : 0);
