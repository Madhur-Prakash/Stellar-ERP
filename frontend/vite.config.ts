/// <reference types="node" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import fs from 'node:fs';
import path from 'node:path';

/*
 * `VITE_*` values live in the repo-root `.env`, alongside the backend's, because a
 * deployment configures one file rather than two - and because three of them
 * (network, contract id, RPC) have to agree with the backend's or the browser reads
 * a different chain than the server writes to.
 *
 * Vite's default `envDir` is this directory, which does not contain a `.env`, so
 * every value silently fell back to its Zod default. The Docker build stage copies
 * only `frontend/`, so the repo root genuinely is not there - hence the existence
 * check rather than a bare `'..'`, which Vite would treat as an empty env. In that
 * build the values arrive as build ARGs instead; see the Dockerfile.
 *
 * Only the `VITE_` prefix is exposed to client code, so pointing Vite at the file
 * that also holds `SECRET_KEY` does not inline it. That prefix is the whole reason
 * the convention exists.
 */
const repoRoot = path.resolve(__dirname, '..');
const envDir = fs.existsSync(path.join(repoRoot, '.env')) ? repoRoot : __dirname;

export default defineConfig({
  plugins: [react(), tailwindcss()],

  envDir,

  resolve: {
    // `@/` -> src/. Keeps imports stable when files move, and makes the
    // dependency direction visible at a glance.
    alias: { '@': path.resolve(__dirname, './src') },
  },

  server: {
    host: true, // bind 0.0.0.0 so the container port mapping works
    port: 5173,
    strictPort: true,
    watch: {
      // Docker on Windows/macOS does not propagate inotify events into the
      // container, so file changes are missed without polling.
      usePolling: process.env.VITE_DOCKER === 'true',
    },
  },

  build: {
    outDir: 'dist',
    /*
     * `hidden` emits the maps but omits the `//# sourceMappingURL` comment, so a
     * browser never asks for them and an error tracker can still be given them at
     * release time.
     *
     * It used to be `true` with a comment saying they were not served publicly.
     * That was true only because the deleted edge configuration returned 404 for
     * `*.map`; when the edge went, nothing replaced the rule and the maps beside
     * `dist/assets` became fetchable. The production image now deletes them as
     * well - see the Dockerfile. Two layers, because the comment was load-bearing
     * once and turned out not to be.
     */
    sourcemap: 'hidden',
    // Recharts alone is ~360 kB; the ceiling is raised so the warning flags a
    // real regression rather than firing on a known, deliberate cost.
    chunkSizeWarningLimit: 700,

    rollupOptions: {
      output: {
        /**
         * Split vendor code by library so an app-code change does not invalidate
         * the whole vendor bundle in users' caches.
         *
         * A function rather than the object form: the object form matches only
         * exact specifiers, so `react` never captured `react/jsx-runtime` or
         * `scheduler` and emitted a 0.04 kB chunk while React itself stayed in
         * the main bundle. Matching on the resolved module path catches every
         * sub-path of a package.
         */
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;

          /*
           * The Stellar SDK gets its own chunk, and this entry is load-bearing.
           *
           * It is imported *only* through `await import()` in
           * `features/trust/chain.ts`, so that the accounting, sales, inventory and
           * analytics screens do not make every user download a blockchain library
           * to open the billing page.
           *
           * That intent is defeated by falling through to `vendor` below:
           * `manualChunks` overrides Rollup's dynamic-import splitting, so merging
           * the SDK into a chunk the app loads eagerly makes it eager too - which is
           * exactly what happened, and it put 1.2 MB in front of every page load.
           * A chunk of its own is reachable only from the dynamic import, so it stays
           * async.
           *
           * Matching the scope rather than the package name catches
           * `@stellar/stellar-base`, which is where most of the weight actually is.
           */
          if (id.includes('@stellar/')) return 'stellar';

          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react';
          if (id.includes('@tanstack/react-router') || id.includes('@tanstack/router-core'))
            return 'router';
          if (id.includes('@tanstack/react-query') || id.includes('@tanstack/query-core'))
            return 'query';
          // Recharts reaches d3 through `victory-vendor`, which re-exports the d3-*
          // packages. All three have to land in the same chunk: with only recharts and
          // d3-* here, `victory-vendor` fell through to `vendor`, and vendor importing
          // d3 while charts imported vendor made the two chunks mutually dependent -
          // Rollup warns, and a browser has no order in which it can load them.
          if (
            id.includes('recharts') ||
            id.includes('victory-vendor') ||
            /[\\/]node_modules[\\/]d3-/.test(id)
          )
            return 'charts';
          if (id.includes('zod') || id.includes('react-hook-form')) return 'forms';

          return 'vendor';
        },
      },
    },
  },
});
