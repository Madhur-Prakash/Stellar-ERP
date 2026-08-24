import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios';

import { env } from '@/lib/env';

/**
 * The HTTP client, and the token lifecycle around it.
 *
 * **Where the access token lives: in memory only.** Not `localStorage`, not
 * `sessionStorage`, not a readable cookie. Any XSS on the page can read those,
 * and a stolen token is valid until it expires. A module-scoped variable dies
 * with the tab, which is the point.
 *
 * That raises the obvious question - how does a page reload stay signed in? The
 * refresh token, which the server sets as an `HttpOnly; Secure; SameSite=Strict`
 * cookie that JavaScript cannot read at all. On boot the app calls
 * `/auth/refresh` once; the browser attaches the cookie, and a fresh access
 * token comes back. So the long-lived credential is never reachable from JS, and
 * the short-lived one never outlives the tab.
 *
 * **Refresh is single-flight.** When a token expires, every in-flight request
 * 401s at once. Naively each would trigger its own refresh - and because the
 * server *rotates* refresh tokens and treats reuse as a breach, the second
 * refresh would present an already-rotated token and get the whole session
 * revoked. So the first 401 starts a refresh, the rest await that same promise,
 * and all of them retry afterwards.
 */

// ---------------------------------------------------------------------------
// In-memory token store
// ---------------------------------------------------------------------------
let accessToken: string | null = null;
let onSessionExpired: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Register the callback that tears down client state when the session dies. */
export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

// ---------------------------------------------------------------------------
// Error shape
// ---------------------------------------------------------------------------
/** The backend's error envelope (see `app/core/exceptions.py`). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
}

/**
 * A normalised API failure.
 *
 * Every call site gets the same shape - machine-readable `code`, a displayable
 * `message`, and `fieldErrors` ready to hand to react-hook-form - instead of
 * each one having to unwrap `error.response.data.error.details.fields`.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly requestId: string | undefined;

  constructor(
    message: string,
    options: {
      code?: string;
      status?: number;
      details?: Record<string, unknown>;
      requestId?: string;
    } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = options.code ?? 'unknown_error';
    this.status = options.status ?? 0;
    this.details = options.details ?? {};
    this.requestId = options.requestId;
  }

  /** Per-field messages from a 422, keyed by field name. */
  get fieldErrors(): Record<string, string> {
    const fields = this.details['fields'];
    if (fields && typeof fields === 'object') {
      return fields as Record<string, string>;
    }
    // The password policy returns a list of reasons rather than a field map.
    const password = this.details['password'];
    if (Array.isArray(password)) {
      return { password: password.join('. ') };
    }
    return {};
  }

  get isValidation(): boolean {
    return this.status === 422;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }

  /** True for conditions a retry might resolve - offline, timeout, 5xx. */
  get isRetryable(): boolean {
    return this.status === 0 || this.status >= 500;
  }

  /**
   * How long to wait before retrying, in seconds, or `undefined` if unknown.
   *
   * The API sends this two ways - a `Retry-After` header and
   * `details.retry_after_seconds` - and this reads the body, because the header is only
   * legible cross-origin when the server remembers to list it in `expose_headers`.
   * The body always survives.
   */
  get retryAfterSeconds(): number | undefined {
    const raw = this.details['retry_after_seconds'];
    const seconds = typeof raw === 'number' ? raw : Number(raw);
    return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : undefined;
  }

  /**
   * A displayable "too many requests" message, with the wait when the server gave one.
   *
   * Exists because the server's own message is `"Too many requests. Slow down."` - correct
   * but unhelpful, since the one thing a user needs is *how long*. A rate limit with no
   * stated wait is indistinguishable from the app being broken, so people retry
   * immediately, which is exactly what keeps the bucket empty.
   */
  get rateLimitMessage(): string {
    const seconds = this.retryAfterSeconds;
    if (seconds === undefined) {
      return 'Too many attempts. Please wait a moment and try again.';
    }
    if (seconds < 60) {
      return `Too many attempts. Please try again in ${seconds} second${seconds === 1 ? '' : 's'}.`;
    }
    const minutes = Math.ceil(seconds / 60);
    return `Too many attempts. Please try again in ${minutes} minute${minutes === 1 ? '' : 's'}.`;
  }
}

/**
 * Replace a `Blob` error body with the parsed envelope, in place.
 *
 * **A `responseType: 'blob'` request gets a Blob body on failure too.** axios applies
 * the response type it was asked for regardless of status, so a 410 from
 * `/documents/{id}/file` arrives as a Blob containing the JSON envelope rather than as
 * the envelope itself. `toApiError` then finds no `body.error`, falls through to its
 * last branch, and the user is shown axios's own `"Request failed with status code
 * 410"` instead of "The stored file is missing." The server said the right thing and
 * the client threw it away.
 *
 * Every export in the app goes through `api.download`, which is also `responseType:
 * 'blob'` - so this one function is the difference between a real message and a status
 * code for file previews and every report download alike.
 *
 * Async, and therefore here rather than inside `toApiError`: reading a Blob is a
 * promise, and the response interceptor is the only `await`-capable point both paths
 * pass through. A body that is not JSON (an HTML error page from a proxy) is left
 * exactly as it was, so the network-error branches still behave.
 */
async function unwrapBlobError(error: AxiosError): Promise<void> {
  const response = error.response;
  const data: unknown = response?.data;
  if (!response || !(data instanceof Blob) || typeof data.text !== 'function') return;

  try {
    const parsed: unknown = JSON.parse(await data.text());
    if (parsed !== null && typeof parsed === 'object' && 'error' in parsed) {
      response.data = parsed;
    }
  } catch {
    // Not JSON. The generic branches in `toApiError` are the correct answer then.
  }
}

/**
 * A readable message for a response that carried no error envelope.
 *
 * The status is always named, because this class of failure is a deployment problem - a
 * misrouted request, a hostname missing from the allow-list - and the number is the first
 * thing anyone diagnosing it will ask for.
 *
 * A short plain-text body is appended when there is one. `Invalid host header` is eleven
 * characters that say exactly what is wrong, and swallowing it in favour of a generic
 * sentence would be throwing away the answer. Markup and JSON are not appended: a proxy's
 * error page is kilobytes of HTML, and JSON that got this far is some other service's
 * shape - readable to a developer, noise to everyone else.
 *
 * Kept worded in step with `app_frontend/lib/core/api_error.dart`, so one deployment fault
 * does not read as two different problems depending on which client is in hand.
 */
function withoutEnvelope(status: number, body: unknown): string {
  const base =
    status === 400
      ? 'The server rejected that request'
      : status === 401
        ? 'Your session has expired. Sign in again'
        : status === 403
          ? 'You do not have permission to do that'
          : status === 404
            ? 'That address does not exist on this server'
            : status === 408 || status === 504
              ? 'The server took too long to respond'
              : status === 413
                ? 'That upload is too large'
                : status === 429
                  ? 'Too many requests. Wait a moment and try again'
                  : status >= 500
                    ? 'The server ran into a problem. Try again in a moment'
                    : 'The request failed';

  const text = typeof body === 'string' ? body.trim() : '';
  const usable = text !== '' && text.length <= 200 && !/^[<[{]/.test(text) ? `: ${text}` : '';

  return `${base}${usable} (HTTP ${status}).`;
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;

  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<ApiErrorBody>;
    const body = axiosError.response?.data;

    if (body?.error) {
      return new ApiError(body.error.message, {
        code: body.error.code,
        status: axiosError.response?.status ?? 0,
        details: body.error.details ?? {},
        ...(body.error.request_id !== undefined ? { requestId: body.error.request_id } : {}),
      });
    }

    // No envelope: the request never reached the app (network down, CORS
    // rejection, proxy error). Say so plainly rather than showing "undefined".
    if (!axiosError.response) {
      return new ApiError(
        axiosError.code === 'ECONNABORTED'
          ? 'The request timed out. Check your connection and try again.'
          : 'Could not reach the server. Check your connection and try again.',
        { code: 'network_error', status: 0 },
      );
    }

    // A response, but not one of ours. Something between the browser and the API
    // answered: a proxy, a load balancer, the host guard in front of the router. Never
    // `axiosError.message` here - "Request failed with status code 400" tells a user
    // nothing they can act on, and its Dio equivalent in the mobile client was four
    // paragraphs of developer prose ending in a link to MDN.
    return new ApiError(withoutEnvelope(axiosError.response.status, axiosError.response.data), {
      code: 'http_error',
      status: axiosError.response.status,
    });
  }

  return new ApiError(error instanceof Error ? error.message : 'Something went wrong');
}

// ---------------------------------------------------------------------------
// Instance
// ---------------------------------------------------------------------------
export const http: AxiosInstance = axios.create({
  baseURL: `${env.apiBaseUrl}${env.apiPrefix}`,
  timeout: 30_000,
  // Required for the HttpOnly refresh cookie to be sent cross-origin. Paired
  // with an explicit CORS allow-list server-side - a wildcard origin is
  // forbidden by browsers alongside credentials.
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

// ---------------------------------------------------------------------------
// Single-flight refresh
// ---------------------------------------------------------------------------
/** Marks a request that has already been retried, so a loop is impossible. */
interface RetryableConfig extends InternalAxiosRequestConfig {
  _retried?: boolean;
}

let refreshPromise: Promise<string> | null = null;

/** Endpoints that must never trigger a refresh-and-retry. */
const NO_REFRESH_PATHS = [
  '/auth/login',
  '/auth/refresh',
  '/auth/register',
  '/auth/logout',
  '/auth/otp/verify',
  '/auth/magic-link/verify',
];

async function refreshAccessToken(): Promise<string> {
  // A bare axios call, not `http`: going through the instance would re-enter
  // this interceptor and attach the dead access token.
  const response = await axios.post<{ access_token: string }>(
    `${env.apiBaseUrl}${env.apiPrefix}/auth/refresh`,
    {},
    { withCredentials: true, timeout: 15_000 },
  );

  const token = response.data.access_token;
  setAccessToken(token);
  return token;
}

http.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (!axios.isAxiosError(error)) {
      return Promise.reject(toApiError(error));
    }

    // Before anything reads the body: a blob response carries its error envelope as a
    // Blob, and every branch below (including the retry's) ends at `toApiError`.
    await unwrapBlobError(error);

    if (!error.config) {
      return Promise.reject(toApiError(error));
    }

    const config = error.config as RetryableConfig;
    const status = error.response?.status;
    const url = config.url ?? '';

    const shouldAttemptRefresh =
      status === 401 && !config._retried && !NO_REFRESH_PATHS.some((path) => url.includes(path));

    if (!shouldAttemptRefresh) {
      // A 401 that survived a refresh is not stale - the brand-new token was
      // rejected too, which means revoked or a bumped token epoch. Retrying
      // cannot fix it, and leaving the session in place leaves the open page
      // firing requests that will every one of them 401. End it, so the guard
      // sends the user to sign in.
      //
      // Deliberately not applied to the paths that never refresh: a 401 from
      // `/auth/login` is a wrong password, and signing the user out for
      // mistyping one would be absurd. Those never carry `_retried`.
      if (status === 401 && config._retried) {
        setAccessToken(null);
        onSessionExpired?.();
      }
      return Promise.reject(toApiError(error));
    }

    config._retried = true;

    try {
      // Concurrent 401s share one refresh - see the module docstring on why
      // parallel refreshes would get the session revoked.
      refreshPromise ??= refreshAccessToken().finally(() => {
        refreshPromise = null;
      });

      const token = await refreshPromise;
      config.headers.Authorization = `Bearer ${token}`;
      return http.request(config);
    } catch {
      // The refresh token is gone, expired, or was revoked. This is a real
      // sign-out, not a transient failure.
      setAccessToken(null);
      onSessionExpired?.();
      return Promise.reject(toApiError(error));
    }
  },
);

// ---------------------------------------------------------------------------
// Typed helpers
// ---------------------------------------------------------------------------

/**
 * File types the save dialog can filter by, keyed by extension.
 *
 * Only what this app actually hands to `api.download`. An extension that is missing here
 * costs nothing but the filter - the file still saves - so this needs extending only to make
 * a new export type show up as its own entry in the dialog's type list.
 */
const MIME_BY_EXTENSION: Record<string, string | undefined> = {
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.pdf': 'application/pdf',
  '.csv': 'text/csv',
};

export const api = {
  async get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const { data } = await http.get<T>(url, config);
    return data;
  },
  async post<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const { data } = await http.post<T>(url, body, config);
    return data;
  },
  async patch<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const { data } = await http.patch<T>(url, body, config);
    return data;
  },
  async put<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const { data } = await http.put<T>(url, body, config);
    return data;
  },
  /**
   * Fetch a file and save it, letting the user pick where when the browser allows it.
   *
   * **Not a plain `<a href>`.** The export routes are guarded like every other endpoint, and
   * a bare link carries no `Authorization` header, so the browser would navigate to a 401.
   * The bytes come through the same axios instance as everything else - interceptors,
   * refresh-on-401 and all - and are handed over afterwards.
   *
   * Two ways to hand them over:
   *
   * 1. **`showSaveFilePicker`**, the operating system's own save dialog, so the file lands
   *    where the user wants it. Chromium-only today (Chrome, Edge, Opera), and secure
   *    contexts only, so it is absent over plain http.
   * 2. **An object URL and a synthetic click**, when the above is unavailable. This obeys the
   *    browser's own "ask where to save each file" setting, so a user who wants to be asked
   *    still is - it just cannot be forced from here. Firefox and Safari implement no
   *    save-picker API at all, so there is nothing better to fall back to.
   *
   * **The dialog opens before the request, and exactly one of the two paths ever runs.** Both
   * of those are deliberate, and each fixes a bug:
   *
   * - Opening it first keeps the *user activation* the picker requires. That activation is
   *   spent by an `await`, so asking after fetching the report threw `SecurityError` on any
   *   export slow enough to outlast it - and then fell back, downloading to the default
   *   folder after the user had been shown a dialog. It also means cancelling costs no
   *   request at all.
   * - Once a handle exists the user has committed to a destination, so a later failure is
   *   reported rather than retried through path 2. Falling back at that point wrote the
   *   chosen file *and* a second copy to the default folder: two files per click.
   *
   * A cancelled dialog is not an error. It throws `AbortError`, which is swallowed rather
   * than surfaced: the user changed their mind, and a toast saying so would be noise.
   */
  async download(url: string, filename: string, config?: AxiosRequestConfig): Promise<void> {
    const picker = (
      window as unknown as {
        showSaveFilePicker?: (options: {
          suggestedName?: string;
          types?: { description: string; accept: Record<string, string[]> }[];
        }) => Promise<{
          createWritable: () => Promise<{
            write: (data: Blob) => Promise<void>;
            close: () => Promise<void>;
          }>;
        }>;
      }
    ).showSaveFilePicker;

    // Taken from the name rather than the response's content type, because the dialog has to
    // be built before the response exists. `accept` is keyed *by* MIME type, so an unknown
    // extension means no filter at all - an empty key is an invalid argument, not a missing
    // one, and the picker rejects the whole call over it.
    const extension = filename.slice(filename.lastIndexOf('.')).toLowerCase();
    const mime = MIME_BY_EXTENSION[extension];

    let handle: {
      createWritable: () => Promise<{
        write: (data: Blob) => Promise<void>;
        close: () => Promise<void>;
      }>;
    } | null = null;
    if (typeof picker === 'function') {
      try {
        handle = await picker.call(window, {
          suggestedName: filename,
          types: mime
            ? [
                {
                  description: `${extension.slice(1).toUpperCase()} file`,
                  accept: { [mime]: [extension] },
                },
              ]
            : undefined,
        });
      } catch (error) {
        // Cancelling is the expected way out of a save dialog, not a failure.
        if (error instanceof DOMException && error.name === 'AbortError') return;
        // Anything else - no user activation left, a blocked or non-secure context - means
        // no destination was chosen, so path 2 is still the right way to deliver the file.
        handle = null;
      }
    }

    const response = await http.get<Blob>(url, { ...config, responseType: 'blob' });
    const blob = response.data;

    if (handle) {
      // `write` then `close`, both on the stream itself - not via `getWriter()`, which locks
      // the stream and makes the close throw. Closing is what commits the bytes: a writable
      // left open leaves a zero-length file on disk. A throw here reaches the caller, which
      // reports it; it does not silently download a second copy somewhere else.
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return;
    }

    const href = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.href = href;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      // A blob URL pins its data for the life of the document otherwise, and a few exports
      // of a large report add up.
      URL.revokeObjectURL(href);
    }
  },

  async delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const { data } = await http.delete<T>(url, config);
    return data;
  },
};

/**
 * Restore a session on app boot.
 *
 * Exchanges the HttpOnly refresh cookie for an access token. Returns `false`
 * when there is no valid cookie, which simply means "not signed in" - the
 * normal first-visit path, not an error.
 */
export async function bootstrapSession(): Promise<boolean> {
  try {
    // Through the same single-flight promise the interceptor uses, not a direct call.
    //
    // This used to call `refreshAccessToken()` straight, which sidesteps exactly the
    // guard this module's docstring exists to explain - and React's StrictMode
    // double-invokes the effect that calls this, so every mount in development fired two
    // refreshes on one cookie. Two outcomes, both bad: they race and the session ends up
    // duplicated, or the second arrives after the first has rotated, looks like a replayed
    // token, and the whole lineage is revoked - a logout nobody asked for.
    refreshPromise ??= refreshAccessToken().finally(() => {
      refreshPromise = null;
    });
    await refreshPromise;
    return true;
  } catch {
    setAccessToken(null);
    return false;
  }
}
