import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/api_error.dart';
import '../../core/format.dart';
import '../../models/organization.dart';
import '../../models/page.dart';
import '../../state/auth_controller.dart';
import '../../state/providers.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_button.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';
import '../auth/auth_layout.dart';

/// Invitation acceptance.
///
/// Handles both recipients: someone who already has an account (accept directly) and someone
/// who does not (sent to registration with the token attached). The preview endpoint reports
/// which case applies, so the screen never asks the user to work it out.
class AcceptInviteScreen extends ConsumerStatefulWidget {
  const AcceptInviteScreen({super.key, this.token});

  final String? token;

  @override
  ConsumerState<AcceptInviteScreen> createState() => _AcceptInviteScreenState();
}

class _AcceptInviteScreenState extends ConsumerState<AcceptInviteScreen> {
  InvitationPreview? _preview;
  ApiError? _error;
  bool _loading = true;
  bool _accepting = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (widget.token == null || widget.token!.isEmpty) {
      setState(() => _loading = false);
      return;
    }
    try {
      final InvitationPreview preview = await ref
          .read(organizationsApiProvider)
          .previewInvitation(widget.token!);
      if (mounted) {
        setState(() {
          _preview = preview;
          _loading = false;
        });
      }
    } catch (error) {
      // An invalid token will not become valid on retry, so this is not retried.
      if (mounted) {
        setState(() {
          _error = ApiError.from(error);
          _loading = false;
        });
      }
    }
  }

  Future<void> _accept() async {
    setState(() => _accepting = true);
    try {
      final MessageResponse result = await ref
          .read(organizationsApiProvider)
          .acceptInvitation(widget.token!);
      // The membership changed, so permissions did too.
      await ref.read(authControllerProvider.notifier).refresh();
      if (!mounted) return;
      context.toastSuccess(result.message, description: result.detail);
      context.go('/');
    } catch (error) {
      if (mounted) {
        setState(() => _accepting = false);
        context.toastApiError(error, 'Could not accept the invitation');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AuthState auth = ref.watch(authControllerProvider);

    if (widget.token == null || widget.token!.isEmpty) {
      return AuthLayout(
        title: 'Invalid invitation link',
        subtitle: const Text('This link is missing its token.'),
        footer: AuthFooterPrompt(
          prompt: '',
          actionLabel: 'Go to sign in',
          onAction: () => context.go('/login'),
        ),
        child: Text(
          'Ask whoever invited you to send a new invitation.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 13, color: t.contentMuted),
        ),
      );
    }

    if (_loading || auth.isLoading) {
      return const AuthLayout(
        title: 'Checking your invitation…',
        child: Column(
          spacing: 12,
          children: <Widget>[
            Skeleton(height: 16),
            FractionallySizedBox(
              widthFactor: 0.66,
              child: Skeleton(height: 16),
            ),
            Skeleton(height: 36),
          ],
        ),
      );
    }

    if (_error != null || _preview == null) {
      return AuthLayout(
        title: 'This invitation is no longer valid',
        subtitle: Text(
          _error?.message ?? 'It may have expired or already been used.',
        ),
        footer: AuthFooterPrompt(
          prompt: '',
          actionLabel: 'Go to sign in',
          onAction: () => context.go('/login'),
        ),
        child: Text(
          'Invitations expire after 7 days. Ask for a new one.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 13, color: t.contentMuted),
        ),
      );
    }

    final InvitationPreview preview = _preview!;

    return AuthLayout(
      title: 'Join ${preview.organizationName}',
      subtitle: Text.rich(
        TextSpan(
          children: <InlineSpan>[
            TextSpan(text: '${preview.invitedByName ?? 'Someone'} invited '),
            TextSpan(
              text: preview.email,
              style: TextStyle(color: t.content, fontWeight: FontWeight.w600),
            ),
            const TextSpan(text: ' to join as '),
            TextSpan(
              text: preview.roleName,
              style: TextStyle(color: t.content, fontWeight: FontWeight.w600),
            ),
            const TextSpan(text: '.'),
          ],
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 20,
        children: <Widget>[
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: t.surfaceSunken.at(0.5),
              borderRadius: BorderRadius.circular(Radii.lg),
              border: Border.all(color: t.border),
            ),
            child: Row(
              spacing: 12,
              children: <Widget>[
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: t.primary.at(0.12),
                    borderRadius: BorderRadius.circular(Radii.lg),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    preview.organizationName
                        .substring(
                          0,
                          preview.organizationName.length >= 2 ? 2 : 1,
                        )
                        .toUpperCase(),
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w700,
                      color: t.primary,
                    ),
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        preview.organizationName,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          color: t.content,
                        ),
                      ),
                      Text(
                        'Role: ${preview.roleName} · expires '
                        '${formatDate(preview.expiresAt)}',
                        style: TextStyle(fontSize: 12, color: t.contentMuted),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),

          if (preview.requiresRegistration) ...<Widget>[
            Text(
              'You will need an account first. Registering through this link joins the '
              'organization automatically and verifies your email.',
              style: TextStyle(
                fontSize: 13,
                color: t.contentMuted,
                height: 1.6,
              ),
            ),
            AppButton(
              onPressed: () => context.go(
                '/register?invitation=${Uri.encodeComponent(widget.token!)}',
              ),
              fullWidth: true,
              size: AppButtonSize.lg,
              label: 'Create your account',
            ),
          ] else if (auth.isAuthenticated)
            AppButton(
              onPressed: _accept,
              loading: _accepting,
              fullWidth: true,
              size: AppButtonSize.lg,
              leftIcon: LucideIcons.check,
              label: 'Accept invitation',
            )
          else ...<Widget>[
            Text(
              'You already have an account. Sign in to accept this invitation.',
              style: TextStyle(
                fontSize: 13,
                color: t.contentMuted,
                height: 1.6,
              ),
            ),
            AppButton(
              onPressed: () => context.go(
                '/login?redirect=${Uri.encodeComponent('/accept-invite?token=${widget.token}')}',
              ),
              fullWidth: true,
              size: AppButtonSize.lg,
              leftIcon: LucideIcons.building2,
              label: 'Sign in to continue',
            ),
          ],
        ],
      ),
    );
  }
}
