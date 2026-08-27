import 'package:flutter/material.dart';

import '../core/format.dart';
import '../theme/oklch.dart';
import '../theme/tokens.dart';

/// The smaller design-system pieces: avatar, skeleton, empty state, page header,
/// tabs, the segmented filter, and the checkbox row.
///
/// Grouped in one file because each is a few dozen lines and they are almost always
/// imported together; splitting them would be seven imports on every screen.

// =============================================================================
// Avatar
// =============================================================================
enum AvatarSize { xs, sm, md, lg, xl }

class AppAvatar extends StatefulWidget {
  const AppAvatar({
    super.key,
    this.src,
    required this.name,
    this.initials,
    this.size = AvatarSize.md,
  });

  final String? src;
  final String name;

  /// Server-computed initials; derived from [name] when absent.
  final String? initials;
  final AvatarSize size;

  @override
  State<AppAvatar> createState() => _AppAvatarState();
}

class _AppAvatarState extends State<AppAvatar> {
  bool _failed = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final (double box, double fontSize) = switch (widget.size) {
      AvatarSize.xs => (24, 10),
      AvatarSize.sm => (28, 11),
      AvatarSize.md => (32, 12),
      AvatarSize.lg => (40, 14),
      AvatarSize.xl => (56, 16),
    };

    final String fallback = widget.initials?.isNotEmpty == true
        ? widget.initials!
        : initialsOf(widget.name);

    final bool showImage =
        widget.src != null && widget.src!.isNotEmpty && !_failed;

    return Container(
      width: box,
      height: box,
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: t.primary.at(0.12),
        shape: BoxShape.circle,
      ),
      alignment: Alignment.center,
      child: showImage
          ? Image.network(
              widget.src!,
              width: box,
              height: box,
              fit: BoxFit.cover,
              // A broken URL would otherwise render Flutter's error box, which looks
              // like a rendering failure. Fall back to initials instead.
              errorBuilder: (_, _, _) {
                // Scheduled rather than set inline: `setState` during a build is an
                // error, and `errorBuilder` runs inside one.
                WidgetsBinding.instance.addPostFrameCallback((_) {
                  if (mounted) setState(() => _failed = true);
                });
                return _initials(fallback, fontSize, t);
              },
            )
          : _initials(fallback, fontSize, t),
    );
  }

  Widget _initials(String text, double fontSize, AppTokens t) {
    // Decorative: the accessible name comes from the surrounding control, so
    // announcing "PS" as well is just noise.
    return ExcludeSemantics(
      child: Text(
        text,
        style: TextStyle(
          fontSize: fontSize,
          fontWeight: FontWeight.w600,
          color: t.primary,
          height: 1,
        ),
      ),
    );
  }
}

// =============================================================================
// Skeleton
// =============================================================================
/// A shimmering placeholder.
///
/// Preferred over a spinner wherever the content has a known shape: it holds the
/// layout, so nothing jumps when the data arrives.
///
/// The gradient sweep is the CSS `@keyframes shimmer` - a 200%-wide gradient slid
/// across the box over 1.6s.
class Skeleton extends StatefulWidget {
  const Skeleton({
    super.key,
    this.width,
    this.height = 14,
    this.radius = Radii.md,
  });

  final double? width;
  final double height;
  final double radius;

  @override
  State<Skeleton> createState() => _SkeletonState();
}

class _SkeletonState extends State<Skeleton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return ExcludeSemantics(
      child: AnimatedBuilder(
        animation: _controller,
        builder: (BuildContext context, Widget? child) {
          final double progress = _controller.value;
          return Container(
            width: widget.width,
            height: widget.height,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(widget.radius),
              gradient: LinearGradient(
                // Swept from right to left, as `background-position: 200% -> -200%`.
                begin: Alignment(-1 - 2 * (1 - progress), 0),
                end: Alignment(1 + 2 * progress, 0),
                colors: <Color>[
                  t.surfaceSunken,
                  t.surfaceHover,
                  t.surfaceSunken,
                ],
                stops: const <double>[0, 0.5, 1],
              ),
            ),
          );
        },
      ),
    );
  }
}

class SkeletonText extends StatelessWidget {
  const SkeletonText({super.key, this.lines = 3});

  final int lines;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 8,
      children: <Widget>[
        for (int index = 0; index < lines; index++)
          FractionallySizedBox(
            alignment: Alignment.centerLeft,
            widthFactor: index == lines - 1 ? 0.66 : 1,
            child: const Skeleton(height: 14),
          ),
      ],
    );
  }
}

/// Full-page loading state, used while the session bootstraps.
class PageSkeleton extends StatelessWidget {
  const PageSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Loading',
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          spacing: 24,
          children: <Widget>[
            const Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 8,
              children: <Widget>[
                Skeleton(width: 224, height: 28),
                Skeleton(width: 320, height: 16),
              ],
            ),
            Row(
              spacing: 16,
              children: <Widget>[
                for (int index = 0; index < 4; index++)
                  const Expanded(
                    child: Skeleton(height: 112, radius: Radii.xl),
                  ),
              ],
            ),
            const Skeleton(height: 288, radius: Radii.xl),
          ],
        ),
      ),
    );
  }
}

// =============================================================================
// Empty state
// =============================================================================
/// The empty state for a list or table.
///
/// A real component, not an afterthought, because "no data" is the *first* thing every
/// user of a new ERP sees. It should explain what belongs here and offer the action
/// that creates it - an empty grid with no explanation reads as a bug.
class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    this.icon,
    required this.title,
    this.description,
    this.action,
    this.verticalPadding = 56,
  });

  final IconData? icon;
  final String title;
  final String? description;
  final Widget? action;
  final double verticalPadding;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: 24, vertical: verticalPadding),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: <Widget>[
          if (icon != null) ...<Widget>[
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                color: t.surfaceSunken,
                borderRadius: BorderRadius.circular(Radii.xl),
              ),
              alignment: Alignment.center,
              child: Icon(icon, size: 20, color: t.contentMuted),
            ),
            const SizedBox(height: 16),
          ],
          Text(
            title,
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: t.content,
            ),
          ),
          if (description != null) ...<Widget>[
            const SizedBox(height: 6),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 384),
              child: Text(
                description!,
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: 13,
                  color: t.contentMuted,
                  height: 1.6,
                ),
              ),
            ),
          ],
          if (action != null) ...<Widget>[const SizedBox(height: 20), action!],
        ],
      ),
    );
  }
}

// =============================================================================
// Page header
// =============================================================================
/// Consistent page heading, used by every route inside the shell.
class PageHeader extends StatelessWidget {
  const PageHeader({
    super.key,
    required this.title,
    this.description,
    this.action,
  });

  final String title;
  final String? description;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Padding(
      padding: const EdgeInsets.only(bottom: 24),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 16,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  title,
                  style: TextStyle(
                    fontSize: 22,
                    height: 1.2,
                    fontWeight: FontWeight.w600,
                    letterSpacing: -0.55,
                    color: t.content,
                  ),
                ),
                if (description != null) ...<Widget>[
                  const SizedBox(height: 4),
                  Text(
                    description!,
                    style: TextStyle(
                      fontSize: 13,
                      color: t.contentMuted,
                      height: 1.5,
                    ),
                  ),
                ],
              ],
            ),
          ),
          ?action,
        ],
      ),
    );
  }
}

// =============================================================================
// Tabs
// =============================================================================
/// The underlined tab strip used by Accounting, Sales, and Inventory.
///
/// Scrolls horizontally rather than wrapping: six tabs on a narrow window would
/// otherwise become two rows and shift the whole page down.
class AppTabs extends StatelessWidget {
  const AppTabs({
    super.key,
    required this.tabs,
    required this.active,
    required this.onChanged,
    this.semanticLabel,
  });

  /// Value and label, in display order.
  final List<(String, String)> tabs;
  final String active;
  final ValueChanged<String> onChanged;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Semantics(
      label: semanticLabel,
      child: Container(
        margin: const EdgeInsets.only(bottom: 16),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: t.border)),
        ),
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            spacing: 4,
            children: <Widget>[
              for (final (String value, String label) in tabs)
                _Tab(
                  label: label,
                  selected: value == active,
                  onTap: () => onChanged(value),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Tab extends StatefulWidget {
  const _Tab({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  State<_Tab> createState() => _TabState();
}

class _TabState extends State<_Tab> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final Color colour = widget.selected
        ? t.content
        : _hovered
        ? t.content
        : t.contentMuted;

    return Semantics(
      selected: widget.selected,
      button: true,
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: GestureDetector(
          onTap: widget.onTap,
          child: AnimatedContainer(
            duration: Motion.fast,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
            decoration: BoxDecoration(
              border: Border(
                bottom: BorderSide(
                  // `primary.at(0)`, not `Colors.transparent`: that constant is
                  // transparent *black*, and the `AnimatedContainer` lerps every
                  // channel - so the rule would darken its way up from black before
                  // arriving at the accent colour. Same colour, zero alpha, and only
                  // the opacity travels.
                  color: widget.selected ? t.primary : t.primary.at(0),
                  width: 2,
                ),
              ),
            ),
            child: Text(
              widget.label,
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w500,
                color: colour,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// =============================================================================
// Segmented control
// =============================================================================
/// The bordered button group - Billing's All/In/Out filter, and the report presets.
///
/// One rounded box with hairline dividers, so it reads as a single control with a
/// selected segment rather than as three buttons that happen to be adjacent.
class Segmented extends StatelessWidget {
  const Segmented({
    super.key,
    required this.segments,
    required this.active,
    required this.onChanged,
  });

  /// Value, label, and an optional tooltip - the fiscal-year presets need one to
  /// explain what "FY 2026-27" covers.
  final List<(String value, String label, String? tooltip)> segments;
  final String active;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(Radii.lg),
        border: Border.all(color: t.border),
      ),
      clipBehavior: Clip.antiAlias,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          for (final (String value, String label, String? tooltip) in segments)
            _Segment(
              label: label,
              tooltip: tooltip,
              selected: value == active,
              onTap: () => onChanged(value),
            ),
        ],
      ),
    );
  }
}

class _Segment extends StatefulWidget {
  const _Segment({
    required this.label,
    required this.selected,
    required this.onTap,
    this.tooltip,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final String? tooltip;

  @override
  State<_Segment> createState() => _SegmentState();
}

class _SegmentState extends State<_Segment> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    Widget segment = MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          color: widget.selected
              ? t.primary
              : _hovered
              ? t.surfaceSunken
              : Colors.transparent,
          child: Text(
            widget.label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w500,
              // White rather than `primaryContent`: the web app hardcodes
              // `text-white` on the selected segment, and on the light theme those
              // are the same value anyway.
              color: widget.selected ? Colors.white : t.contentMuted,
            ),
          ),
        ),
      ),
    );

    if (widget.tooltip != null) {
      segment = Tooltip(message: widget.tooltip!, child: segment);
    }

    return Semantics(selected: widget.selected, button: true, child: segment);
  }
}

// =============================================================================
// Checkbox row
// =============================================================================
/// A checkbox with a label beside it, at the web app's 14px size.
///
/// Material's checkbox is 18px inside a 48px tap target, which in a card header
/// beside a 13px label is comically large. Scaled down, with the tap target kept on
/// the whole row so it does not become fiddly to hit.
class CheckRow extends StatelessWidget {
  const CheckRow({
    super.key,
    required this.value,
    required this.onChanged,
    required this.label,
    this.fontSize = 13,
  });

  final bool value;
  final ValueChanged<bool> onChanged;
  final String label;
  final double fontSize;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: () => onChanged(!value),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          spacing: 8,
          children: <Widget>[
            SizedBox(
              width: 16,
              height: 16,
              child: Transform.scale(
                scale: 0.8,
                child: Checkbox(
                  value: value,
                  onChanged: (bool? next) => onChanged(next ?? false),
                ),
              ),
            ),
            Text(
              label,
              style: TextStyle(fontSize: fontSize, color: t.contentSecondary),
            ),
          ],
        ),
      ),
    );
  }
}

// =============================================================================
// Inline link
// =============================================================================
/// Text that navigates, in the brand colour, underlined on hover.
///
/// Hand-built rather than delegating to [AppButton] so it can sit inline at an
/// arbitrary size and weight - the footer's 12px links and the "+ Add category"
/// affordance in a field label both need that, and neither maps onto a button size.
class AppTextLink extends StatefulWidget {
  const AppTextLink({
    super.key,
    required this.label,
    required this.onTap,
    this.fontSize = 13,
    this.fontWeight = FontWeight.w500,
    this.colour,
    this.hoverColour,
    this.trailingIcon,
  });

  final String label;
  final VoidCallback onTap;

  /// A glyph after the label, for a link that leaves the application.
  ///
  /// Colour and blue text say "this is a link"; they say nothing about *where*.
  /// A desktop app has no address bar and no status bar, so without a marker the
  /// only way to discover that a link opens a browser is to click it - which, on
  /// this screen, is the difference between reading a figure and being handed to
  /// a block explorer. The web counterpart carries the same icon.
  final IconData? trailingIcon;
  final double fontSize;
  final FontWeight fontWeight;
  final Color? colour;

  /// Shift to this colour on hover, instead of underlining.
  ///
  /// **The web has two link treatments and this is which one you get.** A link already
  /// carrying the primary colour underlines (`hover:underline` on "Create an account"),
  /// while a muted one changes colour and stays undecorated - `hover:text-primary` on
  /// "Forgot password?", `hover:text-content` in the footer and on "Use a different
  /// email". Null gives the first, which is the right default: an underline is the only
  /// feedback available to a link that is already the accent colour.
  ///
  /// This used to underline unconditionally, so every muted link in the app answered a
  /// hover differently from its counterpart on the website.
  final Color? hoverColour;

  @override
  State<AppTextLink> createState() => _AppTextLinkState();
}

class _AppTextLinkState extends State<AppTextLink> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final Color base = widget.colour ?? context.tokens.primary;
    final Color? hoverColour = widget.hoverColour;
    final bool shiftsColour = hoverColour != null;
    final Color colour = _hovered && shiftsColour ? hoverColour : base;

    return Semantics(
      link: true,
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: GestureDetector(
          onTap: widget.onTap,
          // Animated for the colour case only, and at the same 120 ms the web spends on
          // `transition-colors`. An underline appearing over 120 ms reads as a smear
          // rather than as a state change, which is why the web transitions colour and
          // not `text-decoration` either.
          child: AnimatedDefaultTextStyle(
            duration: shiftsColour ? Motion.fast : Duration.zero,
            style: TextStyle(
              fontSize: widget.fontSize,
              fontWeight: widget.fontWeight,
              color: colour,
              decoration: _hovered && !shiftsColour
                  ? TextDecoration.underline
                  : null,
              decorationColor: colour,
            ),
            // A Row only when there is an icon: wrapping every link in one would
            // change how each sits in its parent, and most of them are inline in
            // running text where a Row's cross-axis stretch is wrong.
            child: widget.trailingIcon == null
                ? Text(widget.label)
                : Row(
                    mainAxisSize: MainAxisSize.min,
                    spacing: 4,
                    children: <Widget>[
                      Text(widget.label),
                      Icon(
                        widget.trailingIcon,
                        size: widget.fontSize - 1,
                        color: colour,
                      ),
                    ],
                  ),
          ),
        ),
      ),
    );
  }
}
