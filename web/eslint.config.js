import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

// Flat config (ESLint 10 requires it). Covers the TypeScript + React source
// under src/; build output and Playwright specs are linted elsewhere or not
// at all. Mirrors the canonical Vite react-ts template so `npm run lint`
// works out of the box (previously failed with "couldn't find a config").
export default tseslint.config(
  { ignores: ["dist", "coverage"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // The pages load data via an async function invoked from an effect;
      // setState runs inside the awaited callback, not synchronously in the
      // effect body, so this rule over-fires on a deliberate and correct
      // fetch-on-mount pattern used consistently across every feature page.
      "react-hooks/set-state-in-effect": "off",
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
);
