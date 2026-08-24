import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type Theme = 'light' | 'dark' | 'system';

/** Must match the key read by the pre-paint script in `index.html`. */
const STORAGE_KEY = 'personalerp-theme';

interface ThemeContextValue {
  /** The user's choice, including `system`. */
  theme: Theme;
  /** What is actually rendered - `system` resolved against the OS setting. */
  resolvedTheme: 'light' | 'dark';
  setTheme: (theme: Theme) => void;
  /** Cycles light -> dark -> system, for the toolbar button. */
  cycleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // Private browsing can throw on access. Fall through to the default.
  }
  return 'system';
}

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/**
 * Theme state.
 *
 * Three modes, not two: `system` is the default because it is the behaviour
 * users expect from a modern app, and it must remain a distinct stored value.
 * Collapsing it to whichever concrete theme was current at the time would freeze
 * the choice, and the app would stop following the OS at sunset.
 *
 * The flash of the wrong theme on load is prevented by an inline script in
 * `index.html` that applies the class before first paint - React mounts too late
 * to do it here.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readStoredTheme);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  // Track the OS preference so `system` stays live rather than being sampled
  // once at mount.
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  const resolvedTheme = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme;

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', resolvedTheme === 'dark');
    // Tells the browser to theme form controls and scrollbars to match.
    root.style.colorScheme = resolvedTheme;
  }, [resolvedTheme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Non-fatal: the theme still applies for this session.
    }
  }, []);

  const cycleTheme = useCallback(() => {
    setTheme(theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light');
  }, [theme, setTheme]);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, resolvedTheme, setTheme, cycleTheme }),
    [theme, resolvedTheme, setTheme, cycleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

// The hook lives beside its provider on purpose - they are one unit, and
// splitting them across files to satisfy fast-refresh granularity would make the
// context harder to follow for no runtime benefit.
// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used inside <ThemeProvider>');
  }
  return context;
}
