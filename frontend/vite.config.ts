/// <reference types="node" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

export default defineConfig({
  plugins: [react(), tailwindcss()],

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
    sourcemap: true, // uploaded to Sentry in Stage 10, not served publicly
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
