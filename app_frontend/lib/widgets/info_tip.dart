import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/tokens.dart';

/// A small ⓘ button that explains the thing next to it.
///
/// Accounting software is full of terms that are precise and unfamiliar -
/// "receivables", "reversal", "control account" - and the usual answers are both bad:
/// a manual nobody reads, or a hover tooltip that vanishes the moment you move
/// towards it and cannot hold a paragraph worth reading. So the explanation lives
/// next to the number it explains, and stays put until dismissed.
///
/// Deliberately *not* a `Tooltip`. These panels run to four paragraphs with bold runs
/// inside them, and they have to be readable at leisure - a hover tooltip is the wrong
/// affordance for text someone needs to finish.
///
/// Implementation notes that matter for it being usable rather than decorative:
///
/// * **Escape closes it, and so does a click anywhere outside.** A popover that can
///   only be dismissed by hitting the same 16-pixel target again is a trap.
/// * **Positioned by an [align] flag rather than measured.** A tile at the right edge
///   of the grid needs its panel to open leftwards or it is clipped; a measuring pass
///   is not worth the weight for that.
class InfoTip extends StatefulWidget {
  const InfoTip({
    super.key,
    required this.label,
    required this.children,
    this.align = InfoTipAlign.left,
  });

  /// What this explains, for the accessible name: "About revenue".
  final String label;

  /// The paragraphs. Rich text is built with [infoText] and [infoRich].
  final List<Widget> children;

  final InfoTipAlign align;

  @override
  State<InfoTip> createState() => _InfoTipState();
}

enum InfoTipAlign { left, right }

class _InfoTipState extends State<InfoTip> {
  final LayerLink _link = LayerLink();
  OverlayEntry? _entry;
  bool _hovered = false;

  bool get _open => _entry != null;

  @override
  void dispose() {
    _entry?.remove();
    _entry = null;
    super.dispose();
  }

  void _toggle() => _open ? _close() : _show();

  void _close() {
    _entry?.remove();
    _entry = null;
    if (mounted) setState(() {});
  }

  void _show() {
    final AppTokens t = context.tokens;

    _entry = OverlayEntry(
      builder: (BuildContext context) => Stack(
        children: <Widget>[
          // A full-screen catcher so a click anywhere outside dismisses. Transparent
          // rather than tinted: this is an explanation, not a modal, and dimming the
          // page for it would overstate its importance.
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.translucent,
              onTap: _close,
            ),
          ),
          CompositedTransformFollower(
            link: _link,
            showWhenUnlinked: false,
            // Anchored to the icon's bottom, offset by its height plus a little.
            targetAnchor: widget.align == InfoTipAlign.left
                ? Alignment.bottomLeft
                : Alignment.bottomRight,
            followerAnchor: widget.align == InfoTipAlign.left
                ? Alignment.topLeft
                : Alignment.topRight,
            offset: const Offset(0, 6),
            child: Semantics(
              container: true,
              child: Container(
                width: 256,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: t.surfaceRaised,
                  borderRadius: BorderRadius.circular(Radii.lg),
                  border: Border.all(color: t.border),
                  boxShadow: t.shadowLg,
                ),
                child: DefaultTextStyle(
                  style: TextStyle(
                    fontSize: 12,
                    height: 1.6,
                    color: t.contentSecondary,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    spacing: 6,
                    children: widget.children,
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );

    Overlay.of(context).insert(_entry!);
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final Color colour = _open
        ? t.primary
        : _hovered
        ? t.content
        : t.contentMuted;

    return CompositedTransformTarget(
      link: _link,
      child: Semantics(
        button: true,
        expanded: _open,
        label: 'About ${widget.label}',
        child: CallbackShortcuts(
          bindings: <ShortcutActivator, VoidCallback>{
            const SingleActivator(LogicalKeyboardKey.escape): _close,
          },
          child: MouseRegion(
            cursor: SystemMouseCursors.help,
            onEnter: (_) => setState(() => _hovered = true),
            onExit: (_) => setState(() => _hovered = false),
            child: GestureDetector(
              onTap: _toggle,
              child: Container(
                width: 16,
                height: 16,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: _open
                        ? t.primary
                        : (_hovered ? t.borderStrong : t.border),
                  ),
                ),
                alignment: Alignment.center,
                child: Text(
                  'i',
                  style: TextStyle(
                    fontSize: 10,
                    height: 1,
                    fontWeight: FontWeight.w600,
                    color: colour,
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// A plain paragraph inside an [InfoTip].
Widget infoText(String text) => Text(text);

/// A paragraph with **bold** runs, written as alternating plain/bold segments.
///
/// The web app writes these as JSX with `<strong>` inside. A segment list is the
/// closest thing that stays readable at the call site - the alternative is a
/// `RichText` tree per sentence, which buries the words in punctuation.
Widget infoRich(List<String> segments) {
  return Builder(
    builder: (BuildContext context) {
      final AppTokens t = context.tokens;
      return Text.rich(
        TextSpan(
          children: <InlineSpan>[
            for (int index = 0; index < segments.length; index++)
              TextSpan(
                text: segments[index],
                style: index.isOdd
                    ? TextStyle(color: t.content, fontWeight: FontWeight.w600)
                    : null,
              ),
          ],
        ),
      );
    },
  );
}
