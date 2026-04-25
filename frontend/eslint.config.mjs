// @ts-check
import eslint from '@eslint/js';
import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

export default defineConfig([
  { ignores: ['dist'] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: {
      'react-refresh': reactRefresh,
    },
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      // eslint-plugin-react-hooks 7.1 added several React-Compiler-aware
      // rules to its `recommended` config. They flag hundreds of
      // pre-existing patterns. Triaged in bd-me2lt; the four below stay off
      // per docs/frontend_lint.md "Rules of the Road" #4 (config-level
      // disable preferred over scattering inline disables) -- the patterns
      // they flag are idiomatic in this codebase and re-enabling would
      // require broad refactors with marginal benefit. `react-hooks/purity`
      // had only one violation (NotificationCenter.tsx Date.now() in a
      // useMemo) which was fixed in bd-f5l03, so it is back on.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      'react-hooks/immutability': 'off',
    },
  },
]);
