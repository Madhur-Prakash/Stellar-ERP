import { Outlet } from '@tanstack/react-router';

import { FeedbackWidget } from '@/features/feedback/FeedbackWidget';
import { useScreenTracking } from '@/features/feedback/useScreenTracking';

/**
 * The outermost layout: the route tree, plus the two things that belong on *every*
 * page including the public ones.
 *
 * In its own file rather than inline in `router.tsx` because that module exports
 * the router itself - a non-component - and a file mixing component and
 * non-component exports breaks React fast refresh. The lint rule that says so is
 * correct: editing the route tree would otherwise force a full reload instead of a
 * hot swap.
 *
 * The feedback button lives here rather than in `AppShell` because the most useful
 * report in any product comes from somebody who could not sign in, and `AppShell`
 * only ever renders behind the auth guard. It also has to be inside the router
 * tree, not a sibling of `<RouterProvider>` in `App.tsx`, because it records which
 * screen a message came from.
 */
export function RootLayout() {
  useScreenTracking();

  return (
    <>
      <Outlet />
      <FeedbackWidget />
    </>
  );
}
