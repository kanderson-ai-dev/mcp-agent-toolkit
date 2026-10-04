import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // frontend/ is a separate package with its own eslint.config.js +
    // toolchain — lint it via `npm --prefix frontend run lint` (CI does).
    // Ignoring it here also stops eslint from loading that nested config,
    // whose plugins only exist under frontend/node_modules.
    ignores: [
      "dist/",
      "coverage/",
      "node_modules/",
      "data/",
      "docs/",
      "frontend/",
      "playwright-report/",
      "test-results/",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true, allowBoolean: true },
      ],
      "@typescript-eslint/no-non-null-assertion": "error",
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
  {
    files: ["**/*.js"],
    ...tseslint.configs.disableTypeChecked,
  },
);
