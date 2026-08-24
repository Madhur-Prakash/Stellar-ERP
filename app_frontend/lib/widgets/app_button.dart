import 'package:flutter/material.dart';

import '../theme/oklch.dart';
import '../theme/tokens.dart';

/// Button variants and sizes, ported from `components/ui/Button.tsx`.
///
/// A lookup table rather than a builder chain: the complete recipe for each variant
/// is visible in one place, which is what you want when working out why a button
/// looks wrong.
enum AppButtonVariant { primary, secondary, outline, ghost, destructive, link }

enum AppButtonSize { sm, md, lg, icon }

/// The application button.
///
/// Hand-built rather than a themed `FilledButton`, for a specific reason: Material's
/// buttons enforce a 48dp minimum tap target and their own internal padding, and this
/// UI is designed at 32/36/44px heights with a 13px label. Overriding a Material
/// button hard enough to reach those metrics leaves nothing of it, so what is kept
/// from Material is what Material is actually good at - the ink response, the focus
/// traversal, and the hover semantics.
///
/// Three behaviours are load-bearing rather than decorative:
///
/// * **`loading` also disables.** Without that a double-click fires the action
///   twice, which on a "post invoice" button means two ledger entries.
/// * **The press scale is 0.98**, matching `active:scale-[0.98]`. It is the only
///   physical feedback a flat button has.
/// * **A ghost or link button does not shift its own layout on hover**, because most
///   of them sit inside table rows where a size change would jitter the column.
class AppButton extends StatefulWidget {
  const AppButton({
    super.key,
    required this.onPressed,
    this.child,
    this.label,
    this.variant = AppButtonVariant.primary,
    this.size = AppButtonSize.md,
    this.loading = false,
    this.leftIcon,
    this.rightIcon,
    this.fullWidth = false,
    this.tooltip,
    this.semanticLabel,
  }) : assert(
         child != null || label != null || leftIcon != null,
         'A button needs a label, a child, or an icon',
       );

  /// Null disables the button, matching Flutter's own convention.
  final VoidCallback? onPressed;

  /// Arbitrary content. Most call sites want [label] instead.
  final Widget? child;
  final String? label;

  final AppButtonVariant variant;
  final AppButtonSize size;

  /// Shows a spinner and blocks interaction.
  final bool loading;

  final IconData? leftIcon;
  final IconData? rightIcon;
  final bool fullWidth;
  final String? tooltip;

  /// The accessible name when the button is icon-only.
  final String? semanticLabel;

  @override
  State<AppButton> createState() => _AppButtonState();
}

class _AppButtonState extends State<AppButton> {
  bool _hovered = false;
  bool _pressed = false;

  bool get _enabled => widget.onPressed != null && !widget.loading;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final _ButtonStyle style = _resolve(t);

    final double height = switch (widget.size) {
      AppButtonSize.sm => 32,
      AppButtonSize.md => 36,
      AppButtonSize.lg => 44,
      AppButtonSize.icon => 36,
    };
    final double horizontalPadding = switch (widget.size) {
      AppButtonSize.sm => 12,
      AppButtonSize.md => 16,
      AppButtonSize.lg => 24,
      AppButtonSize.icon => 0,
    };
    final double fontSize = switch (widget.size) {
      AppButtonSize.sm => 13,
      AppButtonSize.md => 14,
      AppButtonSize.lg => 15,
      AppButtonSize.icon => 14,
    };
    final double gap = widget.size == AppButtonSize.sm ? 6 : 8;
    final double radius = widget.size == AppButtonSize.lg ? Radii.lg : Radii.md;
    final double iconSize = widget.size == AppButtonSize.sm ? 14 : 16;

    // A link is inline text, so it takes no box, no height, and no padding.
    final bool isLink = widget.variant == AppButtonVariant.link;

    final List<Widget> content = <Widget>[
      if (widget.loading)
        SizedBox(
          width: iconSize,
          height: iconSize,
          child: CircularProgressIndicator(
            strokeWidth: 2,
            color: style.foreground,
          ),
        )
      else if (widget.leftIcon != null)
        Icon(widget.leftIcon, size: iconSize, color: style.foreground),
      if (widget.label != null)
        Text(
          widget.label!,
          style: TextStyle(
            fontSize: fontSize,
            fontWeight: FontWeight.w500,
            color: style.foreground,
            height: 1.2,
            decoration: isLink && _hovered ? TextDecoration.underline : null,
            decorationColor: style.foreground,
          ),
        ),
      if (widget.child != null)
        DefaultTextStyle.merge(
          style: TextStyle(
            fontSize: fontSize,
            fontWeight: FontWeight.w500,
            color: style.foreground,
          ),
          child: IconTheme.merge(
            data: IconThemeData(size: iconSize, color: style.foreground),
            child: widget.child!,
          ),
        ),
      if (!widget.loading && widget.rightIcon != null)
        Icon(widget.rightIcon, size: iconSize, color: style.foreground),
    ];

    Widget body = Row(
      mainAxisSize: widget.fullWidth ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      spacing: gap,
      children: content,
    );

    if (!isLink) {
      body = Container(
        height: height,
        width: widget.size == AppButtonSize.icon ? height : null,
        padding: EdgeInsets.symmetric(horizontal: horizontalPadding),
        decoration: BoxDecoration(
          color: style.background,
          borderRadius: BorderRadius.circular(radius),
          border: style.border == null
              ? null
              : Border.all(color: style.border!),
          boxShadow: style.shadow,
        ),
        alignment: Alignment.center,
        child: body,
      );
    }

    // `final`, and named separately from `button` below, because the focus ring is drawn by
    // a `Builder` whose closure runs *after* this method returns. A closure captures the
    // variable, not its value - so reusing one name here would have the Builder wrap
    // whatever `button` held by then, which is the Builder itself. That is an infinite
    // widget tree, and it presents as a stack overflow on the first frame rather than as
    // anything that points at this line.
    final Widget pressable = AnimatedOpacity(
      duration: Motion.fast,
      opacity: _enabled ? 1 : 0.5,
      child: AnimatedScale(
        // `active:scale-[0.98]`.
        scale: _pressed && _enabled ? 0.98 : 1,
        duration: Motion.fast,
        curve: Motion.easeOutQuart,
        child: body,
      ),
    );

    // Hover only. **There must be no second tap recognizer below the `InkWell`**, and
    // that is the whole reason the press-state callbacks live on the `InkWell` rather
    // than on a `GestureDetector` here, which is where they used to be.
    //
    // Two competing tap recognizers enter the same gesture arena, and the deeper one
    // wins. So a `GestureDetector` nested inside the `InkWell` - even one that only
    // declares `onTapDown`/`onTapUp` to animate the press - claimed every tap, and
    // `InkWell.onTap` never fired. `onPressed` was silently dead on every button in the
    // app: nothing logged, nothing threw, and the button still animated on press, which
    // is what made it look like the callbacks were the broken part.
    Widget button = MouseRegion(
      cursor: _enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() {
        _hovered = false;
        _pressed = false;
      }),
      child: Focus(
        canRequestFocus: _enabled,
        child: Builder(
          builder: (BuildContext context) {
            final bool focused = Focus.of(context).hasPrimaryFocus;
            return Container(
              decoration: focused
                  ? BoxDecoration(
                      // `focus-visible:ring-2 ring-offset-2` - drawn as an outer
                      // ring so it never eats into the button's own box.
                      borderRadius: BorderRadius.circular(radius + 2),
                      border: Border.all(color: t.ring, width: 2),
                    )
                  : null,
              padding: focused ? const EdgeInsets.all(2) : EdgeInsets.zero,
              child: pressable,
            );
          },
        ),
      ),
    );

    // `InkWell` rather than a bare gesture detector because it answers to Enter and
    // Space, which is how a keyboard user presses a button, and carries the activation
    // semantics with it. Under a transparent `Material` so none of Material's own
    // painting shows through.
    //
    // It owns *all* tap handling: the callback and the press animation both. One
    // recognizer means nothing can outrank it - see the note above.
    button = Semantics(
      button: true,
      enabled: _enabled,
      label: widget.semanticLabel,
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(radius),
        child: InkWell(
          onTap: _enabled ? widget.onPressed : null,
          onTapDown: _enabled ? (_) => setState(() => _pressed = true) : null,
          onTapUp: _enabled ? (_) => setState(() => _pressed = false) : null,
          onTapCancel: _enabled ? () => setState(() => _pressed = false) : null,
          borderRadius: BorderRadius.circular(radius),
          splashColor: isLink ? Colors.transparent : null,
          highlightColor: Colors.transparent,
          hoverColor: Colors.transparent,
          child: button,
        ),
      ),
    );

    if (widget.fullWidth) {
      button = SizedBox(width: double.infinity, child: button);
    }
    if (widget.tooltip != null) {
      button = Tooltip(message: widget.tooltip!, child: button);
    }

    return button;
  }

  _ButtonStyle _resolve(AppTokens t) {
    switch (widget.variant) {
      case AppButtonVariant.primary:
        return _ButtonStyle(
          background: _hovered && _enabled ? t.primaryHover : t.primary,
          foreground: t.primaryContent,
          shadow: t.shadowXs,
        );
      case AppButtonVariant.secondary:
        return _ButtonStyle(
          background: _hovered && _enabled ? t.surfaceHover : t.surfaceSunken,
          foreground: t.content,
          border: t.border,
        );
      // `surfaceHover.at(0)` rather than `Colors.transparent` on both of these.
      //
      // The background is animated (see the `AnimatedContainer` above), and
      // `Colors.transparent` is transparent *black* - so lerping to a near-white hover
      // grey walks the RGB up from zero and paints a solid mid-grey halfway through.
      // Every ghost and outline button flashed dark before settling. Holding the colour
      // and animating only its alpha keeps the wash at its final hue throughout.
      case AppButtonVariant.outline:
        return _ButtonStyle(
          background: _hovered && _enabled
              ? t.surfaceHover
              : t.surfaceHover.at(0),
          foreground: t.content,
          border: t.borderStrong,
        );
      case AppButtonVariant.ghost:
        return _ButtonStyle(
          background: _hovered && _enabled
              ? t.surfaceHover
              : t.surfaceHover.at(0),
          foreground: _hovered && _enabled ? t.content : t.contentSecondary,
        );
      case AppButtonVariant.destructive:
        return _ButtonStyle(
          // `hover:brightness-110` - lightened rather than swapped for another
          // token, so the destructive red stays recognisably one colour.
          background: _hovered && _enabled
              ? Color.lerp(t.danger, Colors.white, 0.12)!
              : t.danger,
          foreground: Colors.white,
          shadow: t.shadowXs,
        );
      case AppButtonVariant.link:
        return _ButtonStyle(
          background: Colors.transparent,
          foreground: t.primary,
        );
    }
  }
}

class _ButtonStyle {
  const _ButtonStyle({
    required this.background,
    required this.foreground,
    this.border,
    this.shadow,
  });

  final Color background;
  final Color foreground;
  final Color? border;
  final List<BoxShadow>? shadow;
}

/// A bare icon button, at the density the header and table rows use.
///
/// `AppButton(size: icon)` with the ceremony removed, because it appears about forty
/// times and every one of them needs a tooltip and an accessible name - a control
/// whose only content is a glyph is unusable with a screen reader otherwise.
class AppIconButton extends StatelessWidget {
  const AppIconButton({
    super.key,
    required this.icon,
    required this.onPressed,
    required this.tooltip,
    this.variant = AppButtonVariant.ghost,
    this.colour,
    this.size = 16,
  });

  final IconData icon;
  final VoidCallback? onPressed;

  /// Doubles as the accessible name, so it can never be left off.
  final String tooltip;
  final AppButtonVariant variant;
  final Color? colour;
  final double size;

  @override
  Widget build(BuildContext context) {
    return AppButton(
      onPressed: onPressed,
      variant: variant,
      size: AppButtonSize.icon,
      tooltip: tooltip,
      semanticLabel: tooltip,
      child: Icon(icon, size: size, color: colour),
    );
  }
}
