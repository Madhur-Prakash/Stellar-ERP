import 'package:flutter/widgets.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

/// The application's navigation, defined once.
///
/// In its own file so the sidebar, the footer, and the command palette read from the
/// same list. Hand-maintained copies would drift the first time a screen was added,
/// and a footer linking to something the sidebar does not offer - or worse, to a route
/// the user has no permission for - is a bug nobody notices until it is reported.
class NavItem {
  const NavItem({
    required this.label,
    required this.path,
    required this.icon,
    this.stage,
    this.permission,
  });

  final String label;
  final String path;
  final IconData icon;

  /// Rendered but disabled until the owning stage lands.
  final int? stage;

  /// Hidden entirely unless the caller holds this permission.
  final String? permission;

  bool get isBuilt => stage == null;
}

class NavSection {
  const NavSection({required this.title, required this.items});

  final String title;
  final List<NavItem> items;
}

/// Navigation.
///
/// Later-stage modules are listed but visibly disabled rather than omitted. It sets
/// the expectation of what this product is, and prevents the sidebar lurching as
/// stages ship. Each carries the stage that unlocks it.
const List<NavSection> navSections = <NavSection>[
  NavSection(
    title: 'Overview',
    items: <NavItem>[
      NavItem(label: 'Dashboard', path: '/', icon: LucideIcons.layoutDashboard),
    ],
  ),
  NavSection(
    title: 'Finance',
    items: <NavItem>[
      // First: for most users this is the only screen they open.
      NavItem(
        label: 'Billing',
        path: '/billing',
        icon: LucideIcons.indianRupee,
        permission: 'journal:read',
      ),
      // Straight after Billing, because it is the setup that screen depends on: the
      // accounts and cards it offers when recording a payment.
      NavItem(
        label: 'Accounts',
        path: '/accounts',
        icon: LucideIcons.landmark,
        permission: 'account:read',
      ),
      NavItem(
        label: 'Accounting',
        path: '/accounting',
        icon: LucideIcons.wallet,
        permission: 'account:read',
      ),
      NavItem(
        label: 'Sales',
        path: '/invoices',
        icon: LucideIcons.fileText,
        permission: 'invoice:read',
      ),
      NavItem(
        label: 'Inventory',
        path: '/inventory',
        icon: LucideIcons.boxes,
        permission: 'inventory:read',
      ),
      NavItem(
        label: 'Documents',
        path: '/documents',
        icon: LucideIcons.scanLine,
        permission: 'document:read',
      ),
      NavItem(
        label: 'Analytics',
        path: '/analytics',
        icon: LucideIcons.chartColumn,
        permission: 'report:read',
      ),
    ],
  ),
  NavSection(
    title: 'Intelligence',
    items: <NavItem>[
      NavItem(
        label: 'AI Assistant',
        path: '/assistant',
        icon: LucideIcons.sparkles,
        stage: 6,
      ),
    ],
  ),
  NavSection(
    title: 'Organization',
    items: <NavItem>[
      NavItem(
        label: 'Members',
        path: '/members',
        icon: LucideIcons.users,
        permission: 'member:read',
      ),
      NavItem(
        label: 'Roles',
        path: '/roles',
        icon: LucideIcons.shieldCheck,
        permission: 'role:read',
      ),
      NavItem(
        label: 'Audit log',
        path: '/audit',
        icon: LucideIcons.fileText,
        permission: 'audit:read',
      ),
      NavItem(label: 'Settings', path: '/settings', icon: LucideIcons.settings),
    ],
  ),
];
