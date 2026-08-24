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
  VITE_APP_NAME: z.string().min(1).default('Stellar ERP'),

  // ---- Ledger 3 -----------------------------------------------------------
  // The verifier reads the Soroban contract itself, in the browser, so it needs
  // these at build time. It does not fetch them from our API for the *check* -
  // that would put us back in the trust path, which is the one thing the design
  // removes. The API's `/verify/network` exists as a convenience for the app's own
  // screens and as a fallback when a bundle names no endpoint.
  VITE_STELLAR_NETWORK: z.enum(['testnet', 'public']).default('testnet'),
  VITE_SOROBAN_CONTRACT_ID: z.string().default(''),
  VITE_SOROBAN_RPC_URL: z.string().default(''),

  // Blank disables error reporting entirely and nothing leaves the browser.
  VITE_SENTRY_DSN: z.string().default(''),
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

  stellarNetwork: parsed.data.VITE_STELLAR_NETWORK,
  sorobanContractId: parsed.data.VITE_SOROBAN_CONTRACT_ID || null,
  /** Blank means "use the public endpoint for the chosen network". */
  sorobanRpcUrl: parsed.data.VITE_SOROBAN_RPC_URL || null,
  sentryDsn: parsed.data.VITE_SENTRY_DSN || null,
} as const;

/** Absolute URL for a versioned API path. */
export function apiUrl(path: string): string {
  return `${env.apiBaseUrl}${env.apiPrefix}${path.startsWith('/') ? path : `/${path}`}`;
}
