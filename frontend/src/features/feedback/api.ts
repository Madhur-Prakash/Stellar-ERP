/**
 * Feedback and usage tracking.
 *
 * Two endpoints with deliberately different failure behaviour, and the difference
 * is the whole design:
 *
 * * `submit` is a **user action**. It can fail, and the user must be told, because
 *   somebody who typed three paragraphs and got a silent no-op will not type them
 *   again.
 * * `track` is **telemetry**. It must never surface anything to anybody. A product
 *   that showed an error toast about its own analytics would be worse than one with
 *   no analytics, so every failure is swallowed - see {@link track}.
 */
import { api } from '@/lib/api';

export type FeedbackKind = 'problem' | 'idea' | 'praise' | 'question';

export interface SubmitFeedback {
  kind: FeedbackKind;
  message: string;
  rating?: number;
  screen?: string;
  contact_email?: string;
}

/**
 * Actions the server will accept. Mirrors `KNOWN_ACTIONS` in
 * `app/modules/feedback/service.py`.
 *
 * Typed here so a typo is a compile error rather than an event silently dropped on
 * arrival - the server drops unknown actions by design, which is safe but invisible.
 */
export type UsageAction =
  | 'screen.dashboard'
  | 'screen.billing'
  | 'screen.accounts'
  | 'screen.accounting'
  | 'screen.sales'
  | 'screen.inventory'
  | 'screen.documents'
  | 'screen.analytics'
  | 'screen.trust'
  | 'screen.settings'
  | 'screen.verify'
  | 'attestation.enabled'
  | 'attestation.disabled'
  | 'seal.now'
  | 'proof.export'
  | 'proof.verified'
  | 'proof.rejected'
  | 'entry.posted'
  | 'invoice.posted'
  | 'bill.posted'
  | 'document.uploaded'
  | 'feedback.submitted'
  | 'user.registered'
  | 'organization.created';

/**
 * Context an event may carry.
 *
 * Note what is absent: no id, no amount, no name, no path. The server drops
 * anything else, and the type mirrors that so a call site cannot try. An analytics
 * table that could hold a customer's name would be inside the compliance boundary,
 * and the point of keeping it first-party is that it is not.
 */
export interface UsageContext {
  surface?: 'web' | 'desktop';
  network?: string;
  cadence?: string;
  outcome?: 'ok' | 'failed' | 'unknown';
  count?: number;
  verified?: boolean;
}

export const feedbackApi = {
  submit: (body: SubmitFeedback) => api.post<unknown>('/feedback', body),
};

/**
 * Record one usage event, and never let it matter.
 *
 * Deliberately not a mutation, not awaited by callers, and it resolves rather than
 * rejects on failure. Three reasons, all the same reason: an unauthenticated
 * caller gets a 401 here (the endpoint needs an organization to be useful), a
 * server with analytics switched off answers 200 with nothing recorded, and a user
 * offline gets a network error - and not one of those is worth a single pixel of
 * the user's attention.
 */
export function track(action: UsageAction, context: UsageContext = {}): void {
  void api
    .post('/feedback/track', { action, surface: 'web', context: { surface: 'web', ...context } })
    .catch(() => {
      // Swallowed on purpose. See above.
    });
}
