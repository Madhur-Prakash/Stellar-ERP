/**
 * The application's navigation, defined once.
 *
 * In its own module so the sidebar and the footer read from the same list. Two
 * hand-maintained copies would drift the first time a screen was added, and a footer
 * linking to something the sidebar does not offer - or worse, to a route the user has no
 * permission for - is a bug nobody notices until it is reported.
 *
 * It also keeps a plain constant out of a file that exports components, which breaks
 * React fast refresh.
 */
import {
  BarChart3,
  Boxes,
  FileText,
  IndianRupee,
  Landmark,
  LayoutDashboard,
  ScanLine,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
  Wallet,
} from 'lucide-react';

export interface NavItem {
  label: string;
  to: string;
  icon: typeof LayoutDashboard;
  /** Rendered but disabled until the owning stage lands. */
  stage?: number;
  /** Hidden entirely unless the caller holds this permission. */
  permission?: string;
}

/**
 * Navigation.
 *
 * Later-stage modules are listed but visibly disabled rather than omitted. It sets the
 * expectation of what this product is, and prevents the sidebar lurching as stages ship.
 * Each carries the stage that unlocks it.
 */
export const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: 'Overview',
    items: [{ label: 'Dashboard', to: '/', icon: LayoutDashboard }],
  },
  {
    title: 'Finance',
    items: [
      // First: for most users this is the only screen they open.
      { label: 'Billing', to: '/billing', icon: IndianRupee, permission: 'journal:read' },
      // Straight after Billing, because it is the setup that screen depends on: the
      // accounts and cards it offers when recording a payment.
      { label: 'Accounts', to: '/accounts', icon: Landmark, permission: 'account:read' },
      { label: 'Accounting', to: '/accounting', icon: Wallet, permission: 'account:read' },
      { label: 'Sales', to: '/invoices', icon: FileText, permission: 'invoice:read' },
      { label: 'Inventory', to: '/inventory', icon: Boxes, permission: 'inventory:read' },
      { label: 'Documents', to: '/documents', icon: ScanLine, permission: 'document:read' },
      { label: 'Analytics', to: '/analytics', icon: BarChart3, permission: 'report:read' },
    ],
  },
  {
    title: 'Intelligence',
    items: [{ label: 'AI Assistant', to: '/assistant', icon: Sparkles, stage: 6 }],
  },
  {
    title: 'Organization',
    items: [
      { label: 'Members', to: '/members', icon: Users, permission: 'member:read' },
      { label: 'Roles', to: '/roles', icon: ShieldCheck, permission: 'role:read' },
      { label: 'Audit log', to: '/audit', icon: FileText, permission: 'audit:read' },
      { label: 'Settings', to: '/settings', icon: Settings },
    ],
  },
];
