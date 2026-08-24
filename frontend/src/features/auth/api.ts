import { api } from '@/lib/api';
import type {
  AuthenticatedUser,
  LoginResult,
  MagicLinkDeviceApproved,
  MessageResponse,
  PasswordPolicy,
  RegisterResponse,
  SessionInfo,
  TokenResponse,
  TwoFactorEnableResponse,
  TwoFactorSetup,
} from '@/types/api';

/**
 * Auth endpoint bindings.
 *
 * A thin, typed layer over the HTTP client. Keeping the URLs here means a route
 * rename touches one file, and components never contain string paths - so a typo
 * is a compile error at the binding rather than a 404 at runtime.
 */
export const authApi = {
  register: (body: {
    email: string;
    password: string;
    full_name: string;
    organization_name?: string;
    invitation_token?: string;
  }) => api.post<RegisterResponse>('/auth/register', body),

  login: (body: { email: string; password: string; remember_me?: boolean }) =>
    api.post<LoginResult>('/auth/login', body),

  loginTwoFactor: (body: { challenge_id: string; code: string; remember_me?: boolean }) =>
    api.post<TokenResponse>('/auth/login/2fa', body),

  logout: (body?: { all_devices?: boolean }) =>
    api.post<MessageResponse>('/auth/logout', body ?? {}),

  me: () => api.get<AuthenticatedUser>('/auth/me'),

  verifyEmail: (token: string) => api.post<MessageResponse>('/auth/verify-email', { token }),

  resendVerification: (email: string) =>
    api.post<MessageResponse>('/auth/resend-verification', { email }),

  forgotPassword: (email: string) => api.post<MessageResponse>('/auth/forgot-password', { email }),

  resetPassword: (body: { email: string; code: string; new_password: string }) =>
    api.post<MessageResponse>('/auth/reset-password', body),

  changePassword: (body: { current_password: string; new_password: string }) =>
    api.post<MessageResponse>('/auth/change-password', body),

  requestMagicLink: (body: { email: string; redirect_path?: string }) =>
    api.post<MessageResponse>('/auth/magic-link', body),

  // Two shapes: tokens when this browser asked for the link, or an approval when an
  // app did. The client that requested it is the one that gets signed in.
  verifyMagicLink: (token: string) =>
    api.post<TokenResponse | MagicLinkDeviceApproved>('/auth/magic-link/verify', { token }),

  requestOtp: (email: string) => api.post<MessageResponse>('/auth/otp', { email }),

  verifyOtp: (body: { email: string; code: string }) =>
    api.post<TokenResponse>('/auth/otp/verify', body),

  passwordPolicy: () => api.get<PasswordPolicy>('/auth/password-policy'),

  // --- Two-factor ---
  beginTwoFactorSetup: () => api.post<TwoFactorSetup>('/auth/2fa/setup'),
  enableTwoFactor: (code: string) =>
    api.post<TwoFactorEnableResponse>('/auth/2fa/enable', { code }),
  disableTwoFactor: (password: string) =>
    api.post<MessageResponse>('/auth/2fa/disable', { password }),
  regenerateRecoveryCodes: (password: string) =>
    api.post<{ recovery_codes: string[] }>('/auth/2fa/recovery-codes', { password }),

  // --- Sessions ---
  listSessions: () => api.get<SessionInfo[]>('/auth/sessions'),
  revokeSession: (sessionId: string) => api.delete<MessageResponse>(`/auth/sessions/${sessionId}`),

  switchOrganization: (organizationId: string) =>
    api.post<TokenResponse>(`/auth/switch-organization/${organizationId}`),
};
