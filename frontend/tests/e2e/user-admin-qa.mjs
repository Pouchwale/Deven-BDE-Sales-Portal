/**
 * Set-a-password and permanent-delete, driven as a Super Admin.
 *
 * Writes, so it creates its own throwaway account and deletes it again. It
 * never touches a real person's password.
 */
import { chromium } from "playwright-core";

const APP = "http://localhost:3000";
const API = "http://localhost:8000";
const failures = [];
const check = (label, ok, detail = "") => {
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(label);
};

const login = await fetch(`${API}/api/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "owner@pouchwale.com", password: "ChangeMe@123" }),
}).then((r) => r.json());
const token = login.access_token;
const throwaway = `qa.temp.${Date.now()}@pouchwale.com`;
const created = await fetch(`${API}/api/users`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({
    name: "QA Throwaway", email: throwaway, password: "Temp@2026abc", role: "BDE",
  }),
}).then((r) => r.json());
console.log(`created throwaway ${created.id}`);

const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
await page.fill("#email", "owner@pouchwale.com");
await page.fill("#password", "ChangeMe@123");
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

/* ---------------------------------------- 1. generate a password */
console.log("\n1. Set password — generated");
await openMenu("QA Throwaway", throwaway);
await page.click('[role="menuitem"]:has-text("Reset password")');
await page.waitForSelector('[role="dialog"]', { timeout: 10000 });
const dialog = page.locator('[role="dialog"]');
check("the dialog offers to generate one",
  (await dialog.innerText()).includes("Generate one for me") ||
  (await dialog.locator("#new-password").getAttribute("placeholder"))?.includes("Generate"));
check("and explains it is shown once",
  (await dialog.innerText()).toLowerCase().includes("once"));
await page.click('button:has-text("Set password")');
await page.waitForSelector('[data-testid="new-password"]', { timeout: 15000 });
const generated = (await page.locator('[data-testid="new-password"]').innerText()).trim();
check("a password is shown after setting", generated.length >= 12, `got "${generated}"`);
check("no ambiguous characters", !/[0O1lI]/.test(generated), generated);
check("it warns the portal cannot show it again",
  (await dialog.innerText()).includes("one-way hashes"));
await page.click('button:has-text("Done")');

// It must actually work as a password.
const asUser = await fetch(`${API}/api/auth/login`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: throwaway, password: generated }),
});
check("the generated password signs in", asUser.status === 200, `status ${asUser.status}`);
check("and forces a change", (await asUser.json()).must_change_password === true);

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
await openMenu("QA Throwaway", throwaway);
await page.click('[role="menuitem"]:has-text("Delete permanently")');
await page.waitForSelector('[role="dialog"]', { timeout: 10000 });
await page.click('button:has-text("Delete for good")');
await page.waitForTimeout(2500);
const gone = await fetch(`${API}/api/users?page_size=200&include_inactive=true`, {
  headers: { Authorization: `Bearer ${token}` },
}).then((r) => r.json());
check("the account is gone entirely",
  !gone.items.some((u) => u.email === throwaway));

const real = errors.filter((e) => !/favicon|404|Failed to load resource/i.test(e));
check("no console errors", real.length === 0, real.slice(0, 2).join(" | "));

await browser.close();
console.log("\n" + (failures.length ? `FAILURES (${failures.length}): ${failures.join(", ")}` : "ALL CHECKS PASSED"));
process.exit(failures.length ? 1 : 0);
