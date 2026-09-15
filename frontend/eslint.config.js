import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      // `React` is imported for the classic JSX runtime and an unused binding
      // in a catch or a destructure is written as `_` by convention here.
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^(React|_)$',
        argsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^(_|err|error)$',
      }],
    },
  },
  {
    // Vitest runs with `globals: true` (see vite.config.js), so describe, it,
    // expect and friends are injected rather than imported.
    files: ['**/*.test.{js,jsx}', 'src/setupTests.js'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node, ...globals.vitest },
    },
  },
])
