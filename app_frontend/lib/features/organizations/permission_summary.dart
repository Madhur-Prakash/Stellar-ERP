/// Turning a role's stored permissions into tags a person can read.
///
/// A role card showing `*:*` tells the reader nothing - it is the internal wire format, and
/// the one role where it appears is the most consequential one to understand. Roles also
/// store a mix of three forms (`account:read`, `account:*`, `*:*`), so a card that printed
/// the raw list would show the same capability three different ways depending on how the
/// role happened to be defined.
///
/// This resolves all three against the server's own catalogue and describes what the role
/// can actually do, grouped the way the role editor groups it.
library;

import '../../models/organization.dart';

class Capability {
  const Capability({
    required this.label,
    this.detail,
    required this.complete,
    required this.slugs,
  });

  /// The group's own label, e.g. "Accounting".
  final String label;

  /// How much of the group, when it is not all of it.
  final String? detail;

  /// Holds every permission in the group.
  final bool complete;

  /// The concrete slugs behind this tag, for the reader who wants them.
  final List<String> slugs;
}

class RoleCapabilities {
  const RoleCapabilities({
    required this.everything,
    required this.capabilities,
    required this.held,
    required this.total,
  });

  /// Holds `*:*`, so listing groups would be noise.
  final bool everything;
  final List<Capability> capabilities;

  /// Concrete permissions held, and how many exist.
  final int held;
  final int total;
}

/// Does this role hold a given concrete permission?
///
/// Mirrors the backend's matching rules, which is the only reason this can be trusted:
/// `*:*` grants everything, `resource:*` grants every action on one resource, and anything
/// else must match exactly.
bool _holds(List<String> permissions, String resource, String slug) =>
    permissions.contains('*:*') ||
    permissions.contains('$resource:*') ||
    permissions.contains(slug);

RoleCapabilities summariseRole(
  List<String> permissions,
  List<PermissionGroup> groups,
) {
  final bool everything = permissions.contains('*:*');
  int held = 0;
  int total = 0;
  final List<Capability> capabilities = <Capability>[];

  for (final PermissionGroup group in groups) {
    total += group.permissions.length;
    final List<PermissionInfo> mine = group.permissions
        .where((PermissionInfo p) => _holds(permissions, p.resource, p.slug))
        .toList(growable: false);
    held += mine.length;

    if (mine.isEmpty) continue;

    final bool complete = mine.length == group.permissions.length;
    // "View only" is the distinction that actually matters when handing someone a role - far
    // more than which four of nine permissions they hold. Stated when true, with a count as
    // the fallback for anything else.
    final bool readOnly = mine.every((PermissionInfo p) => p.action == 'read');

    capabilities.add(
      Capability(
        label: group.label,
        detail: complete
            ? null
            : readOnly
            ? 'view only'
            : '${mine.length} of ${group.permissions.length}',
        complete: complete,
        slugs: mine.map((PermissionInfo p) => p.slug).toList(growable: false),
      ),
    );
  }

  return RoleCapabilities(
    everything: everything,
    capabilities: capabilities,
    held: held,
    total: total,
  );
}
