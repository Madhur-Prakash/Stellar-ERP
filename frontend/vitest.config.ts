/**
 * Vitest, for the one thing in the frontend that genuinely needs a test runner:
 * proving that the TypeScript canonical encoding agrees with the Python one.
 *
 * Not a general-purpose component-test setup. The web client's correctness is
 * covered by `tsc -b`, type-aware eslint, and the backend suite that owns every
 * business rule. What none of those can check is whether two independent
 * implementations of a hash function produce the same bytes - and if they ever
 * disagree, every proof this product has issued stops verifying. That is worth a
 * dependency.
 *
 * `node` environment rather than `jsdom`: the code under test uses Web Crypto and
 * `TextEncoder`, both of which Node has natively, and nothing under test touches
 * the DOM. A jsdom environment would be slower and would substitute a polyfilled
 * SubtleCrypto for the real one, which is the opposite of what this test wants.
 */
import path from 'node:path';

import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
