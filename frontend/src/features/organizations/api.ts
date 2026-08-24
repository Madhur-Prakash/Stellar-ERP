import { api } from '@/lib/api';
import type {
  AuditEntry,
  CursorPage,
  Invitation,
  InvitationPreview,
  Member,
  MessageResponse,
  Organization,
  OrganizationListItem,
  PermissionCatalogue,
  Role,
  RoleDetail,
  UserProfile,
  UserStats,
} from '@/types/api';

/**
 * Organization, member, role, and audit bindings.
 *
 * Note that none of these URLs carry an organization id. The active
 * organization comes from the signed access token, so `/organizations/current`
 * always means "the one this session is operating in" - there is no id in the
 * URL for a client to tamper with, which is what makes cross-tenant access
 * structurally impossible rather than merely checked.
 */
export const organizationsApi = {
  // --- Organizations ---
  list: () => api.get<OrganizationListItem[]>('/organizations'),

  create: (body: { name: string; slug?: string; country?: string; currency?: string }) =>
    api.post<Organization>('/organizations', body),

  current: () => api.get<Organization>('/organizations/current'),

  update: (body: Partial<Organization>) => api.patch<Organization>('/organizations/current', body),

  remove: () => api.delete<MessageResponse>('/organizations/current'),

  leave: () => api.post<MessageResponse>('/organizations/current/leave'),

  // --- Members ---
  listMembers: () => api.get<Member[]>('/organizations/current/members'),

  updateMember: (memberId: string, body: { role_id?: string; job_title?: string }) =>
    api.patch<Member>(`/organizations/current/members/${memberId}`, body),

  suspendMember: (memberId: string) =>
    api.post<Member>(`/organizations/current/members/${memberId}/suspend`),

  reactivateMember: (memberId: string) =>
    api.post<Member>(`/organizations/current/members/${memberId}/reactivate`),

  removeMember: (memberId: string) =>
    api.delete<MessageResponse>(`/organizations/current/members/${memberId}`),

  // --- Invitations ---
  listInvitations: () => api.get<Invitation[]>('/organizations/current/invitations'),

  invite: (body: { email: string; role_id?: string; message?: string }) =>
    api.post<Invitation>('/organizations/current/invitations', body),

  resendInvitation: (invitationId: string) =>
    api.post<Invitation>(`/organizations/current/invitations/${invitationId}/resend`),

  revokeInvitation: (invitationId: string) =>
    api.delete<MessageResponse>(`/organizations/current/invitations/${invitationId}`),

  /** Unauthenticated - the recipient has not signed in yet. */
  previewInvitation: (token: string) => api.get<InvitationPreview>(`/invitations/${token}`),

  acceptInvitation: (token: string) => api.post<MessageResponse>('/invitations/accept', { token }),

  // --- Roles ---
  listRoles: () => api.get<Role[]>('/roles'),
  getRole: (roleId: string) => api.get<RoleDetail>(`/roles/${roleId}`),
  permissionCatalogue: () => api.get<PermissionCatalogue>('/roles/permissions'),

  createRole: (body: { name: string; description?: string; permissions: string[] }) =>
    api.post<RoleDetail>('/roles', body),

  updateRole: (
    roleId: string,
    body: { name?: string; description?: string; permissions?: string[]; is_default?: boolean },
  ) => api.patch<RoleDetail>(`/roles/${roleId}`, body),

  deleteRole: (roleId: string) => api.delete<MessageResponse>(`/roles/${roleId}`),

  // --- Audit ---
  listAudit: (params?: { cursor?: string; limit?: number; action?: string; severity?: string }) =>
    api.get<CursorPage<AuditEntry>>('/audit', { params }),

  auditActions: () => api.get<string[]>('/audit/actions'),
};

/** Profile bindings - scoped to the caller's own record. */
export const usersApi = {
  me: () => api.get<UserProfile>('/users/me'),
  updateProfile: (body: {
    full_name?: string;
    phone?: string;
    locale?: string;
    timezone?: string;
    theme?: 'light' | 'dark' | 'system';
  }) => api.patch<UserProfile>('/users/me', body),
  updatePreferences: (body: { theme?: 'light' | 'dark' | 'system'; locale?: string }) =>
    api.patch<UserProfile>('/users/me/preferences', body),
  stats: () => api.get<UserStats>('/users/me/stats'),
};
