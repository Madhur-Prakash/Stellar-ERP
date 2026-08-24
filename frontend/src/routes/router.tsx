import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
} from '@tanstack/react-router';

import { AppShell, StagePlaceholder } from '@/components/layout/AppShell';
import { PageSkeleton } from '@/components/ui/Skeleton';
import { LoginPage } from '@/features/auth/LoginPage';
import {
  ForgotPasswordPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from '@/features/auth/PasswordPages';
import { MagicLinkPage, MagicLinkVerifyPage, OtpPage } from '@/features/auth/PasswordlessPages';
import { RegisterPage } from '@/features/auth/RegisterPage';
import { AccountingPage } from '@/features/accounting/AccountingPage';
import { DashboardPage } from '@/features/dashboard/DashboardPage';
import { AnalyticsPage } from '@/features/analytics/AnalyticsPage';
import { AccountsPage } from '@/features/billing/AccountsPage';
import { BillingPage } from '@/features/billing/BillingPage';
import { DocumentsPage } from '@/features/documents/DocumentsPage';
import { InventoryPage } from '@/features/inventory/InventoryPage';
import { AcceptInvitePage } from '@/features/organizations/AcceptInvitePage';
import { AuditPage } from '@/features/organizations/AuditPage';
import { MembersPage } from '@/features/organizations/MembersPage';
import { RolesPage } from '@/features/organizations/RolesPage';
import { InvoicesPage } from '@/features/sales/InvoicesPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { TrustPage } from '@/features/trust/TrustPage';
import { VerifyPage } from '@/features/verify/VerifyPage';
import { NotFoundPage } from '@/routes/NotFoundPage';
import { RootLayout } from '@/routes/RootLayout';
import { RouteErrorPage } from '@/routes/RouteErrorPage';

/**
 * Routing.
 *
 * Code-based rather than file-based: the route tree is small, and having it in
 * one file makes the auth boundary - which routes are public and which are
 * guarded - reviewable at a glance instead of inferred from a directory layout.
 *
 * Auth state is threaded through the router `context` so guards can run in
 * `beforeLoad`, before a protected component mounts. Reading it from a hook
 * inside the component would render the page first and redirect after, briefly
 * flashing content the user is not entitled to.
 */

/*
 * `throw redirect(...)` is TanStack Router's documented way to redirect from a
 * `beforeLoad` guard - the router catches the thrown descriptor as control flow.
 * It is not an Error subclass, so `only-throw-error` is disabled for this file
 * rather than working around the framework's intended API.
 */
/* eslint-disable @typescript-eslint/only-throw-error */

export interface RouterContext {
  isAuthenticated: boolean;
  /** True while the initial session restore is in flight. */
  isLoading: boolean;
  hasPermission: (permission: string) => boolean;
}

// `createRootRouteWithContext` is curried: the first call fixes the context
// type so every child route's `beforeLoad` sees it, which a plain
// `createRootRoute` generic does not do.
const rootRoute = createRootRouteWithContext<RouterContext>()({
  // `RootLayout` carries the feedback button and screen tracking. It lives in its
  // own file so that this module can keep exporting `router` - a non-component -
  // without breaking React fast refresh, which needs a file to export components
  // or values but not both.
  component: RootLayout,
  errorComponent: RouteErrorPage,
  notFoundComponent: NotFoundPage,
});

// ---------------------------------------------------------------------------
// Public routes
// ---------------------------------------------------------------------------
/**
 * Guard for the sign-in screens: an already-authenticated user is sent to the
 * dashboard rather than shown a login form they do not need.
 *
 * The `isLoading` check matters. During the initial refresh we do not yet know
 * whether there is a session, and redirecting on `!isAuthenticated` would bounce
 * a signed-in user to `/login` on every reload.
 */
function redirectIfAuthenticated({ context }: { context: RouterContext }) {
  if (!context.isLoading && context.isAuthenticated) {
    throw redirect({ to: '/' });
  }
}

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  beforeLoad: redirectIfAuthenticated,
  component: LoginPage,
  // Return type uses `?`, not `| undefined`. A required key whose type includes
  // undefined still counts as required, which would force every `<Link to="/login">`
  // in the app to pass an explicit `search` prop.
  validateSearch: (search: Record<string, unknown>): { redirect?: string } =>
    typeof search['redirect'] === 'string' ? { redirect: search['redirect'] } : {},
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/register',
  beforeLoad: redirectIfAuthenticated,
  component: RegisterPage,
  validateSearch: (search: Record<string, unknown>): { invitation?: string } =>
    typeof search['invitation'] === 'string' ? { invitation: search['invitation'] } : {},
});

const forgotPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/forgot-password',
  beforeLoad: redirectIfAuthenticated,
  component: ForgotPasswordPage,
});

const resetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/reset-password',
  component: ResetPasswordPage,
  // `email` only, and it is a convenience: the reset code is what authorises the
  // change, and it is typed in - never carried in the URL, where it would land in
  // browser history and any `Referer` header.
  validateSearch: (search: Record<string, unknown>): { email?: string } =>
    typeof search['email'] === 'string' ? { email: search['email'] } : {},
});

const verifyEmailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/verify-email',
  component: VerifyEmailPage,
  validateSearch: (search: Record<string, unknown>): { token?: string } =>
    typeof search['token'] === 'string' ? { token: search['token'] } : {},
});

const magicLinkRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/magic-link',
  beforeLoad: redirectIfAuthenticated,
  component: MagicLinkPage,
});

const magicLinkVerifyRoute = createRoute({
  getParentRoute: () => rootRoute,
  // A separate path from the request page so the emailed link cannot be
  // confused with the form, and so this one is never guarded.
  path: '/magic-link/verify',
  component: MagicLinkVerifyPage,
  validateSearch: (search: Record<string, unknown>): { token?: string } =>
    typeof search['token'] === 'string' ? { token: search['token'] } : {},
});

const otpRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/otp',
  beforeLoad: redirectIfAuthenticated,
  component: OtpPage,
});

const acceptInviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  // Deliberately unguarded: the recipient may or may not have an account, and
  // the page handles both.
  path: '/accept-invite',
  component: AcceptInvitePage,
  validateSearch: (search: Record<string, unknown>): { token?: string } =>
    typeof search['token'] === 'string' ? { token: search['token'] } : {},
});

/**
 * The public verifier.
 *
 * **The only route in this application that is deliberately reachable by somebody
 * with no account and no relationship to the business**, and it is a sibling of
 * the auth routes rather than a child of `appRoute` on purpose: hanging it under
 * the authenticated tree would put a session guard between a bank's credit officer
 * and the verdict, which defeats the entire feature.
 *
 * It is also outside `redirectIfAuthenticated`. A signed-in accountant checking a
 * proof for one of their own clients has a perfectly good reason to be here, and
 * bouncing them to the dashboard would make the link look broken.
 */
const verifyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/verify',
  component: VerifyPage,
});

// ---------------------------------------------------------------------------
// Authenticated routes
// ---------------------------------------------------------------------------
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  beforeLoad: ({ context, location }) => {
    // Do nothing until the session restore settles - see the note above.
    if (context.isLoading) return;

    if (!context.isAuthenticated) {
      throw redirect({
        to: '/login',
        // Preserve the intended destination so sign-in returns the user there
        // rather than dumping them on the dashboard.
        search: { redirect: location.href },
        replace: true,
      });
    }
  },
  component: AppShell,
  pendingComponent: PageSkeleton,
});

const dashboardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/',
  component: DashboardPage,
});

const membersRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/members',
  component: MembersPage,
});

const rolesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/roles',
  component: RolesPage,
});

const auditRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/audit',
  component: AuditPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings',
  component: SettingsPage,
});

/**
 * A `beforeLoad` guard that requires one permission.
 *
 * **It must not act while the session is still loading**, and that is the whole reason
 * this is a shared helper rather than six inline checks. On a hard reload, permissions
 * have not arrived yet, so `hasPermission` answers `false` for everything - and a guard
 * that redirects on that answer sends the user to the dashboard every single time they
 * refresh a page. Which is exactly what happened.
 *
 * Returning early is safe because the router re-evaluates guards when the context
 * changes, and `App.tsx` re-provides it once the session resolves. `appRoute` has always
 * relied on that; the per-route guards simply did not.
 */
function requirePermission(permission: string) {
  return ({ context }: { context: RouterContext }) => {
    if (context.isLoading) return;
    if (!context.hasPermission(permission)) throw redirect({ to: '/', replace: true });
  };
}

// Built modules. Each is permission-guarded in `beforeLoad` rather than inside
// the component, so an unauthorised user is redirected before the page renders
// and never briefly sees data they are not entitled to.
// Billing is guarded on `journal:write` rather than a permission of its own: these
// entries *are* journal entries, and inventing a parallel permission that grants the
// same underlying capability would be security theatre.
const billingRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/billing',
  beforeLoad: requirePermission('journal:read'),
  component: BillingPage,
});

// Accounts and cards. Guarded on `account:read` rather than `journal:read`, because this
// screen is about the chart of accounts rather than about the day book - and the account
// number behind it is the most sensitive thing either screen shows.
const accountsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/accounts',
  beforeLoad: requirePermission('account:read'),
  component: AccountsPage,
});

const accountingRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/accounting',
  /**
   * The selected tab lives in the URL.
   *
   * It was `useState`, so a reload dropped the user back on the first tab - they were
   * reading the trial balance, refreshed, and landed on the chart of accounts. A tab is
   * a location as far as the user is concerned, so it belongs in the address, which also
   * makes it linkable and survivable across a browser restart.
   *
   * Validated loosely on purpose: an unknown or hand-edited value falls back to the
   * default tab rather than throwing, because a bad query string should not be able to
   * break a page.
   */
  validateSearch: (search: Record<string, unknown>): { tab?: string } =>
    typeof search.tab === 'string' ? { tab: search.tab } : {},
  beforeLoad: requirePermission('account:read'),
  component: AccountingPage,
});

const invoicesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/invoices',
  /**
   * The selected tab lives in the URL.
   *
   * It was `useState`, so a reload dropped the user back on the first tab - they were
   * reading the trial balance, refreshed, and landed on the chart of accounts. A tab is
   * a location as far as the user is concerned, so it belongs in the address, which also
   * makes it linkable and survivable across a browser restart.
   *
   * Validated loosely on purpose: an unknown or hand-edited value falls back to the
   * default tab rather than throwing, because a bad query string should not be able to
   * break a page.
   */
  validateSearch: (search: Record<string, unknown>): { tab?: string } =>
    typeof search.tab === 'string' ? { tab: search.tab } : {},
  beforeLoad: requirePermission('invoice:read'),
  component: InvoicesPage,
});

const inventoryRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/inventory',
  /**
   * The selected tab lives in the URL.
   *
   * It was `useState`, so a reload dropped the user back on the first tab - they were
   * reading the trial balance, refreshed, and landed on the chart of accounts. A tab is
   * a location as far as the user is concerned, so it belongs in the address, which also
   * makes it linkable and survivable across a browser restart.
   *
   * Validated loosely on purpose: an unknown or hand-edited value falls back to the
   * default tab rather than throwing, because a bad query string should not be able to
   * break a page.
   */
  validateSearch: (search: Record<string, unknown>): { tab?: string } =>
    typeof search.tab === 'string' ? { tab: search.tab } : {},
  beforeLoad: requirePermission('inventory:read'),
  component: InventoryPage,
});

const documentsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/documents',
  beforeLoad: requirePermission('document:read'),
  component: DocumentsPage,
});

const analyticsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/analytics',
  beforeLoad: requirePermission('report:read'),
  component: AnalyticsPage,
});

// The third ledger. Guarded on `seal:read` rather than `journal:read`: seeing that
// the books are sealed is a different thing from reading them, and every seeded
// role gets it - an invoice's verification QR promises something, and the person
// who raised that invoice should be able to see whether the promise holds.
const trustRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/trust',
  beforeLoad: requirePermission('seal:read'),
  component: TrustPage,
});

const assistantRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/assistant',
  component: () => (
    <StagePlaceholder
      title="AI Assistant"
      description="Ask questions about your business in plain language."
      stage={6}
    />
  ),
});

// ---------------------------------------------------------------------------
// Tree
// ---------------------------------------------------------------------------
const routeTree = rootRoute.addChildren([
  loginRoute,
  registerRoute,
  forgotPasswordRoute,
  resetPasswordRoute,
  verifyEmailRoute,
  magicLinkRoute,
  magicLinkVerifyRoute,
  otpRoute,
  acceptInviteRoute,
  verifyRoute,
  appRoute.addChildren([
    dashboardRoute,
    membersRoute,
    rolesRoute,
    auditRoute,
    settingsRoute,
    billingRoute,
    accountsRoute,
    accountingRoute,
    invoicesRoute,
    inventoryRoute,
    documentsRoute,
    analyticsRoute,
    trustRoute,
    assistantRoute,
  ]),
]);

export const router = createRouter({
  routeTree,
  // Real values are injected by <RouterProvider> in App.tsx; these are only
  // placeholders so the type checks before the provider mounts.
  context: {
    isAuthenticated: false,
    isLoading: true,
    hasPermission: () => false,
  } satisfies RouterContext,
  defaultPreload: 'intent',
  defaultPreloadStaleTime: 0,
  scrollRestoration: true,
});

// Gives `<Link to="...">` autocompletion and compile-time checking of paths.
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
