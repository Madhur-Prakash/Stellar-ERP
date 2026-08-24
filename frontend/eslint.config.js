import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

/**
 * ESLint flat config.
 *
 * Type-aware linting (`recommendedTypeChecked`) is enabled deliberately. It is
 * slower, but it is what catches the class of bug TypeScript alone misses - a
 * floating promise whose rejection is never handled, an `await` on a
 * non-thenable. In an app where every mutation is async, those matter.
 */
export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage'] },

  js.configs.recommended,

  // Type-aware rules apply only to files inside a tsconfig project. Spreading
  // them globally makes ESLint try to type-check its own config file, which is
  // not in one, and it fails to load the rules at all.
  ...tseslint.configs.recommendedTypeChecked.map((config) => ({
    ...config,
    files: ['**/*.{ts,tsx}'],
  })),

  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // Unused args prefixed with `_` are intentional (event handlers, catch
      // bindings), so they are exempted rather than the rule being disabled.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],

      // An unhandled promise rejection is a silent failure. `void promise` is
      // the explicit opt-out, which is why it is allowed.
      '@typescript-eslint/no-floating-promises': ['error', { ignoreVoid: true }],
      '@typescript-eslint/no-misused-promises': [
        'error',
        // Passing an async function to onClick is idiomatic React and harmless.
        { checksVoidReturn: { attributes: false } },
      ],

      // Template literals silently stringify objects as "[object Object]".
      '@typescript-eslint/restrict-template-expressions': [
        'error',
        { allowNumber: true, allowBoolean: true, allowNullish: true },
      ],

      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
    },
  },

  {
    // Vite config runs in Node, not the browser.
    files: ['vite.config.ts'],
    languageOptions: { globals: globals.node },
  },

  {
    // Plain-JS config files: Node globals, and no TypeScript rules at all.
    files: ['**/*.js'],
    languageOptions: { globals: globals.node },
    ...tseslint.configs.disableTypeChecked,
  },
);
