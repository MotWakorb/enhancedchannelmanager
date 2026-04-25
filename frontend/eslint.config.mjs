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
      // eslint-plugin-react-hooks 7.1 added several React-Compiler-aware rules
      // to its `recommended` config. They flag hundreds of pre-existing
      // patterns (set-state-in-effect call sites, ref reads tied to layout
      // measurement, manual memoization that the compiler cannot preserve,
      // immutability assumptions, throw-in-render purity). Addressing them
      // is real refactor work, not in scope for the eslint 9 -> 10 bump
      // (bd-5x6n7). Disabled here per docs/frontend_lint.md "Rules of the
      // Road" #4 -- prefer config-level disables over scattering inline
      // disables. Follow-up bead: triage and re-enable individually.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      'react-hooks/immutability': 'off',
      'react-hooks/purity': 'off',
    },
  },
]);
