import 'package:flutter/material.dart';

import '../theme/oklch.dart';
import '../theme/tokens.dart';

/// A modal dialog.
///
/// The web app builds this on the platform `<dialog showModal()>` because the browser
/// gives four things correctly that are routinely got wrong by hand: focus moved into
/// the dialog and trapped there, the rest of the page inert to pointer and screen
/// reader alike, Escape to close, and a real backdrop.
///
/// Flutter's `showDialog` gives all four for the same reason - it is the framework's
/// own modal route - so this is a `showDialog` call with the chrome restyled, not a
/// hand-rolled overlay. `barrierDismissible` covers the backdrop click that the web
/// version has to compute from click coordinates, since `::backdrop` is not
/// hit-testable.
///
/// Width is `min(92vw, 34rem)`, matched from the CSS: wide enough for a two-column
/// form, never wider than the window.
Future<T?> showAppModal<T>({
  required BuildContext context,
  required String title,
  String? description,
  required WidgetBuilder builder,
  List<Widget> Function(BuildContext context)? footer,
  bool dismissible = true,
}) {
  return showDialog<T>(
    context: context,
    barrierDismissible: dismissible,
    builder: (BuildContext context) => AppModal(
      title: title,
      description: description,
      footer: footer?.call(context),
      child: builder(context),
    ),
  );
}

class AppModal extends StatelessWidget {
  const AppModal({
    super.key,
    required this.title,
    this.description,
    required this.child,
    this.footer,
    this.maxWidth = 544,
  });

  final String title;
  final String? description;
  final Widget child;
  final List<Widget>? footer;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final Size window = MediaQuery.sizeOf(context);

    return Dialog(
      backgroundColor: t.surface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      insetPadding: const EdgeInsets.all(24),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(Radii.xl),
        side: BorderSide(color: t.border),
      ),
      clipBehavior: Clip.antiAlias,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxWidth: maxWidth < window.width * 0.92
              ? maxWidth
              : window.width * 0.92,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              decoration: BoxDecoration(
                border: Border(bottom: BorderSide(color: t.border)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    title,
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                      color: t.content,
                    ),
                  ),
                  if (description != null) ...<Widget>[
                    const SizedBox(height: 2),
                    Text(
                      description!,
                      style: TextStyle(
                        fontSize: 13,
                        color: t.contentMuted,
                        height: 1.45,
                      ),
                    ),
                  ],
                ],
              ),
            ),

            // `max-h-[70vh] overflow-y-auto`. A form that outgrows the window scrolls
            // inside the dialog rather than pushing the footer off the bottom, which
            // would leave the Save button unreachable.
            Flexible(
              child: ConstrainedBox(
                constraints: BoxConstraints(maxHeight: window.height * 0.7),
                child: SingleChildScrollView(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 16,
                  ),
                  child: child,
                ),
              ),
            ),

            if (footer != null)
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 20,
                  vertical: 12,
                ),
                decoration: BoxDecoration(
                  color: t.surfaceSunken.at(0.4),
                  border: Border(top: BorderSide(color: t.border)),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  spacing: 8,
                  children: footer!,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// The confirm-then-act prompt, for a destructive action.
///
/// The web app uses `window.confirm` for these and says why: Stage 1 had one
/// destructive action, and a dialog system belonged with the rest of the UI kit. The
/// kit exists here, so these are real dialogs - but the *text* is carried over
/// verbatim, because each one names what will and will not happen ("their account is
/// not deleted", "its history and stock stay") and that wording is the whole value.
Future<bool> confirmAction(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'Confirm',
  bool destructive = true,
}) async {
  final AppTokens t = context.tokens;

  final bool? answer = await showDialog<bool>(
    context: context,
    builder: (BuildContext context) => AppModal(
      title: title,
      maxWidth: 420,
      footer: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: Text('Cancel', style: TextStyle(color: t.contentSecondary)),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          style: FilledButton.styleFrom(
            backgroundColor: destructive ? t.danger : t.primary,
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(Radii.md),
            ),
          ),
          child: Text(confirmLabel),
        ),
      ],
      child: Text(
        message,
        style: TextStyle(fontSize: 13, color: t.contentSecondary, height: 1.6),
      ),
    ),
  );

  return answer ?? false;
}

/// Ask for a single line of text, then act.
///
/// Replaces the web app's `window.prompt`, which it uses for a reversal reason and a
/// document rejection. The distinction that version documents is preserved: cancelling
/// returns null and does nothing, whereas submitting an *empty* string is a legitimate
/// "no reason given" and proceeds.
Future<String?> promptForText(
  BuildContext context, {
  required String title,
  String? description,
  String? placeholder,
  String confirmLabel = 'Continue',
  bool allowEmpty = true,
  int minimumLength = 0,
}) async {
  final TextEditingController controller = TextEditingController();
  final AppTokens t = context.tokens;

  final String? answer = await showDialog<String>(
    context: context,
    builder: (BuildContext context) {
      return StatefulBuilder(
        builder:
            (BuildContext context, void Function(void Function()) setState) {
              final String text = controller.text.trim();
              final bool valid =
                  (allowEmpty || text.isNotEmpty) &&
                  text.length >= minimumLength;

              return AppModal(
                title: title,
                description: description,
                maxWidth: 460,
                footer: <Widget>[
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: Text(
                      'Cancel',
                      style: TextStyle(color: t.contentSecondary),
                    ),
                  ),
                  FilledButton(
                    onPressed: valid
                        ? () => Navigator.of(context).pop(text)
                        : null,
                    style: FilledButton.styleFrom(
                      backgroundColor: t.primary,
                      foregroundColor: t.primaryContent,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(Radii.md),
                      ),
                    ),
                    child: Text(confirmLabel),
                  ),
                ],
                child: TextField(
                  controller: controller,
                  autofocus: true,
                  onChanged: (_) => setState(() {}),
                  onSubmitted: valid
                      ? (String value) =>
                            Navigator.of(context).pop(value.trim())
                      : null,
                  style: TextStyle(fontSize: 14, color: t.content),
                  decoration: InputDecoration(hintText: placeholder),
                ),
              );
            },
      );
    },
  );

  controller.dispose();
  return answer;
}
