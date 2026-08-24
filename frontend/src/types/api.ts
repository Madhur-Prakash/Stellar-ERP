/**
 * TypeScript mirrors of the backend response contracts.
 *
 * Hand-written for Stage 1 rather than generated. The surface is small enough
 * that a generator plus its codegen step is more machinery than it saves, and
 * hand-written types let the comments explain intent. Stage 9 introduces
 * generation from `/openapi.json` once the API surface is large enough that
 * drift becomes the bigger risk.
 *
 * Field names are `snake_case` because the API is `snake_case` end to end - one
 * name for each field everywhere, rather than a translation layer.
 */

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  role_name: string;
  role_slug: string;
  is_owner: boolean;
  /** How this organization's figures and dates are rendered. On the session payload
   *  because every screen needs them before its first paint. */
  currency: string;
  timezone: string;
  fiscal_year_start_month: number;
}

export interface AuthenticatedUser {
  id: string;
  email: string;
  full_name: string;
  avatar_url: string | null;
  initials: string;
  is_email_verified: boolean;
  is_two_factor_enabled: boolean;
  is_superuser: boolean;
  locale: string;
  timezone: string;
  theme: 'light' | 'dark' | 'system';
  last_login_at: string | null;
  active_organization: OrganizationSummary | null;
  organizations: OrganizationSummary[];
  /** Expanded permission slugs for the active organization. */
  permissions: string[];
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  expires_at: string;
  session_id: string;
  user: AuthenticatedUser;
  must_change_password: boolean;
}

/** Returned by `/auth/login` when a second factor is outstanding. */
export interface TwoFactorChallenge {
  challenge_id: string;
  two_factor_required: true;
  message: string;
}

export type LoginResult = TokenResponse | TwoFactorChallenge;

/** Narrows a login response without inspecting `challenge_id` at call sites. */
export function isTwoFactorChallenge(result: LoginResult): result is TwoFactorChallenge {
  return 'two_factor_required' in result && result.two_factor_required === true;
}

/**
 * Returned by `/auth/magic-link/verify` when the link belonged to an app.
 *
 * Nothing is signed in here: the client that *requested* the link is the one that
 * gets the session, so opening an app's link in a browser approves the app and leaves
 * the browser signed out.
 */
export interface MagicLinkDeviceApproved {
  device_approved: true;
  user_code: string;
  message: string;
}

export function isDeviceApproved(
  result: TokenResponse | MagicLinkDeviceApproved,
): result is MagicLinkDeviceApproved {
  return 'device_approved' in result && result.device_approved === true;
}

export interface RegisterResponse {
  user_id: string;
  email: string;
  email_verification_required: boolean;
  organization_id: string | null;
  message: string;
}

export interface MessageResponse {
  message: string;
  detail: string | null;
}

/** Mirrors the backend's `describe_policy()`. Served by `GET /auth/password-policy`
 *  so the UI's hints can never contradict what the server enforces. */
export interface PasswordPolicy {
  min_length: number;
  max_length: number;
  requires_uppercase: boolean;
  requires_lowercase: boolean;
  requires_special: boolean;
  requires_digit: boolean;
  /** The literal set of accepted special characters. */
  special_characters: string;
  rules: string[];
}

export interface TwoFactorSetup {
  secret: string;
  provisioning_uri: string;
  /** PNG `data:` URI - inline so the secret never becomes a fetchable URL. */
  qr_code: string;
}

export interface TwoFactorEnableResponse {
  enabled: boolean;
  /** Shown exactly once. */
  recovery_codes: string[];
}

export interface SessionInfo {
  id: string;
  ip_address: string | null;
  user_agent: string | null;
  device_label: string | null;
  device_type: string | null;
  login_method: 'password' | 'magic_link' | 'otp' | 'invitation' | 'impersonation';
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  is_current: boolean;
}

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------
export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  avatar_url: string | null;
  initials: string;
  phone: string | null;
  is_active: boolean;
  is_email_verified: boolean;
  is_two_factor_enabled: boolean;
  locale: string;
  timezone: string;
  theme: 'light' | 'dark' | 'system';
  last_login_at: string | null;
  created_at: string;
}

export interface UserStats {
  active_sessions: number;
  organizations: number;
  recovery_codes_remaining: number;
}

// ---------------------------------------------------------------------------
// Organizations
// ---------------------------------------------------------------------------
export type OrganizationPlan = 'free' | 'starter' | 'growth' | 'enterprise';

export interface Organization {
  id: string;
  name: string;
  slug: string;
  legal_name: string | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  logo_url: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string;
  currency: string;
  timezone: string;
  fiscal_year_start_month: number;
  gstin: string | null;
  pan: string | null;
  cin: string | null;
  plan: OrganizationPlan;
  is_active: boolean;
  onboarded_at: string | null;
  created_at: string;
}

export interface OrganizationListItem {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  plan: OrganizationPlan;
  role_name: string;
  is_owner: boolean;
  member_count: number;
}

export interface RoleSummary {
  id: string;
  name: string;
  slug: string;
  is_system: boolean;
}

export interface MemberUser {
  id: string;
  email: string;
  full_name: string;
  avatar_url: string | null;
  initials: string;
  is_email_verified: boolean;
  last_login_at: string | null;
}

export interface Member {
  id: string;
  user: MemberUser;
  role: RoleSummary;
  status: 'active' | 'suspended';
  is_owner: boolean;
  job_title: string | null;
  joined_at: string | null;
  last_active_at: string | null;
  created_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: RoleSummary;
  status: 'pending' | 'accepted' | 'revoked' | 'expired';
  message: string | null;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
  is_expired: boolean;
}

export interface InvitationPreview {
  organization_name: string;
  organization_logo_url: string | null;
  role_name: string;
  invited_by_name: string | null;
  email: string;
  expires_at: string;
  requires_registration: boolean;
}

// ---------------------------------------------------------------------------
// Roles & permissions
// ---------------------------------------------------------------------------
export interface Role {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  /** As stored - may contain wildcards such as `invoice:*`. */
  permissions: string[];
  is_system: boolean;
  is_default: boolean;
  member_count: number;
  created_at: string;
}

export interface RoleDetail extends Role {
  /** Wildcards resolved to the concrete permissions actually enforced. */
  effective_permissions: string[];
}

export interface PermissionInfo {
  slug: string;
  resource: string;
  action: string;
}

export interface PermissionGroup {
  key: string;
  label: string;
  description: string;
  permissions: PermissionInfo[];
}

export interface PermissionCatalogue {
  groups: PermissionGroup[];
  total: number;
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------
export type AuditSeverity = 'info' | 'warning' | 'critical';

export interface AuditActor {
  id: string | null;
  email: string | null;
  name: string | null;
}

export interface AuditEntry {
  id: string;
  action: string;
  severity: AuditSeverity;
  summary: string | null;
  actor: AuditActor;
  resource_type: string | null;
  resource_id: string | null;
  ip_address: string | null;
  request_id: string | null;
  /**
   * What changed, **in more than one shape** - which is why the value is `unknown`
   * rather than the `{ before, after }` pair it used to claim to be.
   *
   * Most writers go through the audit service's `diff()` and produce exactly that pair
   * per field. Others - document upload, re-extract, confirm-into-bill - use this column
   * as a flat snapshot instead: `{ status: 'uploaded', duplicate_of: null }`.
   *
   * Typing it as the pair was a lie the compiler could not catch, and it crashed the
   * audit page outright: `change.before` on a `null` value throws, so one uploaded
   * document made the whole page unrenderable. Audit rows are immutable and those
   * entries are already written, so the reader has to cope with both shapes forever -
   * `unknown` is what forces it to. Narrow with `asFieldDiff` in `AuditPage`.
   */
  changes: Record<string, unknown>;
  context: Record<string, unknown>;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Pagination envelopes
// ---------------------------------------------------------------------------
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface PageMeta {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
}

export interface Page<T> {
  items: T[];
  meta: PageMeta;
}
