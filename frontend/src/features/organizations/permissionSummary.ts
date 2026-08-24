/**
 * Turning a role's stored permissions into tags a person can read.
 *
 * A role card showing `*:*` tells the reader nothing - it is the internal wire format, and
 * the one role where it appears is the most consequential one to understand. Roles also
 * store a mix of three forms (`account:read`, `account:*`, `*:*`), so a card that printed
 * the raw list would show the same capability three different ways depending on how the
 * role happened to be defined.
 *
 * This resolves all three against the server's own catalogue and describes what the role
 * can actually do, grouped the way the role editor groups it.
 *
 * Pure, and in its own module: a file exporting both a component and a function breaks
 * React fast refresh.
 */
import type { PermissionGroup } from '@/types/api';

export interface Capability {
  /** The group's own label, e.g. "Accounting". */
  label: string;
  /** How much of the group, when it is not all of it. */
  detail?: string;
  /** Holds every permission in the group. */
  complete: boolean;
  /** The concrete slugs behind this tag, for the reader who wants them. */
  slugs: string[];
}

export interface RoleSummary {
  /** Holds `*:*`, so listing groups would be noise. */
  everything: boolean;
  capabilities: Capability[];
  /** Concrete permissions held, and how many exist. */
  held: number;
  total: number;
}

/**
 * Does this role hold a given concrete permission?
 *
 * Mirrors the backend's matching rules, which is the only reason this can be trusted:
 * `*:*` grants everything, `resource:*` grants every action on one resource, and anything
 * else must match exactly.
 */
function holds(permissions: readonly string[], resource: string, slug: string): boolean {
  return (
    permissions.includes('*:*') ||
    permissions.includes(`${resource}:*`) ||
    permissions.includes(slug)
  );
}

export function summariseRole(
  permissions: readonly string[],
  groups: readonly PermissionGroup[],
): RoleSummary {
  const everything = permissions.includes('*:*');

  let held = 0;
  let total = 0;
  const capabilities: Capability[] = [];

  for (const group of groups) {
    total += group.permissions.length;
    const mine = group.permissions.filter((permission) =>
      holds(permissions, permission.resource, permission.slug),
    );
    held += mine.length;

    if (mine.length === 0) continue;

    const complete = mine.length === group.permissions.length;
    // "View only" is the distinction that actually matters when handing someone a role -
    // far more than which four of nine permissions they hold. Stated when true, and a
    // count falls back for anything else.
    const readOnly = mine.every((permission) => permission.action === 'read');

    capabilities.push({
      label: group.label,
      ...(complete
        ? {}
        : { detail: readOnly ? 'view only' : `${mine.length} of ${group.permissions.length}` }),
      complete,
      slugs: mine.map((permission) => permission.slug),
    });
  }

  return { everything, capabilities, held, total };
}
