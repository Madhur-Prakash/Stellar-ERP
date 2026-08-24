import 'dart:async';

import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../core/api_error.dart';
import '../theme/tokens.dart';

/// Toasts, in the web app's arrangement: bottom-right, stacked, dismissible.
///
/// Not a `SnackBar`. Material's snack bar is a single bottom-centre bar that replaces
/// whatever was already showing, and this app fires several in a row - recording a week
/// of receipts produces one per entry, and the point of keeping the form open is that
/// they accumulate rather than each erasing the last.
///
/// The metrics come straight from the `Toaster` configuration in `App.tsx`:
/// `surface-raised` on a `border`, `radius-lg`, `shadow-lg`, 13px text.
enum ToastTone { success, error, warning, info }

class ToastData {
  ToastData({
    required this.message,
    this.description,
    required this.tone,
    this.duration = const Duration(seconds: 5),
    this.actionLabel,
    this.onAction,
  });

  final String message;
  final String? description;
  final ToastTone tone;
  final Duration duration;

  /// An inline action - "Resend" on the unverified-email toast, which is the whole
  /// reason that failure is a toast rather than a form error.
  final String? actionLabel;
  final VoidCallback? onAction;
}

/// The host. Mounted once, above the router, so a toast survives navigation.
class ToastScope extends StatefulWidget {
  const ToastScope({super.key, required this.child});

  final Widget child;

  static ToastScopeState of(BuildContext context) {
    final ToastScopeState? state = context
        .findAncestorStateOfType<ToastScopeState>();
    assert(state != null, 'No ToastScope found in the widget tree');
    return state!;
  }

  @override
  State<ToastScope> createState() => ToastScopeState();
}

class ToastScopeState extends State<ToastScope> {
  final List<ToastData> _toasts = <ToastData>[];

  void show(ToastData toast) {
    setState(() => _toasts.add(toast));
    Timer(toast.duration, () {
      if (mounted) setState(() => _toasts.remove(toast));
    });
  }

  void _dismiss(ToastData toast) => setState(() => _toasts.remove(toast));

  @override
  Widget build(BuildContext context) {
    // Toasts are mounted in `MaterialApp.builder`, which is *outside* the router - that is
    // what lets one survive navigation. The cost is that nothing here sits under a
    // `Scaffold`, so the nearest `DefaultTextStyle` is the deliberately hideous fallback
    // `MaterialApp` installs to nag you into using a `Material`: 48px red monospace with a
    // double yellow underline. A `Text` merges its own style *onto* the ambient one, so the
    // toast's explicit size and colour won and the leftover underline and font family came
    // through - hence yellow-underlined toast text. Restoring the theme's own body style
    // here fixes every toast at once, rather than each `Text` having to opt out.
    final TextStyle base =
        (Theme.of(context).textTheme.bodyMedium ?? const TextStyle()).copyWith(
          decoration: TextDecoration.none,
        );

    return Stack(
      children: <Widget>[
        widget.child,
        Positioned(
          right: 16,
          bottom: 16,
          child: DefaultTextStyle(
            style: base,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              mainAxisAlignment: MainAxisAlignment.end,
              spacing: 8,
              children: <Widget>[
                for (final ToastData toast in _toasts)
                  _Toast(
                    key: ObjectKey(toast),
                    toast: toast,
                    onDismiss: () => _dismiss(toast),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _Toast extends StatefulWidget {
  const _Toast({super.key, required this.toast, required this.onDismiss});

  final ToastData toast;
  final VoidCallback onDismiss;

  @override
  State<_Toast> createState() => _ToastState();
}

class _ToastState extends State<_Toast> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: Motion.slow,
  )..forward();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final (IconData icon, Color colour) = switch (widget.toast.tone) {
      ToastTone.success => (LucideIcons.circleCheckBig, t.success),
      ToastTone.error => (LucideIcons.circleX, t.danger),
      ToastTone.warning => (LucideIcons.triangleAlert, t.warning),
      ToastTone.info => (LucideIcons.info, t.info),
    };

    final CurvedAnimation eased = CurvedAnimation(
      parent: _controller,
      curve: Motion.easeOutQuart,
    );

    return FadeTransition(
      opacity: eased,
      child: SlideTransition(
        // Slides in from the right edge it lives on, which reads as arriving rather
        // than appearing.
        position: Tween<Offset>(
          begin: const Offset(0.15, 0),
          end: Offset.zero,
        ).animate(eased),
        child: Semantics(
          liveRegion: true,
          child: Container(
            width: 360,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
            decoration: BoxDecoration(
              color: t.surfaceRaised,
              borderRadius: BorderRadius.circular(Radii.lg),
              border: Border.all(color: t.border),
              boxShadow: t.shadowLg,
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 10,
              children: <Widget>[
                Padding(
                  padding: const EdgeInsets.only(top: 1),
                  child: Icon(icon, size: 16, color: colour),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        widget.toast.message,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                          color: t.content,
                          height: 1.4,
                        ),
                      ),
                      if (widget.toast.description != null) ...<Widget>[
                        const SizedBox(height: 2),
                        Text(
                          widget.toast.description!,
                          // Wraps, then gives up. A description is a save path or a server
                          // message, and neither has a bounded length: without a cap a deeply
                          // nested folder turns a toast into a wall covering the screen it is
                          // reporting on. Three lines fits any real path at this width.
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 12,
                            color: t.contentMuted,
                            height: 1.45,
                          ),
                        ),
                      ],
                      if (widget.toast.actionLabel != null) ...<Widget>[
                        const SizedBox(height: 8),
                        GestureDetector(
                          onTap: () {
                            widget.toast.onAction?.call();
                            widget.onDismiss();
                          },
                          child: MouseRegion(
                            cursor: SystemMouseCursors.click,
                            child: Text(
                              widget.toast.actionLabel!,
                              style: TextStyle(
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                                color: t.primary,
                              ),
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                GestureDetector(
                  onTap: widget.onDismiss,
                  child: MouseRegion(
                    cursor: SystemMouseCursors.click,
                    child: Semantics(
                      button: true,
                      label: 'Dismiss',
                      child: Icon(
                        LucideIcons.x,
                        size: 14,
                        color: t.contentMuted,
                      ),
                    ),
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

/// The call sites read like `toast.success(...)` in the web app, so they read the same
/// way here.
extension ToastMessenger on BuildContext {
  void toastSuccess(String message, {String? description}) =>
      ToastScope.of(this).show(
        ToastData(
          message: message,
          description: description,
          tone: ToastTone.success,
        ),
      );

  void toastError(String message, {String? description}) =>
      ToastScope.of(this).show(
        ToastData(
          message: message,
          description: description,
          tone: ToastTone.error,
        ),
      );

  void toastWarning(
    String message, {
    String? description,
    Duration? duration,
  }) => ToastScope.of(this).show(
    ToastData(
      message: message,
      description: description,
      tone: ToastTone.warning,
      duration: duration ?? const Duration(seconds: 5),
    ),
  );

  void toastInfo(String message, {String? description}) =>
      ToastScope.of(this).show(
        ToastData(
          message: message,
          description: description,
          tone: ToastTone.info,
        ),
      );

  void toastAction(
    String message, {
    String? description,
    required String actionLabel,
    required VoidCallback onAction,
    ToastTone tone = ToastTone.error,
  }) => ToastScope.of(this).show(
    ToastData(
      message: message,
      description: description,
      tone: tone,
      actionLabel: actionLabel,
      onAction: onAction,
    ),
  );

  /// Report a failed mutation.
  ///
  /// The server's own message is surfaced verbatim whenever there is one, because it
  /// is more specific than anything written here could be - it names the duplicate
  /// invoice number, or the count of members still holding a role. The [fallback] is
  /// only for a failure that never reached the API.
  void toastApiError(Object error, String fallback) {
    final ApiError normalised = ApiError.from(error);
    toastError(
      normalised.code == 'unknown_error' ? fallback : normalised.message,
    );
  }
}
