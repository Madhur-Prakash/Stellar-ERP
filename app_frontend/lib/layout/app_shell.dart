import 'dart:ui' show ImageFilter, PathMetric;

import 'package:flutter/foundation.dart' show defaultTargetPlatform;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../core/api_error.dart';
import '../models/auth.dart';
import '../state/auth_controller.dart';
import '../theme/oklch.dart';
import '../theme/tokens.dart';
import '../widgets/app_badge.dart';
import '../widgets/app_button.dart';
import '../widgets/primitives.dart';
import '../widgets/toast.dart';
import 'command_palette.dart';
import 'footer.dart';
import 'nav.dart';
import 'theme_toggle.dart';

/// The authenticated shell: sidebar, header, scrolling content, footer.
///
/// The web app pins a 248px sidebar and offsets the content by the same amount, with
/// a sticky 56px glass header. Reproduced here as a `Row` rather than a Material
/// `NavigationRail`, because a rail is a fixed-width strip of icons with an optional
/// label - it has no notion of section headings, permission-filtered groups, a
/// "coming soon" row, or an organization switcher pinned above the list, and all four
/// are load-bearing here.
///
/// **The drawer behaviour is kept even though this is a desktop app.** The 1024px
/// breakpoint is not about phones: a desktop window dragged to half a 1080p screen is
/// 960px wide, and at that width a 248px sidebar plus a data table is cramped. So the
/// same rule applies - persistent above the breakpoint, a drawer with a scrim below it.
class AppShell extends ConsumerStatefulWidget {
  const AppShell({super.key, required this.child});

  final Widget child;

  /// `lg:` in the web app's class names.
  static const double desktopBreakpoint = 1024;
  static const double sidebarWidth = 248;
  static const double headerHeight = 56;

  @override
  ConsumerState<AppShell> createState() => _AppShellState();
}

class _AppShellState extends ConsumerState<AppShell> {
  bool _drawerOpen = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AuthState auth = ref.watch(authControllerProvider);
    final double width = MediaQuery.sizeOf(context).width;
    final bool wide = width >= AppShell.desktopBreakpoint;

    // The router's guard deliberately does nothing while the session restore is in
    // flight - redirecting on `!isAuthenticated` before we know would bounce a
    // signed-in user to the sign-in screen on every launch. The cost was that this
    // shell built anyway, so a first launch showed chrome and an empty dashboard
    // before landing on sign-in, which reads as the app having loaded and then
    // thrown the user out. A window with no address bar makes that worse, not
    // better: there is nothing else on screen to explain what happened.
    //
    // So the protected shell shows nothing of itself until the answer is known.
    // The sign-in screens are unaffected; they are public and build immediately.
    if (auth.isLoading) {
      return Scaffold(
        backgroundColor: t.canvas,
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            spacing: 12,
            children: <Widget>[
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: t.primary,
                  borderRadius: BorderRadius.circular(Radii.xl),
                ),
                alignment: Alignment.center,
                child: Text(
                  'E',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: t.primaryContent,
                    height: 1,
                  ),
                ),
              ),
              Text(
                'Signing you in…',
                style: TextStyle(fontSize: 13, color: t.contentMuted),
              ),
            ],
          ),
        ),
      );
    }

    // Cmd/Ctrl+K opens the palette from anywhere. Registered on the shell rather than
    // per-screen so it works regardless of which route is showing, and on both
    // modifiers because a Mac user presses ⌘ and everyone else presses Ctrl.
    return CallbackShortcuts(
      bindings: <ShortcutActivator, VoidCallback>{
        const SingleActivator(LogicalKeyboardKey.keyK, control: true): () =>
            showCommandPalette(context, ref),
        const SingleActivator(LogicalKeyboardKey.keyK, meta: true): () =>
            showCommandPalette(context, ref),
      },
      child: Focus(
        autofocus: true,
        child: Scaffold(
          backgroundColor: t.canvas,
          body: Stack(
            children: <Widget>[
              Row(
                children: <Widget>[
                  if (wide) _Sidebar(auth: auth, onNavigate: () {}),
                  Expanded(
                    child: Column(
                      children: <Widget>[
                        _Header(
                          showMenu: !wide,
                          onMenu: () => setState(() => _drawerOpen = true),
                        ),
                        Expanded(
                          child: SingleChildScrollView(
                            child: Column(
                              children: <Widget>[
                                // Padding lives here, once, rather than in each
                                // screen. Half the routes would set their own and
                                // half would set none, so screens were inset
                                // inconsistently - and none had bottom padding,
                                // which is why the last row of a long table sat
                                // flush against the viewport edge and looked cut off.
                                Padding(
                                  padding: EdgeInsets.fromLTRB(
                                    wide ? 32 : 24,
                                    wide ? 32 : 24,
                                    wide ? 32 : 24,
                                    wide ? 48 : 40,
                                  ),
                                  child: widget.child,
                                ),
                                const AppFooter(),
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),

              // The drawer and its scrim, below the breakpoint.
              if (!wide && _drawerOpen) ...<Widget>[
                Positioned.fill(
                  child: GestureDetector(
                    onTap: () => setState(() => _drawerOpen = false),
                    child: ColoredBox(
                      color: Colors.black.withValues(alpha: 0.4),
                    ),
                  ),
                ),
                Positioned(
                  left: 0,
                  top: 0,
                  bottom: 0,
                  child: _Sidebar(
                    auth: auth,
                    showClose: true,
                    onClose: () => setState(() => _drawerOpen = false),
                    onNavigate: () => setState(() => _drawerOpen = false),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

// =============================================================================
// Sidebar
// =============================================================================
class _Sidebar extends ConsumerStatefulWidget {
  const _Sidebar({
    required this.auth,
    required this.onNavigate,
    this.showClose = false,
    this.onClose,
  });

  final AuthState auth;

  /// Any click inside the nav closes the drawer, which otherwise covers the screen
  /// just navigated to. Handled by passing the callback down rather than reacting to
  /// the route in an effect - setting state after a route change is an extra frame.
  final VoidCallback onNavigate;

  final bool showClose;
  final VoidCallback? onClose;

  @override
  ConsumerState<_Sidebar> createState() => _SidebarState();
}

/// Stateful only to own the hover, which has to live *above* the rows rather than inside
/// each one.
///
/// **Two rows could otherwise be shaded at once, and were.** The obvious spelling gives
/// every row its own `bool _hovered` flipped by its own `MouseRegion`, which is correct
/// only while every `onExit` arrives. It does not always: Flutter resolves mouse regions
/// per frame, so a pointer sweeping down a list two pixels apart, a rebuild that moves a
/// row mid-gesture, or a pointer leaving the window can drop one - and a row whose exit
/// went missing stays shaded while the next one lights up, one of them nowhere near the
/// pointer.
///
/// A single path on the parent makes that unrepresentable: entering any row overwrites
/// the one before it, so a missed exit self-corrects on the very next enter instead of
/// persisting until something rebuilds.
class _SidebarState extends ConsumerState<_Sidebar> {
  /// The path of the row under the pointer, or null.
  String? _hoveredPath;

  /// Sentinel for the menu's create entry, which is not an organization id.
  static const String _createOrganizationValue = '__create_organization__';

  Future<void> _chooseOrganization(String value) async {
    if (value == _createOrganizationValue) {
      widget.onNavigate();
      // `create=1` is what makes Settings scroll its create card into view. The
      // card sits below profile, security and appearance, so landing at the top
      // of the page hides the one thing the tap asked for.
      context.go('/settings?create=1');
      return;
    }
    if (value == widget.auth.organization?.id) return;

    final ToastScopeState toasts = ToastScope.of(context);
    try {
      await ref.read(authControllerProvider.notifier).switchOrganization(value);
    } catch (error) {
      toasts.show(
        ToastData(message: ApiError.from(error).message, tone: ToastTone.error),
      );
    }
  }

  void _setHovered(String path, bool entered) {
    if (entered) {
      if (_hoveredPath != path) setState(() => _hoveredPath = path);
      return;
    }
    // Guarded, not unconditional. Flutter can deliver the next row's `onEnter` *before*
    // this row's `onExit`; clearing blindly would then wipe the hover that had just
    // arrived and leave the sidebar with nothing shaded.
    if (_hoveredPath == path) setState(() => _hoveredPath = null);
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AuthState auth = widget.auth;
    final VoidCallback onNavigate = widget.onNavigate;
    final bool showClose = widget.showClose;
    final VoidCallback? onClose = widget.onClose;
    final AuthenticatedUser? user = auth.user;
    final OrganizationSummary? organization = auth.organization;
    final String location = GoRouterState.of(context).matchedLocation;

    return Container(
      width: AppShell.sidebarWidth,
      decoration: BoxDecoration(
        color: t.surface,
        border: Border(right: BorderSide(color: t.border)),
      ),
      child: Column(
        children: <Widget>[
          // Brand row, at the header's height so the two line up across the divider.
          SizedBox(
            height: AppShell.headerHeight,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: MouseRegion(
                      cursor: SystemMouseCursors.click,
                      child: GestureDetector(
                        onTap: () {
                          onNavigate();
                          context.go('/');
                        },
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          spacing: 8,
                          children: <Widget>[
                            Container(
                              width: 28,
                              height: 28,
                              decoration: BoxDecoration(
                                color: t.primary,
                                borderRadius: BorderRadius.circular(Radii.lg),
                              ),
                              alignment: Alignment.center,
                              child: Text(
                                'E',
                                style: TextStyle(
                                  fontSize: 14,
                                  fontWeight: FontWeight.w700,
                                  color: t.primaryContent,
                                  height: 1,
                                ),
                              ),
                            ),
                            Text(
                              'Stellar ERP',
                              style: TextStyle(
                                fontSize: 15,
                                fontWeight: FontWeight.w600,
                                letterSpacing: -0.3,
                                color: t.content,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  if (showClose)
                    AppIconButton(
                      icon: LucideIcons.x,
                      tooltip: 'Close navigation',
                      onPressed: onClose,
                    ),
                ],
              ),
            ),
          ),

          // Organization switcher. A real one: it lists every organization the
          // session carries and switches on selection, rather than carrying a
          // chevron and navigating to Settings - which is the shape of a dropdown
          // making a promise it did not keep. The memberships were already in the
          // session and `switchOrganization` was already wired for the command
          // palette, which you have to know exists to find.
          //
          // Rendered with no active organization too. That case is not
          // hypothetical: a suspended member is excluded from the switcher by
          // design, and someone who registered without a company never had one -
          // so hiding the control removes the only signpost from exactly the
          // people with nowhere to go.
          Padding(
            padding: const EdgeInsets.only(left: 12, right: 12, bottom: 12),
            child: PopupMenuButton<String>(
              tooltip: 'Switch organization',
              position: PopupMenuPosition.under,
              color: t.surfaceRaised,
              onSelected: (String value) => _chooseOrganization(value),
              itemBuilder: (BuildContext context) => <PopupMenuEntry<String>>[
                for (final OrganizationSummary item
                    in widget.auth.user?.organizations ??
                        const <OrganizationSummary>[])
                  PopupMenuItem<String>(
                    value: item.id,
                    child: Row(
                      spacing: 10,
                      children: <Widget>[
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text(
                                item.name,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                  fontSize: 13,
                                  color: t.content,
                                ),
                              ),
                              Text(
                                item.roleName,
                                style: TextStyle(
                                  fontSize: 11,
                                  color: t.contentMuted,
                                ),
                              ),
                            ],
                          ),
                        ),
                        if (item.id == organization?.id)
                          Icon(LucideIcons.check, size: 14, color: t.primary),
                      ],
                    ),
                  ),
                const PopupMenuDivider(),
                PopupMenuItem<String>(
                  value: _createOrganizationValue,
                  child: Row(
                    spacing: 10,
                    children: <Widget>[
                      Icon(LucideIcons.plus, size: 14, color: t.contentMuted),
                      Text(
                        organization == null
                            ? 'Create an organization'
                            : 'Create another organization',
                        style: TextStyle(fontSize: 13, color: t.content),
                      ),
                    ],
                  ),
                ),
              ],
              child: _OrganizationRow(organization: organization),
            ),
          ),

          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.only(left: 12, right: 12, bottom: 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                spacing: 20,
                children: <Widget>[
                  for (final NavSection section in navSections)
                    if (_visible(section).isNotEmpty)
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Padding(
                            padding: const EdgeInsets.only(left: 10, bottom: 6),
                            child: Text(
                              section.title.toUpperCase(),
                              style: TextStyle(
                                fontSize: 10,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 0.8,
                                color: t.contentMuted,
                              ),
                            ),
                          ),
                          for (final NavItem item in _visible(section))
                            _NavRow(
                              item: item,
                              active: _isActive(item, location),
                              // Keyed on the path, which is what makes a row unique -
                              // two sections can carry the same label.
                              hovered: _hoveredPath == item.path,
                              onHover: (bool entered) =>
                                  _setHovered(item.path, entered),
                              onTap: () {
                                onNavigate();
                                context.go(item.path);
                              },
                            ),
                        ],
                      ),
                ],
              ),
            ),
          ),

          // User.
          if (user != null)
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                border: Border(top: BorderSide(color: t.border)),
              ),
              child: Row(
                spacing: 10,
                children: <Widget>[
                  AppAvatar(
                    src: user.avatarUrl,
                    name: user.fullName,
                    initials: user.initials,
                  ),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          user.fullName,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w500,
                            color: t.content,
                          ),
                        ),
                        Text(
                          user.email,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(fontSize: 11, color: t.contentMuted),
                        ),
                      ],
                    ),
                  ),
                  AppIconButton(
                    icon: LucideIcons.logOut,
                    tooltip: 'Sign out',
                    onPressed: () =>
                        ref.read(authControllerProvider.notifier).signOut(),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  List<NavItem> _visible(NavSection section) => section.items
      .where(
        (NavItem item) =>
            item.permission == null || widget.auth.can(item.permission!),
      )
      .toList(growable: false);

  /// The dashboard matches only its exact path; everything else matches a prefix, so
  /// `/invoices?tab=customers` still highlights Sales.
  bool _isActive(NavItem item, String location) =>
      item.path == '/' ? location == '/' : location.startsWith(item.path);
}

/// The switcher's face. Nullable, because a user can legitimately have no active
/// organization - suspended, or registered without a company - and that is exactly
/// when they most need the control that leads somewhere.
class _OrganizationRow extends StatefulWidget {
  const _OrganizationRow({required this.organization});

  final OrganizationSummary? organization;

  @override
  State<_OrganizationRow> createState() => _OrganizationRowState();
}

class _OrganizationRowState extends State<_OrganizationRow> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final OrganizationSummary? organization = widget.organization;
    final String name = organization?.name ?? 'No organization';
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: Builder(
        builder: (BuildContext context) => AnimatedContainer(
          duration: Motion.fast,
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(
            // Zero-alpha hover grey, not `Colors.transparent` - see the note in
            // `_NavRow` on why the lerp otherwise flashes dark mid-fade.
            color: _hovered ? t.surfaceHover : t.surfaceHover.at(0),
            borderRadius: BorderRadius.circular(Radii.lg),
            border: Border.all(color: t.border),
          ),
          child: Row(
            spacing: 10,
            children: <Widget>[
              Container(
                width: 28,
                height: 28,
                decoration: BoxDecoration(
                  color: t.primary.at(0.12),
                  borderRadius: BorderRadius.circular(Radii.md),
                ),
                alignment: Alignment.center,
                child: Text(
                  name.substring(0, name.length >= 2 ? 2 : 1).toUpperCase(),
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: t.primary,
                    height: 1,
                  ),
                ),
              ),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      name,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                        color: t.content,
                      ),
                    ),
                    Text(
                      organization?.roleName ?? 'Create or join one',
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 11, color: t.contentMuted),
                    ),
                  ],
                ),
              ),
              Icon(LucideIcons.chevronDown, size: 14, color: t.contentMuted),
            ],
          ),
        ),
      ),
    );
  }
}

/// Stateless: the hover belongs to [_SidebarState], which can only hold one row at a
/// time. See the note there for why a bool per row was not enough.
class _NavRow extends StatelessWidget {
  const _NavRow({
    required this.item,
    required this.active,
    required this.hovered,
    required this.onHover,
    required this.onTap,
  });

  final NavItem item;
  final bool active;
  final bool hovered;
  final ValueChanged<bool> onHover;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    // Not yet built. A disabled row is more honest than a link to a 404 - but the
    // badge has to say so in words. "S6" is an internal build-order number that means
    // nothing to whoever is using this, and on a greyed-out row it read as an error
    // code.
    if (!item.isBuilt) {
      return Tooltip(
        message:
            '${item.label} is not built yet. It arrives in a later update.',
        child: Opacity(
          opacity: 0.55,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            child: Row(
              spacing: 10,
              children: <Widget>[
                Icon(item.icon, size: 16, color: t.contentMuted),
                Expanded(
                  child: Text(
                    item.label,
                    style: TextStyle(fontSize: 13, color: t.contentMuted),
                  ),
                ),
                const AppBadge('Coming soon'),
              ],
            ),
          ),
        ),
      );
    }

    // `surfaceHover.at(0)`, never `Colors.transparent`.
    //
    // **`Colors.transparent` is transparent *black* (`0x00000000`), and an
    // `AnimatedContainer` lerps every channel.** Fading to a near-white hover grey
    // therefore drags red, green and blue up from zero: halfway through the 120 ms the
    // row paints rgb(188,188,189) - a solid mid-grey, far darker than either end - and
    // only then settles onto the barely-there rgb(245,245,246). Hovering flashed dark
    // and then dropped its colour, which is exactly backwards from what the fade is for.
    //
    // The same colour at zero alpha leaves the RGB fixed and animates opacity alone, so
    // the row washes in at its final hue and stays there.
    final Color background = active
        ? t.primary.at(0.10)
        : hovered
        ? t.surfaceHover
        : t.surfaceHover.at(0);
    final Color foreground = active
        ? t.primary
        : hovered
        ? t.content
        : t.contentSecondary;

    return Semantics(
      selected: active,
      button: true,
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => onHover(true),
        onExit: (_) => onHover(false),
        child: GestureDetector(
          onTap: onTap,
          child: AnimatedContainer(
            // Fades in, clears instantly - and the asymmetry is the point.
            //
            // A symmetric 120 ms cross-fade means the row you just left is still
            // painting its background while the row you entered paints its own. Sweep
            // the pointer down the list and two or three rows are shaded at any instant:
            // a comet tail behind the cursor that looks exactly like several rows being
            // selected at once. It needs no dropped event to happen - it is just the
            // exit animation outliving the exit.
            //
            // Zero on the way out means at most one row is ever shaded. The active row
            // keeps its animation because it is not chasing a pointer: it changes on
            // navigation, where a fade reads as deliberate rather than as lag.
            duration: active || hovered ? Motion.fast : Duration.zero,
            margin: const EdgeInsets.only(bottom: 2),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(
              color: background,
              borderRadius: BorderRadius.circular(Radii.lg),
            ),
            child: Row(
              spacing: 10,
              children: <Widget>[
                Icon(item.icon, size: 16, color: foreground),
                Text(
                  item.label,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                    color: foreground,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// =============================================================================
// Header
// =============================================================================
class _Header extends ConsumerWidget {
  const _Header({required this.showMenu, required this.onMenu});

  final bool showMenu;
  final VoidCallback onMenu;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final double width = MediaQuery.sizeOf(context).width;

    // `glass`: a translucent fill over a 12px blur. Restrained on purpose - heavy
    // blur over dense tables hurts legibility, so it is used only on floating chrome.
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          height: AppShell.headerHeight,
          padding: EdgeInsets.symmetric(
            horizontal: width >= AppShell.desktopBreakpoint ? 24 : 16,
          ),
          decoration: BoxDecoration(
            color: t.glassBg,
            border: Border(bottom: BorderSide(color: t.border)),
          ),
          child: Row(
            spacing: 12,
            children: <Widget>[
              if (showMenu)
                AppIconButton(
                  icon: LucideIcons.menu,
                  tooltip: 'Open navigation',
                  onPressed: onMenu,
                ),
              // Opens the palette. A button rather than a real text field: it is a
              // launcher, and a focusable input here would swallow keystrokes meant
              // for the screen behind it.
              _SearchLauncher(onTap: () => showCommandPalette(context, ref)),
              const Spacer(),
              AppIconButton(
                icon: LucideIcons.bell,
                tooltip: 'Notifications',
                onPressed: null,
              ),
              const ThemeToggle(),
            ],
          ),
        ),
      ),
    );
  }
}

class _SearchLauncher extends StatefulWidget {
  const _SearchLauncher({required this.onTap});

  final VoidCallback onTap;

  @override
  State<_SearchLauncher> createState() => _SearchLauncherState();
}

class _SearchLauncherState extends State<_SearchLauncher> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: Motion.fast,
          width: 288,
          height: 32,
          padding: const EdgeInsets.symmetric(horizontal: 10),
          decoration: BoxDecoration(
            color: _hovered ? t.surfaceHover : t.surfaceSunken,
            borderRadius: BorderRadius.circular(Radii.lg),
            border: Border.all(color: t.border),
          ),
          child: Row(
            spacing: 8,
            children: <Widget>[
              Icon(LucideIcons.search, size: 14, color: t.contentMuted),
              Expanded(
                child: Text(
                  'Search or jump to…',
                  style: TextStyle(
                    fontSize: 13,
                    color: _hovered ? t.contentSecondary : t.contentMuted,
                  ),
                ),
              ),
              KeyHint(label: shortcutModifier == '⌘' ? '⌘K' : 'Ctrl K'),
            ],
          ),
        ),
      ),
    );
  }
}

/// A keycap, for the shortcut hints in the header and the footer.
class KeyHint extends StatelessWidget {
  const KeyHint({super.key, required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: t.surface,
        borderRadius: BorderRadius.circular(Radii.xs),
        border: Border.all(color: t.border),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w500,
          color: t.contentMuted,
          height: 1.4,
        ),
      ),
    );
  }
}

/// The modifier this machine actually uses for the command palette.
///
/// The shortcut handler accepts either, so both work everywhere - but a hint has to
/// name one, and telling a Mac user to press Ctrl when everything else on their
/// system is ⌘ makes the hint read as untrustworthy.
final String shortcutModifier = defaultTargetPlatform == TargetPlatform.macOS
    ? '⌘'
    : 'Ctrl';

/// A module a later stage delivers.
class StagePlaceholder extends StatelessWidget {
  const StagePlaceholder({
    super.key,
    required this.title,
    required this.description,
    required this.stage,
  });

  final String title;
  final String description;
  final int stage;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        PageHeader(title: title, description: description),
        CustomPaint(
          painter: _DashedBoxPainter(colour: t.border),
          child: EmptyState(
            icon: LucideIcons.building2,
            title: 'Coming soon',
            description:
                '$title is not built yet. Nothing else in the app is waiting on it - '
                'your books, reports and records all work without it.',
            verticalPadding: 80,
            // The stage number stays, quietly: useful to whoever is building this,
            // meaningless to whoever is using it, so it belongs in the small print
            // and not the heading.
            action: Text(
              'Planned for stage $stage',
              style: TextStyle(fontSize: 11, color: t.contentMuted),
            ),
          ),
        ),
      ],
    );
  }
}

class _DashedBoxPainter extends CustomPainter {
  const _DashedBoxPainter({required this.colour});

  final Color colour;

  @override
  void paint(Canvas canvas, Size size) {
    final Paint paint = Paint()
      ..color = colour
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    final RRect box = RRect.fromRectAndRadius(
      Rect.fromLTWH(0.5, 0.5, size.width - 1, size.height - 1),
      const Radius.circular(Radii.xl),
    );
    final Path path = Path()..addRRect(box);
    for (final PathMetric metric in path.computeMetrics()) {
      double distance = 0;
      while (distance < metric.length) {
        final double end = (distance + 6).clamp(0, metric.length);
        canvas.drawPath(metric.extractPath(distance, end), paint);
        distance = end + 4;
      }
    }
  }

  @override
  bool shouldRepaint(_DashedBoxPainter old) => old.colour != colour;
}
