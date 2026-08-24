import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/api_error.dart';
import '../../core/env.dart';
import '../../theme/app_theme.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_button.dart';

/// Route-level error boundary.
///
/// Shows the request id when the failure came from the API, because that is what makes a
/// user report actionable - it maps directly to the backend log lines for that exact
/// request. The stack trace is shown in a debug build only; in a release build it would
/// leak internals to no benefit.
class RouteErrorScreen extends StatelessWidget {
  const RouteErrorScreen({super.key, this.error});

  final Object? error;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final ApiError? apiError = error == null ? null : ApiError.from(error!);
    final bool isApi = apiError != null && apiError.code != 'unknown_error';

    return Scaffold(
      backgroundColor: t.canvas,
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  color: t.dangerBg,
                  borderRadius: BorderRadius.circular(Radii.xl),
                ),
                alignment: Alignment.center,
                child: Icon(LucideIcons.refreshCw, size: 20, color: t.danger),
              ),
              const SizedBox(height: 16),
              Text(
                'Something went wrong',
                style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.w600,
                  letterSpacing: -0.72,
                  color: t.content,
                ),
              ),
              const SizedBox(height: 8),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 440),
                child: Text(
                  isApi
                      ? apiError.message
                      : 'An unexpected error occurred while loading this screen.',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontSize: 13,
                    color: t.contentMuted,
                    height: 1.6,
                  ),
                ),
              ),
              if (apiError?.requestId != null) ...<Widget>[
                const SizedBox(height: 12),
                Text(
                  'Request ID: ${apiError!.requestId}',
                  style: monoStyle(fontSize: 11, color: t.contentMuted),
                ),
              ],
              const SizedBox(height: 24),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                spacing: 8,
                children: <Widget>[
                  AppButton(
                    onPressed: () => context.go('/'),
                    leftIcon: LucideIcons.refreshCw,
                    label: 'Back to dashboard',
                  ),
                ],
              ),
              if (Env.isDev && error != null) ...<Widget>[
                const SizedBox(height: 32),
                ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 672),
                  child: Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: t.surfaceSunken,
                      borderRadius: BorderRadius.circular(Radii.lg),
                    ),
                    child: Text(
                      '$error',
                      style: monoStyle(fontSize: 11, color: t.contentMuted),
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Shown where a screen's own data failed to load.
///
/// Inline rather than replacing the whole route, so the navigation and the rest of the
/// screen stay usable: one failed panel should not take the app down with it.
class InlineError extends StatelessWidget {
  const InlineError({super.key, required this.error, this.onRetry});

  final Object error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final ApiError normalised = ApiError.from(error);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 40),
      child: Column(
        children: <Widget>[
          Icon(LucideIcons.triangleAlert, size: 20, color: t.danger),
          const SizedBox(height: 12),
          Text(
            normalised.message,
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 13,
              color: t.contentSecondary,
              height: 1.6,
            ),
          ),
          if (normalised.requestId != null) ...<Widget>[
            const SizedBox(height: 8),
            Text(
              'Request ID: ${normalised.requestId}',
              style: monoStyle(fontSize: 11, color: t.contentMuted),
            ),
          ],
          if (onRetry != null) ...<Widget>[
            const SizedBox(height: 16),
            AppButton(
              onPressed: onRetry,
              variant: AppButtonVariant.secondary,
              size: AppButtonSize.sm,
              leftIcon: LucideIcons.refreshCw,
              label: 'Try again',
            ),
          ],
        ],
      ),
    );
  }
}
