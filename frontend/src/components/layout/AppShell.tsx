import { Link, Outlet } from '@tanstack/react-router';
import { Bell, Building2, Check, ChevronDown, LogOut, Menu, Plus, Search, X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { toast } from 'sonner';

import { CommandPalette } from '@/components/layout/CommandPalette';
import { Footer } from '@/components/layout/Footer';
import { NAV_SECTIONS } from '@/components/layout/nav';
import { ThemeToggle } from '@/components/layout/ThemeToggle';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/features/auth/AuthProvider';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';

/**
 * The organization control in the sidebar - a real switcher, not a link.
 *
 * It carried a chevron and went to `/settings`, which is the shape of a dropdown
 * making a promise it did not keep: the user's other organizations were reachable
 * only from the command palette, which you have to know exists. Everything needed
 * was already here - the session carries every membership, and `switchOrganization`
 * was wired for the palette - so this is a menu over data the shell already held.
 *
 * **It renders with no active organization too.** That case is not hypothetical: a
 * member whose membership is suspended is excluded from the switcher by design, and
 * a user who registered without a company never had one. Hiding the control from
 * exactly the people with nowhere to go is how the create-an-organization journey
 * became a dead end.
 */
function OrganizationSwitcher({ onNavigate }: { onNavigate: () => void }) {
  const { user, switchOrganization } = useAuth();
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState<string>();
  const wrapper = useRef<HTMLDivElement>(null);

  const active = user?.active_organization;
  const organizations = user?.organizations ?? [];

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };

    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  async function choose(organizationId: string) {
    if (organizationId === active?.id) {
      setOpen(false);
      return;
    }
    setSwitching(organizationId);
    try {
      // Re-mints the session: a different organization means different permissions,
      // so the tokens have to be reissued rather than reused.
      await switchOrganization(organizationId);
      setOpen(false);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not switch organization');
    } finally {
      setSwitching(undefined);
    }
  }

  return (
    <div ref={wrapper} className="relative px-3 pb-3">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
        className="hover:bg-surface-hover border-border flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition-colors"
      >
        <span
          className="bg-primary/12 text-primary flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[11px] font-bold"
          aria-hidden
        >
          {active ? active.name.slice(0, 2).toUpperCase() : <Building2 className="h-3.5 w-3.5" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-content block truncate text-[13px] font-medium">
            {active?.name ?? 'No organization'}
          </span>
          <span className="text-content-muted block truncate text-[11px]">
            {active?.role_name ?? 'Create or join one'}
          </span>
        </span>
        <ChevronDown
          className={cn(
            'text-content-muted h-3.5 w-3.5 shrink-0 transition-transform',
            open && 'rotate-180',
          )}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="menu"
          className="bg-surface-raised border-border absolute right-3 left-3 z-50 mt-1 overflow-hidden rounded-lg border shadow-lg"
        >
          {organizations.length > 0 && (
            <ul className="max-h-64 overflow-y-auto py-1">
              {organizations.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={switching !== undefined}
                    onClick={() => void choose(item.id)}
                    className="hover:bg-surface-hover flex w-full items-center gap-2.5 px-2.5 py-2 text-left transition-colors disabled:opacity-60"
                  >
                    <span
                      className="bg-primary/12 text-primary flex h-6 w-6 shrink-0 items-center justify-center rounded text-[10px] font-bold"
                      aria-hidden
                    >
                      {item.name.slice(0, 2).toUpperCase()}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="text-content block truncate text-[13px]">{item.name}</span>
                      <span className="text-content-muted block truncate text-[11px]">
                        {item.role_name}
                      </span>
                    </span>
                    {item.id === active?.id && (
                      <Check className="text-primary h-3.5 w-3.5 shrink-0" aria-label="Current" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="border-border border-t">
            <Link
              to="/settings"
              // The create card sits below profile, security and appearance, so
              // landing at the top of Settings hides the one thing this tap asked
              // for. The hash is what Settings scrolls to.
              hash="create-organization"
              onClick={() => {
                setOpen(false);
                onNavigate();
              }}
              className="hover:bg-surface-hover text-content-secondary flex items-center gap-2 px-2.5 py-2 text-[13px] transition-colors"
            >
              <Plus className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {organizations.length > 0 ? 'Create another organization' : 'Create an organization'}
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

export function AppShell() {
  const { user, signOut, can, isLoading } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  // The page behind the mobile drawer must not scroll. Without this, a swipe on the
  // scrim scrolls the document underneath, so closing the drawer returns the user to a
  // different part of the page than the one they left - and on iOS the rubber-banding
  // drags the fixed drawer around with it.
  useEffect(() => {
    if (!mobileOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [mobileOpen]);

  // Cmd/Ctrl+K opens the palette from anywhere.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  // The guard on this route deliberately does nothing while the session restore is
  // in flight - redirecting on `!isAuthenticated` before we know would bounce a
  // signed-in user to the sign-in screen on every load. The cost was that the shell
  // mounted anyway: a first-time visitor got a chrome-and-empty-dashboard flash
  // before being sent to sign in, which reads as the app having loaded and then
  // thrown them out.
  //
  // So the protected shell renders nothing of itself until the answer is known.
  // Public routes are untouched - they carry no guard and still paint immediately.
  if (isLoading) {
    return (
      <div className="bg-canvas flex min-h-dvh items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <span
            className="bg-primary text-primary-content flex h-10 w-10 animate-pulse items-center justify-center rounded-xl text-lg font-bold"
            aria-hidden
          >
            E
          </span>
          <p className="text-content-muted text-[13px]" role="status">
            Signing you in…
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-canvas min-h-dvh">
      {/* Keyboard users land here first; it lets them jump the whole sidebar. */}
      <a
        href="#main-content"
        className="sr-only-focusable bg-primary text-primary-content fixed top-3 left-3 z-[60] rounded-md px-3 py-2 text-sm font-medium"
      >
        Skip to content
      </a>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />

      {/* ---- Sidebar ---- */}
      <aside
        className={cn(
          'border-border bg-surface fixed inset-y-0 left-0 z-40 flex w-[248px] flex-col border-r',
          'transition-transform duration-[var(--duration-base)] ease-[var(--ease-out-quart)]',
          mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
        )}
        aria-label="Main navigation"
      >
        <div className="flex h-14 items-center justify-between px-4">
          <Link to="/" className="flex items-center gap-2">
            <span
              className="bg-primary text-primary-content flex h-7 w-7 items-center justify-center rounded-lg text-sm font-bold"
              aria-hidden
            >
              E
            </span>
            <span className="text-content text-[15px] font-semibold tracking-tight">
              Stellar ERP
            </span>
          </Link>
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <OrganizationSwitcher onNavigate={() => setMobileOpen(false)} />

        {/* Any click inside the nav closes the mobile drawer, which otherwise
            covers the page just navigated to. Handled here by delegation rather
            than in an effect on the pathname: setting state in an effect after
            render is an extra paint, and React flags it. */}
        <nav
          className="flex-1 space-y-5 overflow-y-auto px-3 pb-4"
          onClick={() => setMobileOpen(false)}
        >
          {NAV_SECTIONS.map((section) => {
            const visible = section.items.filter(
              (item) => !item.permission || can(item.permission),
            );
            if (visible.length === 0) return null;

            return (
              <div key={section.title}>
                <p className="text-content-muted px-2.5 pb-1.5 text-[10px] font-semibold tracking-wider uppercase">
                  {section.title}
                </p>
                <ul className="space-y-0.5">
                  {visible.map((item) => (
                    <li key={item.to}>
                      {item.stage ? (
                        // Not yet built. A disabled row is more honest than a link to a
                        // 404 - but the badge has to say so in words. "S6" is an internal
                        // build-order number that means nothing to whoever is using this,
                        // and on a greyed-out row it read as an error code.
                        <span
                          className="text-content-muted flex cursor-not-allowed items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] opacity-55"
                          title={`${item.label} is not built yet. It arrives in a later update.`}
                        >
                          <item.icon className="h-4 w-4 shrink-0" aria-hidden />
                          <span className="flex-1">{item.label}</span>
                          <Badge tone="neutral" className="text-[9px] whitespace-nowrap">
                            Coming soon
                          </Badge>
                        </span>
                      ) : (
                        <Link
                          to={item.to}
                          className="text-content-secondary hover:bg-surface-hover hover:text-content flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors"
                          activeProps={{
                            className: 'bg-primary/10 text-primary hover:bg-primary/10',
                          }}
                          activeOptions={{ exact: item.to === '/' }}
                        >
                          <item.icon className="h-4 w-4 shrink-0" aria-hidden />
                          {item.label}
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </nav>

        {/* User */}
        {user && (
          <div className="border-border border-t p-3">
            <div className="flex items-center gap-2.5">
              <Avatar src={user.avatar_url} name={user.full_name} initials={user.initials} />
              <div className="min-w-0 flex-1">
                <p className="text-content truncate text-[13px] font-medium">{user.full_name}</p>
                <p className="text-content-muted truncate text-[11px]">{user.email}</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void signOut()}
                aria-label="Sign out"
                title="Sign out"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </aside>

      {/* Scrim behind the mobile drawer */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* ---- Content ---- */}
      <div className="lg:pl-[248px]">
        <header className="glass border-border sticky top-0 z-20 flex h-14 items-center gap-2 border-b px-3 sm:gap-3 sm:px-4 lg:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <Menu className="h-4 w-4" />
          </Button>

          {/* Opens the palette. A button rather than a real input: it is a
              launcher, and a focusable text field here would swallow keystrokes
              meant for the page. */}
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            className="border-border bg-surface-sunken text-content-muted hover:bg-surface-hover hover:text-content-secondary flex h-8 min-w-0 max-w-72 flex-1 items-center gap-2 rounded-lg border px-2.5 text-[13px] transition-colors"
          >
            <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
            {/* Truncates rather than pushing the bell and the theme toggle off a
                360-pixel screen. */}
            <span className="flex-1 truncate text-left">Search or jump to…</span>
            <kbd className="border-border bg-surface text-content-muted hidden rounded border px-1.5 py-0.5 font-sans text-[10px] font-medium sm:inline-block">
              ⌘K
            </kbd>
          </button>

          <div className="flex-1" />

          <Button variant="ghost" size="icon" aria-label="Notifications" title="Notifications">
            <Bell className="h-4 w-4" />
          </Button>
          <ThemeToggle />
        </header>

        {/* Padding lives here, once, rather than in each page.
            Half the routes set `p-6 lg:p-8` themselves and half set nothing, so pages
            were inset inconsistently - and none of them had bottom padding, which is why
            the last row of a long table sat flush against the viewport edge with nothing
            below it and looked cut off. `pb-16` guarantees breathing room after the final
            element on every screen. */}
        <main id="main-content" className="animate-fade-in p-4 pb-10 sm:p-6 lg:p-8 lg:pb-12">
          <Outlet />
        </main>

        {/* The footer supplies the closing whitespace now, so `main` no longer needs the
            extra-large bottom padding that was standing in for it. */}
        <Footer />
      </div>
    </div>
  );
}

/** Consistent page heading, used by every route inside the shell. */
export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0">
        <h1 className="text-content text-[20px] leading-tight font-semibold tracking-[-0.025em] sm:text-[22px]">
          {title}
        </h1>
        {description && <p className="text-content-muted mt-1 text-[13px]">{description}</p>}
      </div>
      {action && <div className="min-w-0 sm:shrink-0">{action}</div>}
    </div>
  );
}

/** Placeholder for a module that a later stage delivers. */
export function StagePlaceholder({
  title,
  description,
  stage,
}: {
  title: string;
  description: string;
  stage: number;
}) {
  return (
    <div className="p-4 sm:p-6 lg:p-8">
      <PageHeader title={title} description={description} />
      <div className="border-border flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-20 text-center">
        <div
          className="bg-surface-sunken text-content-muted mb-4 flex h-12 w-12 items-center justify-center rounded-xl"
          aria-hidden
        >
          <Building2 className="h-5 w-5" />
        </div>
        <h2 className="text-content text-[15px] font-semibold">Coming soon</h2>
        <p className="text-content-muted mt-1.5 max-w-md text-[13px] leading-relaxed">
          {title} is not built yet. Nothing else in the app is waiting on it - your books, reports
          and records all work without it.
        </p>
        {/* The stage number stays, quietly: useful to whoever is building this, meaningless
            to whoever is using it, so it belongs in the small print and not the heading. */}
        <p className="text-content-muted mt-3 text-[11px]">Planned for stage {stage}</p>
      </div>
    </div>
  );
}
