// Build the portal as static files and copy them into the backend, which
// serves them (backend/app/web.py). Run after ANY frontend change, then commit
// backend/app/web together with the change:
//
//     npm run export:backend
//
// Why: on Render's free tier a separate frontend service kept going to sleep
// and Render refused to wake it (429 hibernate-rate-limited), so sign-in
// failed. One service - the backend - serving pages and /api stays awake.
//
// SOURCE_HASH records which frontend source the files were built from;
// backend/tests/test_web.py fails when the source has changed since, so a
// stale build cannot slip through.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = join(dirname(fileURLToPath(import.meta.url)), "..");
const target = join(frontend, "..", "backend", "app", "web");
const built = join(frontend, "out-static");

// Keep this list and the hashing in step with backend/tests/test_web.py.
const HASHED = ["src", "public", "next.config.ts", "package-lock.json", "postcss.config.mjs", "tsconfig.json"];

function files(path) {
  if (!existsSync(path)) return [];
  if (statSync(path).isFile()) return [path];
  return readdirSync(path).flatMap((name) => files(join(path, name)));
}

export function sourceHash() {
  const hash = createHash("sha256");
  const all = HASHED.flatMap((entry) => files(join(frontend, entry)))
    .map((path) => relative(frontend, path).split(sep).join("/"))
    .sort();
  for (const rel of all) {
    // Line endings differ between a Windows checkout and Linux; ignore them.
    const body = readFileSync(join(frontend, rel)).toString("latin1").replaceAll("\r\n", "\n");
    hash.update(rel + "\0" + body + "\0", "latin1");
  }
  return hash.digest("hex");
}

const result = spawnSync("npx", ["next", "build"], {
  cwd: frontend,
  stdio: "inherit",
  shell: process.platform === "win32",
  env: { ...process.env, PORTAL_STATIC_EXPORT: "1", NEXT_TELEMETRY_DISABLED: "1" },
});
if (result.status !== 0) {
  console.error("next build failed; backend/app/web left unchanged.");
  process.exit(result.status ?? 1);
}
if (!existsSync(join(built, "index.html"))) {
  console.error(`Expected ${built}/index.html after the build.`);
  process.exit(1);
}

rmSync(target, { recursive: true, force: true });
cpSync(built, target, { recursive: true });
writeFileSync(join(target, "SOURCE_HASH"), sourceHash() + "\n");
console.log(`Static portal copied to ${relative(join(frontend, ".."), target)}`);
