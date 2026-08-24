/**
 * Recording which screens get opened.
 *
 * One event per navigation, and it is the denominator for everything else the
 * product wants to know: how many businesses reach the Trust screen, and how many
 * of those switch sealing on. Without a screen count, "twelve organizations sealed
 * this week" has no idea whether that is a good number.
 *
 * Two things it deliberately does not do:
 *
 * - **It does not send the URL.** A path can carry an id - `/invoices/abc-123` -
 *   and an analytics table holding invoice ids is inside the compliance boundary.
 *   The path is mapped to one of a closed set of screen names, and anything
 *   unrecognised is not recorded at all.
 * - **It does not fire while signed out.** The endpoint needs an organization to
 *   answer the only question this table exists for, and an anonymous event would be
 *   a row that can never be counted.
 */
import { useEffect, useRef } from 'react';
import { useRouterState } from '@tanstack/react-router';

import { useAuth } from '@/features/auth/AuthProvider';
import { type UsageAction, track } from '@/features/feedback/api';

/**
 * Route path to screen name.
 *
 * An explicit table rather than deriving the name from the path, so that adding a
 * route does not silently start recording it, and so a path with an id in it can
 * never become an event name.
 */
const SCREENS: Record<string, UsageAction> = {
  '/': 'screen.dashboard',
  '/billing': 'screen.billing',
  '/accounts': 'screen.accounts',
  '/accounting': 'screen.accounting',
  '/invoices': 'screen.sales',
  '/inventory': 'screen.inventory',
  '/documents': 'screen.documents',
  '/analytics': 'screen.analytics',
  '/trust': 'screen.trust',
  '/settings': 'screen.settings',
  '/verify': 'screen.verify',
};

export function useScreenTracking(): void {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { isAuthenticated } = useAuth();

  // The last screen actually recorded. A tab change on the Accounting screen
  // updates the query string but not the path, and firing again would count one
  // visit three times.
  const lastSent = useRef<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated) return;

    const action = SCREENS[pathname];
    if (!action) return;
    if (lastSent.current === action) return;

    lastSent.current = action;
    track(action);
  }, [pathname, isAuthenticated]);
}
