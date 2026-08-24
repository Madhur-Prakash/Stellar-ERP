import { useNavigate } from '@tanstack/react-router';
import { Command } from 'cmdk';
import {
  Building2,
  FileText,
  LayoutDashboard,
  LogOut,
  Moon,
  Monitor,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  Users,
} from 'lucide-react';
import { useEffect, useRef } from 'react';

import { useAuth } from '@/features/auth/AuthProvider';
import { useTheme } from '@/features/theme/ThemeProvider';

/**
 * The command palette.
 *
 * A keyboard-driven launcher is core navigation here, not a power-user extra: the
 * people running this are in their books all day, and reaching for a mouse to
 * change screens is the slowest part of that. It is wired up in Stage 1 with
 * navigation, theme, and organization switching; Stage 6 adds natural-language
 * actions to the same surface, which is why it is built as an extensible list of
 * groups rather than a fixed menu.
 *
 * Permission-gated entries are filtered out, not disabled - offering a command
 * that will 403 is worse than not offering it.
 *
 * Mounted only while open, so every invocation starts with an empty query rather than
 * whatever was typed last time.
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!open) return null;
  return <Palette onOpenChange={onOpenChange} />;
}

function Palette({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const { user, signOut, switchOrganization, can } = useAuth();
  const { setTheme } = useTheme();
  const dialog = useRef<HTMLDialogElement>(null);

  // `showModal` on mount, which is what makes Escape work, traps focus inside the
  // palette, and makes the page behind it inert to both pointer and screen reader. The
  // hand-rolled overlay this replaces got none of those - and the `ESC` hint beside the
  // search box had been promising one of them since the palette was written.
  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  // Belt and braces on the scroll lock: the dialog is in the top layer and the page
  // behind it cannot be interacted with, but browsers still differ on whether it can be
  // scrolled.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  function run(action: () => void) {
    onOpenChange(false);
    // Deferred a frame so the dialog unmounts before navigation, avoiding a
    // visible flash of the palette over the new route.
    requestAnimationFrame(action);
  }

  const otherOrganizations = (user?.organizations ?? []).filter(
    (organization) => organization.id !== user?.active_organization?.id,
  );

  return (
    <dialog
      ref={dialog}
      aria-label="Command palette"
      // `cancel` fires for Escape. Prevented and routed through `onOpenChange` so the
      // parent's state always agrees with whether the dialog is really open - letting the
      // browser close it directly would leave `open` true and the palette unopenable.
      onCancel={(event) => {
        event.preventDefault();
        onOpenChange(false);
      }}
      onClick={(event) => {
        // A backdrop click lands on the dialog element itself, because ::backdrop is not
        // a child. Compare against the box to tell the two apart.
        if (event.target !== event.currentTarget) return;
        const box = event.currentTarget.getBoundingClientRect();
        const inside =
          event.clientX >= box.left &&
          event.clientX <= box.right &&
          event.clientY >= box.top &&
          event.clientY <= box.bottom;
        if (!inside) onOpenChange(false);
      }}
      className={
        // Pinned near the top rather than centred: the list grows downwards as you type,
        // and a centred dialog would shift under the cursor while being read.
        'animate-slide-up mx-auto mt-[12vh] mb-auto w-[min(92vw,32rem)] rounded-xl border-0 bg-transparent p-0 ' +
        'backdrop:bg-black/50 backdrop:backdrop-blur-sm'
      }
    >
      <Command
        label="Command palette"
        className="bg-surface-raised border-border w-full overflow-hidden rounded-xl border shadow-xl"
        loop
      >
        <div className="border-border flex items-center gap-2.5 border-b px-4">
          <Search className="text-content-muted h-4 w-4 shrink-0" aria-hidden />
          <Command.Input
            autoFocus
            placeholder="Search or run a command…"
            className="text-content placeholder:text-content-muted h-12 flex-1 bg-transparent text-sm outline-none"
          />
          <kbd className="border-border text-content-muted rounded border px-1.5 py-0.5 text-[10px] font-medium">
            ESC
          </kbd>
        </div>

        <Command.List className="max-h-[52vh] overflow-y-auto p-2">
          <Command.Empty className="text-content-muted py-8 text-center text-[13px]">
            No results found.
          </Command.Empty>

          <Group heading="Navigate">
            <Item
              icon={LayoutDashboard}
              label="Dashboard"
              onSelect={() => run(() => void navigate({ to: '/' }))}
            />
            {can('member:read') && (
              <Item
                icon={Users}
                label="Members"
                onSelect={() => run(() => void navigate({ to: '/members' }))}
              />
            )}
            {can('role:read') && (
              <Item
                icon={ShieldCheck}
                label="Roles and permissions"
                onSelect={() => run(() => void navigate({ to: '/roles' }))}
              />
            )}
            {can('audit:read') && (
              <Item
                icon={FileText}
                label="Audit log"
                onSelect={() => run(() => void navigate({ to: '/audit' }))}
              />
            )}
            <Item
              icon={Settings}
              label="Settings"
              onSelect={() => run(() => void navigate({ to: '/settings' }))}
            />
          </Group>

          {otherOrganizations.length > 0 && (
            <Group heading="Switch organization">
              {otherOrganizations.map((organization) => (
                <Item
                  key={organization.id}
                  icon={Building2}
                  label={organization.name}
                  hint={organization.role_name}
                  onSelect={() => run(() => void switchOrganization(organization.id))}
                />
              ))}
            </Group>
          )}

          <Group heading="Appearance">
            <Item icon={Sun} label="Light theme" onSelect={() => run(() => setTheme('light'))} />
            <Item icon={Moon} label="Dark theme" onSelect={() => run(() => setTheme('dark'))} />
            <Item
              icon={Monitor}
              label="Match system theme"
              onSelect={() => run(() => setTheme('system'))}
            />
          </Group>

          <Group heading="Account">
            <Item icon={LogOut} label="Sign out" onSelect={() => run(() => void signOut())} />
          </Group>
        </Command.List>
      </Command>
    </dialog>
  );
}

function Group({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <Command.Group
      heading={heading}
      className="[&_[cmdk-group-heading]]:text-content-muted mb-1 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:uppercase"
    >
      {children}
    </Command.Group>
  );
}

function Item({
  icon: Icon,
  label,
  hint,
  onSelect,
}: {
  icon: typeof LayoutDashboard;
  label: string;
  hint?: string;
  onSelect: () => void;
}) {
  return (
    <Command.Item
      value={label}
      onSelect={onSelect}
      className="text-content-secondary data-[selected=true]:bg-surface-hover data-[selected=true]:text-content flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px]"
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden />
      <span className="flex-1">{label}</span>
      {hint && <span className="text-content-muted text-[11px]">{hint}</span>}
    </Command.Item>
  );
}
