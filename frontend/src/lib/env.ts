import { z } from 'zod';

/**
 * Validated build-time environment.
 *
 * Vite inlines `import.meta.env.VITE_*` at build time, so a missing or malformed
 * value silently becomes `undefined` and surfaces later as a request to
 * `undefined/api/v1/auth/login`. Parsing here fails the build (or the first
 * import in dev) with a message that names the variable.
 */
const schema = z.object({
  VITE_API_BASE_URL: z.string().url().default('http://localhost:8000'),
  VITE_API_V1_PREFIX: z.string().startsWith('/').default('/api/v1'),
  VITE_APP_NAME: z.string().min(1).default('Personal ERP'),
});

const parsed = schema.safeParse(import.meta.env);

if (!parsed.success) {
  const issues = parsed.error.issues.map((i) => `  ${i.path.join('.')}: ${i.message}`).join('\n');
  throw new Error(`Invalid frontend environment:\n${issues}`);
}

export const env = {
  apiBaseUrl: parsed.data.VITE_API_BASE_URL.replace(/\/$/, ''),
  apiPrefix: parsed.data.VITE_API_V1_PREFIX,
  appName: parsed.data.VITE_APP_NAME,
  isDev: import.meta.env.DEV,
} as const;

/** Absolute URL for a versioned API path. */
export function apiUrl(path: string): string {
  return `${env.apiBaseUrl}${env.apiPrefix}${path.startsWith('/') ? path : `/${path}`}`;
}
