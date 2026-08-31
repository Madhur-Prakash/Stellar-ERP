import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Mail, MoreHorizontal, Send, Trash2, UserPlus, Users, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { PageHeader } from '@/components/layout/AppShell';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAuth } from '@/features/auth/AuthProvider';
import { organizationsApi } from '@/features/organizations/api';
import { ApiError } from '@/lib/api';
import { formatRelative } from '@/lib/format';

export function MembersPage() {
  const { can, user } = useAuth();
  const queryClient = useQueryClient();
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRoleId, setInviteRoleId] = useState('');
  const [inviteError, setInviteError] = useState<string>();

  const { data: members, isLoading } = useQuery({
    queryKey: ['members'],
    queryFn: organizationsApi.listMembers,
  });

  const { data: invitations } = useQuery({
    queryKey: ['invitations'],
    queryFn: organizationsApi.listInvitations,
    enabled: can('member:read'),
  });

  const { data: roles } = useQuery({
    queryKey: ['roles'],
    queryFn: organizationsApi.listRoles,
    enabled: can('role:read'),
  });

  function invalidateAll() {
    void queryClient.invalidateQueries({ queryKey: ['members'] });
    void queryClient.invalidateQueries({ queryKey: ['invitations'] });
  }

  const invite = useMutation({
    mutationFn: () =>
      organizationsApi.invite({
        email: inviteEmail.trim(),
        ...(inviteRoleId ? { role_id: inviteRoleId } : {}),
      }),
    onSuccess: (invitation) => {
      toast.success(`Invitation sent to ${invitation.email}`);
      setInviteEmail('');
      setInviteError(undefined);
      invalidateAll();
    },
    onError: (error) => {
      setInviteError(error instanceof ApiError ? error.message : 'Could not send the invitation');
    },
  });

  const suspend = useMutation({
    mutationFn: organizationsApi.suspendMember,
    onSuccess: () => {
      toast.success('Member suspended');
      invalidateAll();
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed'),
  });

  const reactivate = useMutation({
    mutationFn: organizationsApi.reactivateMember,
    onSuccess: () => {
      toast.success('Member reactivated');
      invalidateAll();
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed'),
  });

  const remove = useMutation({
    mutationFn: organizationsApi.removeMember,
    onSuccess: () => {
      toast.success('Member removed');
      invalidateAll();
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed'),
  });

  const changeRole = useMutation({
    mutationFn: ({ memberId, roleId }: { memberId: string; roleId: string }) =>
      organizationsApi.updateMember(memberId, { role_id: roleId }),
    onSuccess: () => {
      toast.success('Role updated', { description: 'It takes effect immediately.' });
      invalidateAll();
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed'),
  });

  const revokeInvite = useMutation({
    mutationFn: organizationsApi.revokeInvitation,
    onSuccess: () => {
      toast.success('Invitation revoked');
      invalidateAll();
    },
  });

  const resendInvite = useMutation({
    mutationFn: organizationsApi.resendInvitation,
    onSuccess: () => toast.success('Invitation resent'),
  });

  const pendingInvitations = (invitations ?? []).filter((i) => i.status === 'pending');

  return (
    <div>
      <PageHeader
        title="Members"
        description="Manage who has access to this organization and what they can do."
      />

      {/* ---- Invite ---- */}
      {can('member:invite') && (
        <Card className="mb-4">
          <CardHeader
            title="Invite someone"
            description="They will get an email with a single-use link."
          />
          <CardBody>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!inviteEmail.trim()) {
                  setInviteError('Enter an email address');
                  return;
                }
                invite.mutate();
              }}
              className="flex flex-wrap items-start gap-3"
            >
              <div className="min-w-[240px] flex-1">
                <Input
                  placeholder="colleague@company.com"
                  type="email"
                  leftIcon={<Mail />}
                  value={inviteEmail}
                  onChange={(event) => setInviteEmail(event.target.value)}
                  error={inviteError}
                  aria-label="Email address to invite"
                />
              </div>

              <select
                value={inviteRoleId}
                onChange={(event) => setInviteRoleId(event.target.value)}
                aria-label="Role for the invited member"
                className="border-border bg-surface text-content focus:border-primary focus:ring-ring/25 h-9 rounded-md border px-3 text-sm focus:ring-2 focus:outline-none"
              >
                <option value="">Default role (Viewer)</option>
                {(roles ?? []).map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                  </option>
                ))}
              </select>

              <Button
                type="submit"
                loading={invite.isPending}
                leftIcon={<UserPlus className="h-4 w-4" />}
              >
                Send invite
              </Button>
            </form>
          </CardBody>
        </Card>
      )}

      {/* ---- Pending invitations ---- */}
      {pendingInvitations.length > 0 && (
        <Card className="mb-4">
          <CardHeader
            title="Pending invitations"
            description={`${pendingInvitations.length} awaiting acceptance`}
          />
          <CardBody className="divide-border divide-y">
            {pendingInvitations.map((invitation) => (
              <div key={invitation.id} className="flex items-center gap-3 py-3 first:pt-0">
                <div
                  className="bg-surface-sunken text-content-muted flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                  aria-hidden
                >
                  <Mail className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-content truncate text-[13px] font-medium">
                    {invitation.email}
                  </p>
                  <p className="text-content-muted text-[11px]">
                    {invitation.role.name} · invited {formatRelative(invitation.created_at)}
                  </p>
                </div>
                {invitation.is_expired ? (
                  <Badge tone="warning">Expired</Badge>
                ) : (
                  <Badge tone="info">Pending</Badge>
                )}
                {can('member:invite') && (
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Resend invitation"
                      aria-label={`Resend invitation to ${invitation.email}`}
                      loading={resendInvite.isPending}
                      onClick={() => resendInvite.mutate(invitation.id)}
                    >
                      <Send className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Revoke invitation"
                      aria-label={`Revoke invitation to ${invitation.email}`}
                      onClick={() => revokeInvite.mutate(invitation.id)}
                    >
                      <X className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      {/* ---- Members ---- */}
      <Card>
        <CardHeader
          title="Team"
          description={members ? `${members.length} member${members.length === 1 ? '' : 's'}` : ''}
        />
        <CardBody className="p-0">
          {isLoading ? (
            <div className="space-y-3 p-5">
              {Array.from({ length: 3 }).map((_, index) => (
                <div key={index} className="flex items-center gap-3">
                  <Skeleton className="h-8 w-8 rounded-full" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-3.5 w-40" />
                    <Skeleton className="h-2.5 w-56" />
                  </div>
                </div>
              ))}
            </div>
          ) : !members || members.length === 0 ? (
            <EmptyState
              icon={Users}
              title="No members yet"
              description="Invite colleagues to collaborate on your books."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead className="border-border bg-surface-sunken/50 border-y">
                  <tr className="text-content-muted text-[11px] font-semibold tracking-wide uppercase">
                    <th className="px-3 py-2.5 whitespace-nowrap sm:px-5">Member</th>
                    <th className="px-3 py-2.5 whitespace-nowrap sm:px-5">Role</th>
                    <th className="px-3 py-2.5 whitespace-nowrap sm:px-5">Status</th>
                    <th className="px-3 py-2.5 whitespace-nowrap sm:px-5">Last active</th>
                    <th className="px-3 py-2.5 sm:px-5" />
                  </tr>
                </thead>
                <tbody className="divide-border divide-y">
                  {members.map((member) => {
                    const isSelf = member.user.id === user?.id;
                    return (
                      <tr key={member.id} className="hover:bg-surface-hover/50">
                        <td className="px-3 py-3 sm:px-5">
                          <div className="flex items-center gap-2.5">
                            <Avatar
                              src={member.user.avatar_url}
                              name={member.user.full_name}
                              initials={member.user.initials}
                              size="sm"
                            />
                            <div className="min-w-0">
                              <p className="text-content truncate font-medium">
                                {member.user.full_name}
                                {isSelf && (
                                  <span className="text-content-muted ml-1.5 font-normal">
                                    (you)
                                  </span>
                                )}
                              </p>
                              <p className="text-content-muted truncate text-[11px]">
                                {member.user.email}
                              </p>
                            </div>
                          </div>
                        </td>

                        <td className="px-3 py-3 sm:px-5">
                          {/* The owner's role is fixed - the server rejects the
                              change, so the control is not offered. */}
                          {can('member:update') && !member.is_owner ? (
                            <select
                              value={member.role.id}
                              onChange={(event) =>
                                changeRole.mutate({
                                  memberId: member.id,
                                  roleId: event.target.value,
                                })
                              }
                              aria-label={`Role for ${member.user.full_name}`}
                              className="border-border bg-surface text-content h-7 rounded-md border px-2 text-[12px]"
                            >
                              {(roles ?? [member.role]).map((role) => (
                                <option key={role.id} value={role.id}>
                                  {role.name}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <span className="text-content-secondary">{member.role.name}</span>
                          )}
                        </td>

                        <td className="px-3 py-3 sm:px-5">
                          {member.is_owner ? (
                            <Badge tone="primary">Owner</Badge>
                          ) : member.status === 'active' ? (
                            <Badge tone="success" dot>
                              Active
                            </Badge>
                          ) : (
                            <Badge tone="warning" dot>
                              Suspended
                            </Badge>
                          )}
                        </td>

                        <td className="text-content-muted px-3 py-3 text-[12px] whitespace-nowrap sm:px-5">
                          {member.last_active_at
                            ? formatRelative(member.last_active_at)
                            : member.user.last_login_at
                              ? formatRelative(member.user.last_login_at)
                              : 'Never'}
                        </td>

                        <td className="px-3 py-3 text-right sm:px-5">
                          {can('member:remove') && !member.is_owner && !isSelf ? (
                            <div className="flex justify-end gap-1">
                              {member.status === 'active' ? (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => suspend.mutate(member.id)}
                                >
                                  Suspend
                                </Button>
                              ) : (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => reactivate.mutate(member.id)}
                                >
                                  Reactivate
                                </Button>
                              )}
                              <Button
                                variant="ghost"
                                size="icon"
                                title={`Remove ${member.user.full_name}`}
                                aria-label={`Remove ${member.user.full_name}`}
                                onClick={() => {
                                  // A native confirm rather than a custom modal.
                                  // Stage 1 has one destructive action; a dialog
                                  // system belongs with the rest of the UI kit.
                                  if (
                                    window.confirm(
                                      `Remove ${member.user.full_name} from this organization? Their account is not deleted.`,
                                    )
                                  ) {
                                    remove.mutate(member.id);
                                  }
                                }}
                              >
                                <Trash2 className="text-danger h-3.5 w-3.5" />
                              </Button>
                            </div>
                          ) : (
                            <MoreHorizontal
                              className="text-content-muted ml-auto h-4 w-4 opacity-30"
                              aria-hidden
                            />
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
