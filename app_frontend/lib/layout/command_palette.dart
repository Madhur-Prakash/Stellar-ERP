import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../core/api_error.dart';
import '../models/auth.dart';
import '../state/auth_controller.dart';
import '../state/theme_controller.dart';
import '../theme/tokens.dart';
import '../widgets/toast.dart';

/// The command palette.
///
/// A keyboard-driven launcher is core navigation here, not a power-user extra: the
/// people running this are in their books all day, and reaching for a mouse to change
/// screens is the slowest part of that. It carries navigation, theme, and organization
/// switching; stage 6 adds natural-language actions to the same surface, which is why
/// it is built as an extensible list of groups rather than a fixed menu.
///
/// Permission-gated entries are filtered out, not disabled - offering a command that
/// will 403 is worse than not offering it.
///
/// Mounted only while open, so every invocation starts with an empty query rather than
/// whatever was typed last time.
Future<void> showCommandPalette(BuildContext context, WidgetRef ref) {
  return showDialog<void>(
    context: context,
    barrierColor: Colors.black.withValues(alpha: 0.5),
    builder: (BuildContext context) => _CommandPalette(ref: ref),
  );
}

class _Command {
  const _Command({
    required this.icon,
    required this.label,
    required this.run,
    this.hint,
  });

  final IconData icon;
  final String label;
  final VoidCallback run;
  final String? hint;
}

class _CommandGroup {
  const _CommandGroup({required this.heading, required this.commands});

  final String heading;
  final List<_Command> commands;
}

class _CommandPalette extends StatefulWidget {
  const _CommandPalette({required this.ref});

  final WidgetRef ref;

  @override
  State<_CommandPalette> createState() => _CommandPaletteState();
}

class _CommandPaletteState extends State<_CommandPalette> {
  final TextEditingController _query = TextEditingController();

  /// Which of the matching commands is highlighted. Arrow keys move it, Enter runs it.
  int _selected = 0;

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  /// Close, then act.
  ///
  /// Deferred a frame so the dialog is gone before navigation, avoiding a visible
  /// flash of the palette over the new screen.
  ///
  /// **Every action closes over a captured router or toast host, never over this
  /// dialog's `BuildContext`.** By the time the callback runs the dialog has been
  /// popped and its element is defunct, so reading `context.go` there would look
  /// correct and throw at runtime - which is exactly the failure a deferred callback
  /// makes hard to trace.
  void _run(VoidCallback action) {
    Navigator.of(context).pop();
    WidgetsBinding.instance.addPostFrameCallback((_) => action());
  }

  List<_CommandGroup> _groups(GoRouter router, ToastScopeState toasts) {
    final AuthState auth = widget.ref.read(authControllerProvider);
    final AuthController controller = widget.ref.read(
      authControllerProvider.notifier,
    );
    final ThemeController theme = widget.ref.read(
      themeControllerProvider.notifier,
    );

    final List<OrganizationSummary> others = auth.user == null
        ? const <OrganizationSummary>[]
        : auth.user!.organizations
              .where((OrganizationSummary o) => o.id != auth.organization?.id)
              .toList(growable: false);

    return <_CommandGroup>[
      _CommandGroup(
        heading: 'Navigate',
        commands: <_Command>[
          _Command(
            icon: LucideIcons.layoutDashboard,
            label: 'Dashboard',
            run: () => _run(() => router.go('/')),
          ),
          if (auth.can('journal:read'))
            _Command(
              icon: LucideIcons.indianRupee,
              label: 'Billing',
              run: () => _run(() => router.go('/billing')),
            ),
          if (auth.can('account:read'))
            _Command(
              icon: LucideIcons.landmark,
              label: 'Accounts & cards',
              run: () => _run(() => router.go('/accounts')),
            ),
          if (auth.can('account:read'))
            _Command(
              icon: LucideIcons.wallet,
              label: 'Accounting',
              run: () => _run(() => router.go('/accounting')),
            ),
          if (auth.can('invoice:read'))
            _Command(
              icon: LucideIcons.fileText,
              label: 'Sales',
              run: () => _run(() => router.go('/invoices')),
            ),
          if (auth.can('inventory:read'))
            _Command(
              icon: LucideIcons.boxes,
              label: 'Inventory',
              run: () => _run(() => router.go('/inventory')),
            ),
          if (auth.can('document:read'))
            _Command(
              icon: LucideIcons.scanLine,
              label: 'Scanned documents',
              run: () => _run(() => router.go('/documents')),
            ),
          if (auth.can('report:read'))
            _Command(
              icon: LucideIcons.chartColumn,
              label: 'Analytics',
              run: () => _run(() => router.go('/analytics')),
            ),
          if (auth.can('member:read'))
            _Command(
              icon: LucideIcons.users,
              label: 'Members',
              run: () => _run(() => router.go('/members')),
            ),
          if (auth.can('role:read'))
            _Command(
              icon: LucideIcons.shieldCheck,
              label: 'Roles and permissions',
              run: () => _run(() => router.go('/roles')),
            ),
          if (auth.can('audit:read'))
            _Command(
              icon: LucideIcons.fileText,
              label: 'Audit log',
              run: () => _run(() => router.go('/audit')),
            ),
          _Command(
            icon: LucideIcons.settings,
            label: 'Settings',
            run: () => _run(() => router.go('/settings')),
          ),
        ],
      ),
      if (others.isNotEmpty)
        _CommandGroup(
          heading: 'Switch organization',
          commands: <_Command>[
            for (final OrganizationSummary organization in others)
              _Command(
                icon: LucideIcons.building2,
                label: organization.name,
                hint: organization.roleName,
                run: () => _run(() async {
                  try {
                    await controller.switchOrganization(organization.id);
                  } catch (error) {
                    toasts.show(
                      ToastData(
                        message: ApiError.from(error).message,
                        tone: ToastTone.error,
                      ),
                    );
                  }
                }),
              ),
          ],
        ),
      _CommandGroup(
        heading: 'Appearance',
        commands: <_Command>[
          _Command(
            icon: LucideIcons.sun,
            label: 'Light theme',
            run: () => _run(() => theme.set(ThemeChoice.light)),
          ),
          _Command(
            icon: LucideIcons.moon,
            label: 'Dark theme',
            run: () => _run(() => theme.set(ThemeChoice.dark)),
          ),
          _Command(
            icon: LucideIcons.monitor,
            label: 'Match system theme',
            run: () => _run(() => theme.set(ThemeChoice.system)),
          ),
        ],
      ),
      _CommandGroup(
        heading: 'Account',
        commands: <_Command>[
          _Command(
            icon: LucideIcons.logOut,
            label: 'Sign out',
            run: () => _run(controller.signOut),
          ),
        ],
      ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final String query = _query.text.trim().toLowerCase();

    // Resolved here, while this element is still mounted - see `_run`.
    final GoRouter router = GoRouter.of(context);
    final ToastScopeState toasts = ToastScope.of(context);

    final List<_CommandGroup> filtered =
        <_CommandGroup>[
              for (final _CommandGroup group in _groups(router, toasts))
                if (query.isEmpty)
                  group
                else
                  _CommandGroup(
                    heading: group.heading,
                    commands: group.commands
                        .where(
                          (_Command c) => c.label.toLowerCase().contains(query),
                        )
                        .toList(growable: false),
                  ),
            ]
            .where((_CommandGroup group) => group.commands.isNotEmpty)
            .toList(growable: false);

    final List<_Command> flat = filtered
        .expand((_CommandGroup group) => group.commands)
        .toList(growable: false);
    final int selected = flat.isEmpty ? 0 : _selected.clamp(0, flat.length - 1);

    return Align(
      // Pinned near the top rather than centred: the list grows downwards as you type,
      // and a centred dialog would shift under the cursor while being read.
      alignment: const Alignment(0, -0.72),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 512),
          child: Material(
            color: t.surfaceRaised,
            borderRadius: BorderRadius.circular(Radii.xl),
            clipBehavior: Clip.antiAlias,
            child: Container(
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(Radii.xl),
                border: Border.all(color: t.border),
                boxShadow: t.shadowXl,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    decoration: BoxDecoration(
                      border: Border(bottom: BorderSide(color: t.border)),
                    ),
                    child: Row(
                      spacing: 10,
                      children: <Widget>[
                        Icon(
                          LucideIcons.search,
                          size: 16,
                          color: t.contentMuted,
                        ),
                        Expanded(
                          child: TextField(
                            controller: _query,
                            autofocus: true,
                            onChanged: (_) => setState(() => _selected = 0),
                            onSubmitted: (_) {
                              if (flat.isNotEmpty) flat[selected].run();
                            },
                            style: TextStyle(fontSize: 14, color: t.content),
                            decoration: InputDecoration(
                              isDense: true,
                              filled: false,
                              border: InputBorder.none,
                              enabledBorder: InputBorder.none,
                              focusedBorder: InputBorder.none,
                              contentPadding: const EdgeInsets.symmetric(
                                vertical: 15,
                              ),
                              hintText: 'Search or run a command…',
                              hintStyle: TextStyle(
                                fontSize: 14,
                                color: t.contentMuted,
                              ),
                            ),
                          ),
                        ),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 6,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(Radii.xs),
                            border: Border.all(color: t.border),
                          ),
                          child: Text(
                            'ESC',
                            style: TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.w500,
                              color: t.contentMuted,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  ConstrainedBox(
                    constraints: BoxConstraints(
                      maxHeight: MediaQuery.sizeOf(context).height * 0.52,
                    ),
                    child: flat.isEmpty
                        ? Padding(
                            padding: const EdgeInsets.symmetric(vertical: 32),
                            child: Text(
                              'No results found.',
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                fontSize: 13,
                                color: t.contentMuted,
                              ),
                            ),
                          )
                        : SingleChildScrollView(
                            padding: const EdgeInsets.all(8),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: <Widget>[
                                for (final _CommandGroup group
                                    in filtered) ...<Widget>[
                                  Padding(
                                    padding: const EdgeInsets.only(
                                      left: 8,
                                      top: 8,
                                      bottom: 4,
                                    ),
                                    child: Text(
                                      group.heading.toUpperCase(),
                                      style: TextStyle(
                                        fontSize: 10,
                                        fontWeight: FontWeight.w600,
                                        letterSpacing: 0.8,
                                        color: t.contentMuted,
                                      ),
                                    ),
                                  ),
                                  for (final _Command command in group.commands)
                                    _CommandRow(
                                      command: command,
                                      selected:
                                          flat.indexOf(command) == selected,
                                      onHover: () => setState(
                                        () => _selected = flat.indexOf(command),
                                      ),
                                    ),
                                ],
                              ],
                            ),
                          ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _CommandRow extends StatelessWidget {
  const _CommandRow({
    required this.command,
    required this.selected,
    required this.onHover,
  });

  final _Command command;
  final bool selected;
  final VoidCallback onHover;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => onHover(),
      child: GestureDetector(
        onTap: command.run,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(
            color: selected ? t.surfaceHover : Colors.transparent,
            borderRadius: BorderRadius.circular(Radii.lg),
          ),
          child: Row(
            spacing: 10,
            children: <Widget>[
              Icon(
                command.icon,
                size: 16,
                color: selected ? t.content : t.contentSecondary,
              ),
              Expanded(
                child: Text(
                  command.label,
                  style: TextStyle(
                    fontSize: 13,
                    color: selected ? t.content : t.contentSecondary,
                  ),
                ),
              ),
              if (command.hint != null)
                Text(
                  command.hint!,
                  style: TextStyle(fontSize: 11, color: t.contentMuted),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
