import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../models/organization.dart';
import '../../state/auth_controller.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/app_input.dart';
import '../../widgets/app_modal.dart';
import '../../widgets/app_select.dart';
import '../../widgets/data_table.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';

/// Members - who has access to this organization and what they can do.
class MembersScreen extends ConsumerStatefulWidget {
  const MembersScreen({super.key});

  @override
  ConsumerState<MembersScreen> createState() => _MembersScreenState();
}

class _MembersScreenState extends ConsumerState<MembersScreen> {
  final TextEditingController _inviteEmail = TextEditingController();
  String _inviteRoleId = '';
  String? _inviteError;
  bool _inviting = false;

  @override
  void dispose() {
    _inviteEmail.dispose();
    super.dispose();
  }

  void _invalidateAll() {
    ref.invalidate(membersProvider);
    ref.invalidate(invitationsProvider);
  }

  Future<void> _invite() async {
    final String email = _inviteEmail.text.trim();
    if (email.isEmpty) {
      setState(() => _inviteError = 'Enter an email address');
      return;
    }

    setState(() {
      _inviteError = null;
      _inviting = true;
    });

    try {
      final Invitation invitation = await ref
          .read(organizationsApiProvider)
          .invite(email: email, roleId: _inviteRoleId);
      _invalidateAll();
      if (!mounted) return;
      context.toastSuccess('Invitation sent to ${invitation.email}');
      _inviteEmail.clear();
    } catch (error) {
      if (mounted) {
        setState(() => _inviteError = 'Could not send the invitation');
        context.toastApiError(error, 'Could not send the invitation');
      }
    } finally {
      if (mounted) setState(() => _inviting = false);
    }
  }

  /// A member mutation that reports its own outcome.
  ///
  /// Suspend, reactivate, remove, and role change all follow the same shape, and writing
  /// four near-identical handlers is how the four end up disagreeing about which caches to
  /// invalidate.
  Future<void> _mutate(
    Future<void> Function() action, {
    required String success,
    String? description,
    required String failure,
  }) async {
    try {
      await action();
      _invalidateAll();
      if (mounted) context.toastSuccess(success, description: description);
    } catch (error) {
      if (mounted) context.toastApiError(error, failure);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AuthState auth = ref.watch(authControllerProvider);
    final AsyncValue<List<Member>> members = ref.watch(membersProvider);
    final List<Invitation> invitations = auth.can('member:read')
        ? (ref.watch(invitationsProvider).valueOrNull ?? const <Invitation>[])
        : const <Invitation>[];
    final List<Role> roles = auth.can('role:read')
        ? (ref.watch(rolesProvider).valueOrNull ?? const <Role>[])
        : const <Role>[];

    final List<Invitation> pending = invitations
        .where((Invitation i) => i.status == 'pending')
        .toList(growable: false);
    final List<Member> rows = members.valueOrNull ?? const <Member>[];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const PageHeader(
          title: 'Members',
          description:
              'Manage who has access to this organization and what they can do.',
        ),

        // ---- Invite ----
        if (auth.can('member:invite')) ...<Widget>[
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const CardHeader(
                  title: 'Invite someone',
                  description: 'They will get an email with a single-use link.',
                ),
                CardBody(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    spacing: 12,
                    children: <Widget>[
                      Expanded(
                        child: AppInput(
                          controller: _inviteEmail,
                          placeholder: 'colleague@company.com',
                          leftIcon: LucideIcons.mail,
                          error: _inviteError,
                          keyboardType: TextInputType.emailAddress,
                          onSubmitted: (_) => _invite(),
                        ),
                      ),
                      SizedBox(
                        width: 200,
                        child: AppSelect(
                          value: _inviteRoleId,
                          placeholder: 'Default role (Viewer)',
                          options: <SelectOption>[
                            for (final Role role in roles)
                              SelectOption(value: role.id, label: role.name),
                          ],
                          onChanged: (String next) =>
                              setState(() => _inviteRoleId = next),
                        ),
                      ),
                      AppButton(
                        onPressed: _invite,
                        loading: _inviting,
                        leftIcon: LucideIcons.userPlus,
                        label: 'Send invite',
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
        ],

        // ---- Pending invitations ----
        if (pending.isNotEmpty) ...<Widget>[
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                CardHeader(
                  title: 'Pending invitations',
                  description: '${pending.length} awaiting acceptance',
                ),
                CardBody(
                  child: Column(
                    children: <Widget>[
                      for (final Invitation invitation in pending)
                        Container(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          decoration: BoxDecoration(
                            border: invitation == pending.last
                                ? null
                                : Border(bottom: BorderSide(color: t.border)),
                          ),
                          child: Row(
                            spacing: 12,
                            children: <Widget>[
                              Container(
                                width: 32,
                                height: 32,
                                decoration: BoxDecoration(
                                  color: t.surfaceSunken,
                                  shape: BoxShape.circle,
                                ),
                                alignment: Alignment.center,
                                child: Icon(
                                  LucideIcons.mail,
                                  size: 16,
                                  color: t.contentMuted,
                                ),
                              ),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: <Widget>[
                                    Text(
                                      invitation.email,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        fontSize: 13,
                                        fontWeight: FontWeight.w500,
                                        color: t.content,
                                      ),
                                    ),
                                    Text(
                                      '${invitation.role.name} · invited '
                                      '${formatRelative(invitation.createdAt)}',
                                      style: TextStyle(
                                        fontSize: 11,
                                        color: t.contentMuted,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              AppBadge(
                                invitation.isExpired ? 'Expired' : 'Pending',
                                tone: invitation.isExpired
                                    ? BadgeTone.warning
                                    : BadgeTone.info,
                              ),
                              if (auth.can('member:invite')) ...<Widget>[
                                AppIconButton(
                                  icon: LucideIcons.send,
                                  tooltip:
                                      'Resend invitation to ${invitation.email}',
                                  size: 14,
                                  onPressed: () => _mutate(
                                    () => ref
                                        .read(organizationsApiProvider)
                                        .resendInvitation(invitation.id),
                                    success: 'Invitation resent',
                                    failure: 'Could not resend it',
                                  ),
                                ),
                                AppIconButton(
                                  icon: LucideIcons.x,
                                  tooltip:
                                      'Revoke invitation to ${invitation.email}',
                                  size: 14,
                                  onPressed: () => _mutate(
                                    () => ref
                                        .read(organizationsApiProvider)
                                        .revokeInvitation(invitation.id),
                                    success: 'Invitation revoked',
                                    failure: 'Could not revoke it',
                                  ),
                                ),
                              ],
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
        ],

        // ---- Members ----
        AppCard(
          child: Column(
            children: <Widget>[
              CardHeader(
                title: 'Team',
                description: members.valueOrNull == null
                    ? null
                    : '${rows.length} member${rows.length == 1 ? '' : 's'}',
              ),
              AppDataTable<Member>(
                rows: rows,
                rowKey: (Member row) => row.id,
                isLoading: members.isLoading,
                empty: const EmptyState(
                  icon: LucideIcons.users,
                  title: 'No members yet',
                  description:
                      'Invite colleagues to collaborate on your books.',
                ),
                columns: <AppColumn<Member>>[
                  AppColumn<Member>(
                    header: 'Member',
                    flex: 2,
                    cell: (Member row) {
                      final bool isSelf = row.user.id == auth.user?.id;
                      return Row(
                        spacing: 10,
                        children: <Widget>[
                          AppAvatar(
                            src: row.user.avatarUrl,
                            name: row.user.fullName,
                            initials: row.user.initials,
                            size: AvatarSize.sm,
                          ),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: <Widget>[
                                Text.rich(
                                  TextSpan(
                                    children: <InlineSpan>[
                                      TextSpan(
                                        text: row.user.fullName,
                                        style: const TextStyle(
                                          fontWeight: FontWeight.w500,
                                        ),
                                      ),
                                      if (isSelf)
                                        TextSpan(
                                          text: ' (you)',
                                          style: TextStyle(
                                            color: t.contentMuted,
                                          ),
                                        ),
                                    ],
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                                Text(
                                  row.user.email,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                    fontSize: 11,
                                    color: t.contentMuted,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      );
                    },
                  ),
                  AppColumn<Member>(
                    header: 'Role',
                    fixedWidth: 180,
                    cell: (Member row) =>
                        // The owner's role is fixed - the server rejects the change, so the
                        // control is not offered.
                        auth.can('member:update') && !row.isOwner
                        ? AppSelect(
                            value: row.role.id,
                            options: <SelectOption>[
                              for (final Role role
                                  in roles.isEmpty
                                      ? <Role>[
                                          Role(
                                            id: row.role.id,
                                            name: row.role.name,
                                            permissions: const <String>[],
                                            isSystem: row.role.isSystem,
                                            isDefault: false,
                                            memberCount: 0,
                                          ),
                                        ]
                                      : roles)
                                SelectOption(value: role.id, label: role.name),
                            ],
                            onChanged: (String next) => _mutate(
                              () => ref
                                  .read(organizationsApiProvider)
                                  .updateMember(row.id, roleId: next),
                              success: 'Role updated',
                              description: 'It takes effect immediately.',
                              failure: 'Could not change the role',
                            ),
                          )
                        : Text(
                            row.role.name,
                            style: TextStyle(color: t.contentSecondary),
                          ),
                  ),
                  AppColumn<Member>(
                    header: 'Status',
                    fixedWidth: 120,
                    cell: (Member row) => row.isOwner
                        ? const AppBadge('Owner', tone: BadgeTone.primary)
                        : row.isActive
                        ? const AppBadge(
                            'Active',
                            tone: BadgeTone.success,
                            dot: true,
                          )
                        : const AppBadge(
                            'Suspended',
                            tone: BadgeTone.warning,
                            dot: true,
                          ),
                  ),
                  AppColumn<Member>(
                    header: 'Last active',
                    hideOnNarrow: true,
                    fixedWidth: 140,
                    cell: (Member row) => Text(
                      row.lastActiveAt != null
                          ? formatRelative(row.lastActiveAt!)
                          : row.user.lastLoginAt != null
                          ? formatRelative(row.user.lastLoginAt!)
                          : 'Never',
                      style: TextStyle(fontSize: 12, color: t.contentMuted),
                    ),
                  ),
                  AppColumn<Member>(
                    header: '',
                    fixedWidth: 150,
                    cell: (Member row) {
                      final bool isSelf = row.user.id == auth.user?.id;
                      if (!auth.can('member:remove') || row.isOwner || isSelf) {
                        return Icon(
                          LucideIcons.moreHorizontal,
                          size: 16,
                          color: t.contentMuted.at(0.3),
                        );
                      }
                      return Row(
                        mainAxisAlignment: MainAxisAlignment.end,
                        mainAxisSize: MainAxisSize.min,
                        spacing: 4,
                        children: <Widget>[
                          AppButton(
                            onPressed: () => _mutate(
                              () => row.isActive
                                  ? ref
                                        .read(organizationsApiProvider)
                                        .suspendMember(row.id)
                                  : ref
                                        .read(organizationsApiProvider)
                                        .reactivateMember(row.id),
                              success: row.isActive
                                  ? 'Member suspended'
                                  : 'Member reactivated',
                              failure: 'Could not update the member',
                            ),
                            variant: AppButtonVariant.ghost,
                            size: AppButtonSize.sm,
                            label: row.isActive ? 'Suspend' : 'Reactivate',
                          ),
                          AppIconButton(
                            icon: LucideIcons.trash2,
                            tooltip: 'Remove ${row.user.fullName}',
                            size: 14,
                            colour: t.danger,
                            onPressed: () async {
                              final bool confirmed = await confirmAction(
                                context,
                                title: 'Remove ${row.user.fullName}?',
                                message:
                                    'They lose access to this organization. Their account is '
                                    'not deleted.',
                                confirmLabel: 'Remove',
                              );
                              if (!confirmed) return;
                              await _mutate(
                                () => ref
                                    .read(organizationsApiProvider)
                                    .removeMember(row.id),
                                success: 'Member removed',
                                failure: 'Could not remove the member',
                              );
                            },
                          ),
                        ],
                      );
                    },
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}
