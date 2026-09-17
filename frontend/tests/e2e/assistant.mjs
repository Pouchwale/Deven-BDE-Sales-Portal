/**
 * The assistant drawer, in real Chrome.
 *
 *   node tests/e2e/assistant.mjs
 *
 * Assumes both servers are up. What it checks depends on how the backend is
 * configured, which it asks `/chat/status` rather than assuming:
 *
 *   CHAT_ENABLED=false   the feature leaves no visual trace
 *   no API key           the launcher shows and the drawer explains why
 *   fully configured     the whole journey, question through answer
 *
 * A *placeholder* key is the most useful third run: it exercises the failure
 * path, which is the part that has to stay free of stack traces.
 */
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

import { APP, BROWSER_CHANNEL, pageApi, requireSeedPassword } from "./support/session.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const SHOTS = process.env.E2E_SCREENSHOT_DIR ?? join(HERE, "screenshots");

const EMAIL = process.env.E2E_EMAIL ?? "parth.fulvani@pouchwale.com";
const PASSWORD = requireSeedPassword();

let passed = 0;
let failed = 0;

function check(name, condition, detail = "") {
  if (condition) {
    passed += 1;
    console.log(`  ok   ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

/** Print the tally and stop. Some configurations finish early. */
async function finish(browser, note) {
  console.log(`\n  (${note})`);
  console.log(`\n${passed} passed, ${failed} failed`);
  await browser.close();
  process.exit(failed === 0 ? 0 : 1);
}

async function main() {
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });

  const browser = await chromium.launch({
    channel: BROWSER_CHANNEL,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  try {
    // ------------------------------------------------------------ sign in
    await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
    await page.fill('input[type="email"]', EMAIL);
    await page.fill('input[type="password"]', PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 20_000 });
    check("signed in", true);

    // ----------------------------------------------------------- launcher
    const launcher = page.getByRole("button", { name: "Ask the assistant" });

    // Three states, and the server decides which one applies, so ask it
    // rather than making the runner know how the backend is configured.
    //
    //   available=false          -> nothing renders at all
    //   available=true, no key   -> launcher + setup state, no composer
    //   enabled=true             -> the full journey below
    const status = (await pageApi(page, "/api/chat/status")).body ?? {};

    if (!status.available) {
      await page.waitForTimeout(1_000);
      check("no launcher when the feature is switched off", (await launcher.count()) === 0);
      check(
        "nothing chat-shaped on the page",
        !(await page.locator('[aria-label="Portal assistant"]').count()),
      );
      return finish(browser, "CHAT_ENABLED is false — off-mode checks only");
    }

    if (!status.enabled) {
      // Switched on, no key. The launcher must still be there, and the panel
      // must explain itself rather than offering an input that can only 503.
      await launcher.waitFor({ state: "visible", timeout: 10_000 });
      check("launcher visible while unconfigured", await launcher.isVisible());

      await launcher.click();
      const panel = page.getByRole("dialog", { name: "Portal assistant" });
      await panel.waitFor({ state: "visible", timeout: 5_000 });
      await page.waitForTimeout(600);
      const panelText = await panel.innerText();

      check("setup state shown", /Not set up yet/i.test(panelText), panelText.slice(0, 120));
      check(
        "no composer that could only 503",
        (await panel.getByRole("textbox", { name: "Message the assistant" }).count()) === 0,
      );

      // `setup` is admin-only, and never carries the key.
      const isAdmin = status.setup !== null;
      check(
        isAdmin ? "admin is told which variable is missing" : "non-admin sees no server config",
        isAdmin
          ? /GROQ_API_KEY/.test(panelText)
          : !/GROQ_API_KEY/.test(panelText) && !panelText.includes(".env"),
        panelText.slice(0, 200),
      );
      check("no key material anywhere in the DOM", !/gsk_/i.test(await page.content()));
      check(
        // The provider name is server configuration too - an admin gets it,
        // a BDE gets "ask an administrator" and nothing else.
        isAdmin ? "provider named, never the key" : "provider not named to a non-admin",
        isAdmin ? /Groq/.test(panelText) : !/Groq/.test(panelText),
        panelText.slice(0, 160),
      );

      await page.screenshot({ path: join(SHOTS, "assistant-unconfigured.png") });
      return finish(browser, "no GROQ_API_KEY — setup-state checks only");
    }

    await launcher.waitFor({ state: "visible", timeout: 10_000 });
    check("launcher renders when the feature is on", await launcher.isVisible());

    // ------------------------------------------------------------- drawer
    await launcher.click();
    const drawer = page.getByRole("dialog", { name: "Portal assistant" });
    await drawer.waitFor({ state: "visible", timeout: 5_000 });
    check("drawer opens", await drawer.isVisible());

    const suggestions = drawer.locator("button", { hasText: /overdue|pending|feedback/i });
    check("role-aware suggestions render", (await suggestions.count()) > 0);

    // A BDE must not be offered a leadership prompt.
    const drawerText = await drawer.innerText();
    check(
      "no leadership prompt offered to a BDE",
      !/how is my team doing/i.test(drawerText),
      drawerText.slice(0, 160),
    );

    // The panel slides in with an opacity ramp; shooting the moment it is
    // "visible" catches it half-transparent.
    await page.waitForTimeout(500);
    await page.screenshot({ path: join(SHOTS, "assistant-empty.png") });

    // ------------------------------------------------------------ sending
    const composer = drawer.getByRole("textbox", { name: "Message the assistant" });
    await composer.fill("What's overdue?");
    await composer.press("Enter");

    check("the question appears in the transcript", await drawer.getByText("What's overdue?").isVisible());

    // Either an answer or a clean failure - both are acceptable here. What is
    // not acceptable is a stack trace reaching the panel.
    await page.waitForTimeout(9_000);
    const after = await drawer.innerText();
    await page.screenshot({ path: join(SHOTS, "assistant-answered.png") });

    // Everything the panel shows after the question, minus the standing
    // footer. Comparing total length would be wrong: the empty state carries
    // three suggestion buttons and is longer than most answers.
    const reply = after
      .split("What's overdue?")
      .slice(1)
      .join("")
      .replace(/Answers come from your own data[\s\S]*$/, "")
      .trim();

    check("a reply rendered", reply.length > 0, JSON.stringify(after.slice(-200)));
    check("the turn finished", !/Thinking/i.test(reply), reply.slice(0, 120));
    for (const tell of ["Traceback", "anthropic.", "sqlalchemy", "app/services", "<class"]) {
      check(`no ${tell} in the panel`, !after.includes(tell));
    }

    // ------------------------------------------------------------ closing
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    check("Escape closes the drawer", !(await drawer.isVisible().catch(() => false)));

    // ------------------------------------------------- survives navigation
    //
    // Clicking the nav is a click OUTSIDE the drawer, and tap-outside-to-close
    // is deliberate, so the panel shuts. What must survive is the CONVERSATION:
    // reopening it on the new page has to show the same thread, not a blank
    // one. Asserting the panel stayed open would be asserting the older
    // behaviour that tap-outside-to-close replaced.
    await launcher.click();
    await drawer.waitFor({ state: "visible", timeout: 5_000 });
    await page.getByRole("link", { name: "Assigned Leads" }).first().click();
    await page.waitForURL(/\/leads/, { timeout: 15_000 });
    await page.waitForTimeout(600);

    check(
      "clicking away closes the drawer",
      !(await drawer.isVisible().catch(() => false)),
    );

    await launcher.click();
    await drawer.waitFor({ state: "visible", timeout: 5_000 });
    await page.waitForTimeout(600);
    const stillThere = await drawer.getByText("What's overdue?").isVisible().catch(() => false);
    check("and the conversation is still there on the next page", stillThere);
  } finally {
    await browser.close();
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
