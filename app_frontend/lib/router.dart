import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'features/accounting/accounting_screen.dart';
import 'features/analytics/analytics_screen.dart';
import 'features/auth/login_screen.dart';
import 'features/auth/password_screens.dart';
import 'features/auth/passwordless_screens.dart';
import 'features/auth/register_screen.dart';
import 'features/billing/accounts_screen.dart';
import 'features/billing/billing_screen.dart';
import 'features/dashboard/dashboard_screen.dart';
import 'features/documents/documents_screen.dart';
import 'features/errors/error_screens.dart';
import 'features/inventory/inventory_screen.dart';
import 'features/organizations/accept_invite_screen.dart';
import 'features/organizations/audit_screen.dart';
import 'features/organizations/members_screen.dart';
import 'features/organizations/roles_screen.dart';
import 'features/sales/sales_screen.dart';
import 'features/settings/settings_screen.dart';
import 'layout/app_shell.dart';
import 'state/auth_controller.dart';

/// Routing.
///
/// Code-based and in one file, like the web app's route tree: it is small enough that
/// having the auth boundary - which routes are public and which are guarded - reviewable
/// at a glance beats inferring it from a directory layout.
///
/// **Guards run in `redirect`, before a screen is built.** Deciding inside a widget
/// would render the page first and redirect after, briefly flashing content the user is
/// not entitled to. That matters more here than on the web: a desktop window has no
/// address bar to give the game away, so a flash of someone else's payables is the only
/// signal anything went wrong.
///
/// **`isLoading` is checked before anything else**, and that is the subtle part. During
/// the initial refresh we do not yet know whether there is a session, and redirecting on
/// `!isAuthenticated` would bounce a signed-in user to the sign-in screen on every
/// launch. The same applies to the permission guards: on a cold start `can()` answers
/// false for everything, and a guard that acted on that answer would send the user to
/// the dashboard every single time they reopened the app on a deep-linked screen.

/// Public routes, listed once so the guard does not restate them.
const Set<String> _publicPaths = <String>{
  '/login',
  '/register',
  '/forgot-password',
  '/reset-password',
  '/verify-email',
  '/magic-link',
  '/magic-link/verify',
  '/otp',
  '/accept-invite',
};

/// Sign-in screens an authenticated user should not see.
///
/// `/reset-password`, `/verify-email`, and `/accept-invite` are deliberately absent: a
/// signed-in user following an emailed link has a legitimate reason to be there, and
/// bouncing them to the dashboard would make the link look broken.
const Set<String> _signInPaths = <String>{
  '/login',
  '/register',
  '/forgot-password',
  '/magic-link',
  '/otp',
};

/// Which permission each guarded screen requires.
///
/// A table rather than a check per route, so the whole authorization surface of the
/// client is one thing to read. The server enforces every one of these regardless -
/// this only decides where to send someone who would be refused.
///
/// Billing is guarded on `journal:read` rather than a permission of its own: these
/// entries *are* journal entries, and inventing a parallel permission that grants the
/// same underlying capability would be security theatre.
const Map<String, String> _requiredPermissions = <String, String>{
  '/billing': 'journal:read',
  '/accounts': 'account:read',
  '/accounting': 'account:read',
  '/invoices': 'invoice:read',
  '/inventory': 'inventory:read',
  '/documents': 'document:read',
  '/analytics': 'report:read',
};

final Provider<GoRouter> routerProvider = Provider<GoRouter>((Ref ref) {
  // `refreshListenable` is how GoRouter is told to re-run its guards. Bumped on every
  // auth change so signing in, signing out, and a session expiring mid-use all
  // re-evaluate the current route rather than leaving a dead screen on display.
  final ValueNotifier<int> revision = ValueNotifier<int>(0);
  ref.listen<AuthState>(
    authControllerProvider,
    (AuthState? previous, AuthState next) => revision.value++,
  );
  ref.onDispose(revision.dispose);

  return GoRouter(
    initialLocation: '/',
    refreshListenable: revision,
    errorBuilder: (BuildContext context, GoRouterState state) =>
        RouteErrorScreen(error: state.error),
    redirect: (BuildContext context, GoRouterState state) {
      final AuthState auth = ref.read(authControllerProvider);
      final String path = state.matchedLocation;

      // Do nothing until the session restore settles - see the file docstring.
      if (auth.isLoading) return null;

      if (!auth.isAuthenticated) {
        if (_publicPaths.contains(path)) return null;
        // Preserve the intended destination so sign-in returns the user there rather
        // than dumping them on the dashboard.
        final String target = Uri.encodeComponent(state.uri.toString());
        return '/login?redirect=$target';
      }

      if (_signInPaths.contains(path)) return '/';

      final String? permission = _requiredPermissions[path];
      if (permission != null && !auth.can(permission)) return '/';

      return null;
    },
    routes: <RouteBase>[
      // -----------------------------------------------------------------------
      // Public
      // -----------------------------------------------------------------------
      GoRoute(
        path: '/login',
        builder: (BuildContext context, GoRouterState state) =>
            LoginScreen(redirectTo: state.uri.queryParameters['redirect']),
      ),
      GoRoute(
        path: '/register',
        builder: (BuildContext context, GoRouterState state) => RegisterScreen(
          invitationToken: state.uri.queryParameters['invitation'],
        ),
      ),
      GoRoute(
        path: '/forgot-password',
        builder: (_, _) => const ForgotPasswordScreen(),
      ),
      GoRoute(
        path: '/reset-password',
        builder: (BuildContext context, GoRouterState state) =>
            // `email` only - it prefills the form. The reset code is typed in,
            // never carried in the URL.
            ResetPasswordScreen(email: state.uri.queryParameters['email']),
      ),
      GoRoute(
        path: '/verify-email',
        builder: (BuildContext context, GoRouterState state) =>
            VerifyEmailScreen(token: state.uri.queryParameters['token']),
      ),
      GoRoute(path: '/magic-link', builder: (_, _) => const MagicLinkScreen()),
      GoRoute(
        // A separate path from the request screen so the emailed link cannot be
        // confused with the form, and so this one is never guarded.
        path: '/magic-link/verify',
        builder: (BuildContext context, GoRouterState state) =>
            MagicLinkVerifyScreen(token: state.uri.queryParameters['token']),
      ),
      GoRoute(path: '/otp', builder: (_, _) => const OtpScreen()),
      GoRoute(
        // Deliberately unguarded: the recipient may or may not have an account, and
        // the screen handles both.
        path: '/accept-invite',
        builder: (BuildContext context, GoRouterState state) =>
            AcceptInviteScreen(token: state.uri.queryParameters['token']),
      ),

      // -----------------------------------------------------------------------
      // Authenticated
      // -----------------------------------------------------------------------
      ShellRoute(
        builder: (BuildContext context, GoRouterState state, Widget child) =>
            AppShell(child: child),
        routes: <RouteBase>[
          GoRoute(path: '/', builder: (_, _) => const DashboardScreen()),
          GoRoute(path: '/billing', builder: (_, _) => const BillingScreen()),
          GoRoute(path: '/accounts', builder: (_, _) => const AccountsScreen()),
          GoRoute(
            path: '/accounting',
            // The selected tab lives in the URL, not in widget state, so it survives a
            // reload and can be linked to. Read loosely on purpose: an unknown value
            // falls back to the default tab rather than throwing, because a bad query
            // string should not be able to break a screen.
            builder: (BuildContext context, GoRouterState state) =>
                AccountingScreen(tab: state.uri.queryParameters['tab']),
          ),
          GoRoute(
            path: '/invoices',
            builder: (BuildContext context, GoRouterState state) =>
                SalesScreen(tab: state.uri.queryParameters['tab']),
          ),
          GoRoute(
            path: '/inventory',
            builder: (BuildContext context, GoRouterState state) =>
                InventoryScreen(tab: state.uri.queryParameters['tab']),
          ),
          GoRoute(
            path: '/documents',
            builder: (_, _) => const DocumentsScreen(),
          ),
          GoRoute(
            path: '/analytics',
            builder: (_, _) => const AnalyticsScreen(),
          ),
          GoRoute(
            path: '/assistant',
            builder: (_, _) => const StagePlaceholder(
              title: 'AI Assistant',
              description:
                  'Ask questions about your business in plain language.',
              stage: 6,
            ),
          ),
          GoRoute(path: '/members', builder: (_, _) => const MembersScreen()),
          GoRoute(path: '/roles', builder: (_, _) => const RolesScreen()),
          GoRoute(path: '/audit', builder: (_, _) => const AuditScreen()),
          GoRoute(
            path: '/settings',
            // `create=1` arrives from the sidebar switcher and the dashboard's
            // onboarding button, and asks the screen to scroll its create-
            // organization card into view. Read here rather than inside the
            // screen: every other query parameter in this router is passed down
            // as a constructor argument, and a `const SettingsScreen()` would not
            // rebuild when only the query string changed.
            builder: (BuildContext context, GoRouterState state) =>
                SettingsScreen(
                  scrollToCreateOrganization:
                      state.uri.queryParameters['create'] == '1',
                ),
          ),
        ],
      ),
    ],
  );
});
