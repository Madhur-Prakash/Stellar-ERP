import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, Pencil, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import { PageHeader } from '@/components/layout/AppShell';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Hint } from '@/components/ui/Hint';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAuth } from '@/features/auth/AuthProvider';
import { organizationsApi } from '@/features/organizations/api';
import { summariseRole } from '@/features/organizations/permissionSummary';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { PermissionGroup, Role } from '@/types/api';

/**
 * What a role can do, as tags.
 *
 * Falls back to the raw slugs while the catalogue is loading rather than rendering nothing:
 * a card that shows its permissions a beat late is fine, a card that appears to have none
 * is alarming.
 */
function RoleCapabilities({
  permissions,
  groups,
}: {
  permissions: string[];
  groups: PermissionGroup[] | undefined;
}) {
  if (!groups) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {permissions.slice(0, 4).map((permission) => (
          <Badge key={permission} tone="neutral" className="font-mono">
            {permission}
          </Badge>
        ))}
      </div>
    );
  }

  const summary = summariseRole(permissions, groups);

  if (summary.everything) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="primary">Everything</Badge>
        <span className="text-content-muted text-[12px]">
          all {summary.total} permissions, including ones added in future updates
        </span>
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {summary.capabilities.map((capability) => (
          <Badge
            key={capability.label}
            // Full access to an area reads as a stronger grant than a partial one, so it
            // is toned differently rather than being distinguishable only by the suffix.
            tone={capability.complete ? 'primary' : 'neutral'}
            // The slugs are still here for anyone who wants them - moved out of the way
            // rather than removed.
            title={capability.slugs.join('\n')}
          >
            {capability.label}
            {capability.detail && (
              <span className="ml-1 font-normal opacity-70">· {capability.detail}</span>
            )}
          </Badge>
        ))}
      </div>
      <p className="text-content-muted mt-2 text-[12px]">
        {summary.held} of {summary.total} permissions
      </p>
    </div>
  );
}

/**
 * Role editor.
 *
 * The permission picker is built from the server's catalogue (`/roles/permissions`) rather
 * than a hard-coded list, so it can never offer a permission the backend does not enforce,
 * or omit one it does. The cards above read from the same catalogue, which is what lets
 * them name capabilities instead of printing slugs.
 */
export function RolesPage() {
  const { can } = useAuth();
  const queryClient = useQueryClient();

  const [creating, setCreating] = useState(false);
  /** The custom role being edited, or null when the form is creating a new one. */
  const [editing, setEditing] = useState<Role | null>(null);
  const [name, setName] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string>();
  const nameField = useRef<HTMLInputElement>(null);

  const formOpen = creating || editing !== null;

  function closeForm() {
    setCreating(false);
    setEditing(null);
    setName('');
    setSelected(new Set());
    setError(undefined);
  }

  /**
   * Open the form on an existing role.
   *
   * The same form serves both jobs rather than a second copy of the permission
   * catalogue: the fields are identical, and two of them would drift the moment a
   * permission group changed.
   */
  function beginEdit(role: Role) {
    setCreating(false);
    setEditing(role);
    setName(role.name);
    // The stored grants, not the expanded set - editing `invoice:*` as eleven
    // separate ticks would silently rewrite a wildcard into a snapshot of what it
    // happens to cover today.
    setSelected(new Set(role.permissions));
    setError(undefined);
    requestAnimationFrame(() => {
      nameField.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      nameField.current?.focus({ preventScroll: true });
    });
  }

  /**
   * Report a problem with the name, and take the user to it.
   *
   * The inline message alone was not enough. "Create role" sits below the whole
   * permission catalogue, so by the time it is pressed the name field is usually
   * scrolled off the top of the window - the rejection rendered somewhere the user
   * was not looking, and the form read as if the button had done nothing at all.
   *
   * The toast says *that* something was refused; the scroll says *where*. Focusing
   * as well means the correction can be typed immediately, and a screen reader
   * lands on the field whose `aria-describedby` carries the same message.
   */
  function rejectName(message: string) {
    setError(message);
    toast.error(message);
    nameField.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    nameField.current?.focus({ preventScroll: true });
  }

  const { data: roles, isLoading } = useQuery({
    queryKey: ['roles'],
    queryFn: organizationsApi.listRoles,
  });

  const { data: catalogue } = useQuery({
    queryKey: ['permission-catalogue'],
    queryFn: organizationsApi.permissionCatalogue,
    staleTime: 60 * 60 * 1000, // the catalogue only changes on deploy
  });

  const create = useMutation({
    mutationFn: () =>
      organizationsApi.createRole({
        name: name.trim(),
        permissions: [...selected],
      }),
    onSuccess: (role) => {
      toast.success(`Role "${role.name}" created`);
      closeForm();
      void queryClient.invalidateQueries({ queryKey: ['roles'] });
    },
    onError: (err) => {
      rejectName(err instanceof ApiError ? err.message : 'Could not create the role');
    },
  });

  const update = useMutation({
    mutationFn: (role: Role) =>
      organizationsApi.updateRole(role.id, {
        name: name.trim(),
        permissions: [...selected],
      }),
    onSuccess: (role) => {
      toast.success(`Role "${role.name}" updated`, {
        // Worth stating plainly. This is not a draft that takes effect on next
        // sign-in: the server applies it to everyone holding the role at once.
        description:
          role.member_count > 0
            ? `Applied immediately to ${role.member_count} member${role.member_count === 1 ? '' : 's'}.`
            : undefined,
      });
      closeForm();
      void queryClient.invalidateQueries({ queryKey: ['roles'] });
    },
    onError: (err) => {
      rejectName(err instanceof ApiError ? err.message : 'Could not update the role');
    },
  });

  const remove = useMutation({
    mutationFn: organizationsApi.deleteRole,
    onSuccess: () => {
      toast.success('Role deleted');
      void queryClient.invalidateQueries({ queryKey: ['roles'] });
    },
    onError: (err) => {
      // The server refuses to delete a role people still hold, and its message
      // names the count - surface it verbatim rather than paraphrasing.
      toast.error(err instanceof ApiError ? err.message : 'Could not delete the role');
    },
  });

  function togglePermission(slug: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function toggleGroup(slugs: string[], allSelected: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const slug of slugs) {
        if (allSelected) next.delete(slug);
        else next.add(slug);
      }
      return next;
    });
  }

  return (
    <div>
      <PageHeader
        title="Roles and permissions"
        description="Roles bundle permissions. Built-in roles cannot be renamed or deleted, but their permissions can be adjusted."
        action={
          can('role:create') && !formOpen ? (
            <Button
              leftIcon={<Plus className="h-4 w-4" />}
              onClick={() => {
                closeForm();
                setCreating(true);
              }}
            >
              New role
            </Button>
          ) : undefined
        }
      />

      {/* ---- Create / edit ---- */}
      {formOpen && (
        <Card className="mb-4">
          <CardHeader
            title={editing ? `Edit ${editing.name}` : 'Create a role'}
            description={
              editing
                ? 'Changes apply immediately to everyone holding this role.'
                : 'Pick a name, then choose exactly what it can do.'
            }
            action={
              <Button variant="ghost" size="sm" onClick={closeForm}>
                Cancel
              </Button>
            }
          />
          <CardBody className="space-y-5">
            <div className="max-w-sm">
              <Input
                ref={nameField}
                label="Role name"
                placeholder="e.g. Invoice Clerk"
                value={name}
                onChange={(event) => setName(event.target.value)}
                error={error}
                autoFocus
              />
            </div>

            {catalogue ? (
              <div className="space-y-4">
                {catalogue.groups.map((group) => {
                  const slugs = group.permissions.map((p) => p.slug);
                  const allSelected = slugs.every((slug) => selected.has(slug));

                  return (
                    <fieldset key={group.key} className="border-border rounded-lg border p-4">
                      <legend className="flex items-center gap-2 px-1.5">
                        <span className="text-content text-[13px] font-semibold">
                          {group.label}
                        </span>
                        <button
                          type="button"
                          className="text-primary text-[11px] hover:underline"
                          onClick={() => toggleGroup(slugs, allSelected)}
                        >
                          {allSelected ? 'Clear' : 'Select all'}
                        </button>
                      </legend>
                      <p className="text-content-muted mb-3 text-[12px]">{group.description}</p>

                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                        {group.permissions.map((permission) => (
                          <label
                            key={permission.slug}
                            className="hover:bg-surface-hover flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[12px]"
                          >
                            <input
                              type="checkbox"
                              checked={selected.has(permission.slug)}
                              onChange={() => togglePermission(permission.slug)}
                              className="border-border text-primary focus:ring-ring/30 h-3.5 w-3.5 rounded"
                            />
                            <span className="text-content-secondary flex-1 truncate">
                              {permission.action}
                            </span>
                            <code className="text-content-muted text-[10px]">
                              {permission.resource}
                            </code>
                          </label>
                        ))}
                      </div>
                    </fieldset>
                  );
                })}
              </div>
            ) : (
              <Skeleton className="h-48 rounded-lg" />
            )}

            <div className="flex items-center gap-3">
              <Button
                loading={create.isPending || update.isPending}
                disabled={!name.trim() || selected.size === 0}
                onClick={() => {
                  if (!name.trim()) {
                    rejectName('Give the role a name');
                    return;
                  }
                  if (editing) update.mutate(editing);
                  else create.mutate();
                }}
              >
                {editing ? 'Save changes' : 'Create role'}
              </Button>
              <span className="text-content-muted text-[12px]">
                {selected.size} permission{selected.size === 1 ? '' : 's'} selected
              </span>
            </div>
          </CardBody>
        </Card>
      )}

      {/* ---- List ---- */}
      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-36 rounded-xl" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {(roles ?? []).map((role) => (
            <Card key={role.id} className="flex flex-col">
              <CardHeader
                title={
                  <span className="flex items-center gap-2">
                    {role.name}
                    {role.is_system && (
                      <Lock className="text-content-muted h-3 w-3" aria-label="Built-in role" />
                    )}
                  </span>
                }
                description={role.description ?? undefined}
              />
              <CardBody className="flex-1">
                <RoleCapabilities permissions={role.permissions} groups={catalogue?.groups} />

                <div className="border-border mt-4 flex items-center justify-between border-t pt-3">
                  <span className="text-content-muted text-[12px]">
                    {role.member_count} member{role.member_count === 1 ? '' : 's'}
                    {role.is_default && ' · default'}
                  </span>

                  <span className="flex items-center gap-0.5">
                    {/* A custom role is editable; a built-in one is not renameable
                        or deletable, which is what `is_system` guards. The server
                        enforces both - this only avoids offering a control that
                        would come back refused. */}
                    {can('role:update') && !role.is_system && (
                      <Hint text={`Edit ${role.name}`} width="w-44">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Edit ${role.name}`}
                          onClick={() => beginEdit(role)}
                        >
                          <Pencil className="text-content-muted h-3.5 w-3.5" />
                        </Button>
                      </Hint>
                    )}
                    {can('role:delete') && !role.is_system && (
                      // The desktop client has said why this is greyed out since it shipped;
                      // the web had a `title` that could never fire, because a disabled
                      // button suppresses pointer events. Same wording as the app - two
                      // clients explaining one rule differently is worse than either.
                      <Hint
                        text={
                          role.member_count > 0
                            ? 'People still hold this role'
                            : `Delete ${role.name}`
                        }
                        width="w-52"
                      >
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Delete ${role.name}`}
                          disabled={role.member_count > 0}
                          onClick={() => {
                            if (window.confirm(`Delete the "${role.name}" role?`)) {
                              remove.mutate(role.id);
                            }
                          }}
                        >
                          <Trash2
                            className={cn(
                              'h-3.5 w-3.5',
                              role.member_count > 0 ? 'text-content-muted' : 'text-danger',
                            )}
                          />
                        </Button>
                      </Hint>
                    )}
                  </span>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {catalogue && (
        <p className="text-content-muted mt-6 flex items-center gap-1.5 text-[12px]">
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
          {catalogue.total} permissions across {catalogue.groups.length} groups. The server enforces
          every one of them on every request.
        </p>
      )}
    </div>
  );
}
