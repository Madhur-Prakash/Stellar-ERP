import { useQueryClient } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { authApi } from '@/features/auth/api';
import { bootstrapSession, setAccessToken, setSessionExpiredHandler } from '@/lib/api';
import { setLocaleSettings } from '@/lib/locale';
import type { AuthenticatedUser, TokenResponse } from '@/types/api';

interface AuthContextValue {
  user: AuthenticatedUser | null;
  /** True until the initial session restore settles. Gate routing on this. */
  isLoading: boolean;
  isAuthenticated: boolean;
  /** Store the result of a successful sign-in. */
  applySession: (tokens: TokenResponse) => void;
  signOut: (allDevices?: boolean) => Promise<void>;
  /** Re-fetch the principal after a change to profile, org, or permissions. */
  refresh: () => Promise<void>;
  switchOrganization: (organizationId: string) => Promise<void>;
  /** Permission check for conditionally rendering UI. */
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Session state for the app.
 *
 * On mount it attempts one silent refresh: the access token lives in memory and
 * is gone after a reload, but the HttpOnly refresh cookie is not, so exchanging
 * it restores the session without the user re-entering anything. Failure just
 * means "signed out", which is the ordinary first-visit case.
 *
 * `isLoading` exists so route guards can distinguish "not signed in" from "we do
 * not know yet". Without it, every reload would bounce an authenticated user to
 * the login screen for a frame before the refresh completes.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const queryClient = useQueryClient();

  /**
   * Set the principal, and adopt their organization's currency, timezone, and financial
   * year at the same moment.
   *
   * One function rather than a `setUser` call plus a reminder, because the two must never
   * drift: the formatters read those settings, so a principal set without them renders
   * every amount on the next paint in the wrong currency. Applied before the re-render
   * this triggers, so the first paint after signing in is already correct.
   */
  const applyPrincipal = useCallback((principal: AuthenticatedUser | null) => {
    setLocaleSettings(principal?.active_organization ?? null);
    setUser(principal);
  }, []);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    applyPrincipal(null);
    // Drop every cached query: leaving another user's data in the cache after a
    // sign-out on a shared machine would leak it to the next person.
    queryClient.clear();
  }, [queryClient, applyPrincipal]);

  // Called by the HTTP layer when a refresh fails - the session is genuinely
  // over, not merely stale.
  useEffect(() => {
    setSessionExpiredHandler(clearSession);
  }, [clearSession, applyPrincipal]);

  useEffect(() => {
    let cancelled = false;

    async function restore() {
      try {
        const restored = await bootstrapSession();
        if (cancelled) return;

        if (restored) {
          const principal = await authApi.me();
          if (!cancelled) applyPrincipal(principal);
        }
      } catch {
        if (!cancelled) clearSession();
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, [clearSession, applyPrincipal]);

  const applySession = useCallback(
    (tokens: TokenResponse) => {
      setAccessToken(tokens.access_token);
      applyPrincipal(tokens.user);
    },
    [applyPrincipal],
  );

  const signOut = useCallback(
    async (allDevices = false) => {
      try {
        await authApi.logout({ all_devices: allDevices });
      } catch {
        // Sign out locally even if the call fails - the user asked to leave, and
        // the token expires on its own regardless.
      } finally {
        clearSession();
      }
    },
    [clearSession],
  );

  const refresh = useCallback(async () => {
    try {
      applyPrincipal(await authApi.me());
    } catch {
      clearSession();
    }
  }, [clearSession, applyPrincipal]);

  const switchOrganization = useCallback(
    async (organizationId: string) => {
      // Returns a new access token: permissions are per-organization and are
      // embedded in the token, so the old one cannot be reused.
      const tokens = await authApi.switchOrganization(organizationId);
      setAccessToken(tokens.access_token);
      applyPrincipal(tokens.user);
      // Every cached query was scoped to the previous organization.
      queryClient.clear();
    },
    [queryClient, applyPrincipal],
  );

  const can = useCallback(
    (permission: string) => {
      if (!user) return false;
      return user.permissions.includes(permission);
    },
    [user],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isLoading,
      isAuthenticated: user !== null,
      applySession,
      signOut,
      refresh,
      switchOrganization,
      can,
    }),
    [user, isLoading, applySession, signOut, refresh, switchOrganization, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// The hook belongs beside its provider - they are one unit.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return context;
}
