// eslint-config-next 16 ships native flat config, so no FlatCompat bridge is
// needed — routing it through @eslint/eslintrc throws on a circular plugin
// reference.
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  ...coreWebVitals,
  ...typescript,
  // out-static/ is the static export (PORTAL_STATIC_EXPORT=1): minified build
  // output, not source.
  { ignores: [".next/**", "out-static/**", "node_modules/**", "tests/e2e/**", "next-env.d.ts"] },
];

export default config;
