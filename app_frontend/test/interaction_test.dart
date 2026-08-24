import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stellarerp_desktop/theme/app_theme.dart';
import 'package:stellarerp_desktop/widgets/app_button.dart';
import 'package:stellarerp_desktop/widgets/info_tip.dart';

/// Does pressing things do anything?
///
/// This exists because of a bug that made **every button in the app dead** while looking
/// entirely healthy. `AppButton` wrapped its content in an `InkWell` carrying `onPressed`,
/// and *inside* that put a `GestureDetector` declaring `onTapDown`/`onTapUp` to animate the
/// press. Two tap recognizers, one gesture arena, and the deeper one wins - so the
/// `GestureDetector` claimed every tap and `InkWell.onTap` never fired.
///
/// Nothing logged, nothing threw, `flutter analyze` was clean, and the button still animated
/// under the cursor - so it read as "the callbacks are broken" rather than "the tap is being
/// stolen two widgets down". The first test here is the one that would have caught it, and it
/// is deliberately the most boring assertion in the suite.
void main() {
  Widget wrap(Widget child) => MaterialApp(
    theme: AppTheme.light(),
    home: Scaffold(body: child),
  );

  group('AppButton actually fires', () {
    testWidgets('a tap calls onPressed', (WidgetTester tester) async {
      int taps = 0;
      await tester.pumpWidget(
        wrap(AppButton(onPressed: () => taps++, label: 'Record money in')),
      );

      await tester.tap(find.text('Record money in'));
      await tester.pumpAndSettle();

      expect(taps, 1);
    });

    testWidgets('every variant and size fires', (WidgetTester tester) async {
      // The press animation and the callback used to live on different widgets, so a
      // variant that changed the tree shape could plausibly have broken one and not the
      // other. Cheap to cover all of them.
      for (final AppButtonVariant variant in AppButtonVariant.values) {
        for (final AppButtonSize size in AppButtonSize.values) {
          int taps = 0;
          await tester.pumpWidget(
            wrap(
              AppButton(
                onPressed: () => taps++,
                variant: variant,
                size: size,
                label: 'Go',
              ),
            ),
          );
          await tester.tap(find.text('Go'));
          await tester.pumpAndSettle();
          expect(taps, 1, reason: '$variant / $size did not fire');
        }
      }
    });

    testWidgets('a null onPressed stays inert', (WidgetTester tester) async {
      await tester.pumpWidget(
        wrap(const AppButton(onPressed: null, label: 'Disabled')),
      );
      // Nothing to assert but the absence of a crash: a disabled button must not throw
      // when pressed, and must not somehow become enabled.
      await tester.tap(find.text('Disabled'), warnIfMissed: false);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });

    testWidgets('a loading button is inert, so a submit cannot double-fire', (
      WidgetTester tester,
    ) async {
      int taps = 0;
      await tester.pumpWidget(
        wrap(
          AppButton(onPressed: () => taps++, loading: true, label: 'Saving…'),
        ),
      );
      await tester.tap(find.text('Saving…'), warnIfMissed: false);
      // `pump`, not `pumpAndSettle`: the spinner animates forever, so waiting for the
      // tree to go quiet would simply time out.
      await tester.pump();
      // `loading` implies disabled - `_enabled` is `onPressed != null && !loading`. That
      // matters on this app's forms: every save shows a spinner while the request is in
      // flight, and a second click posting a second entry to the ledger is not a
      // cosmetic bug.
      expect(taps, 0);
    });

    testWidgets('a tap inside a scroll view fires', (
      WidgetTester tester,
    ) async {
      // Where the app actually puts its buttons. A scrollable adds its own drag
      // recognizer to the arena, which a tap must still win.
      int taps = 0;
      await tester.pumpWidget(
        wrap(
          SingleChildScrollView(
            child: Column(
              children: <Widget>[
                const SizedBox(height: 40),
                AppButton(onPressed: () => taps++, label: 'Add card'),
              ],
            ),
          ),
        ),
      );

      await tester.tap(find.text('Add card'));
      await tester.pumpAndSettle();
      expect(taps, 1);
    });
  });

  group('InfoTip does not swallow the app', () {
    /// The tip inserts an `OverlayEntry` with a `Positioned.fill` catcher so a click
    /// anywhere dismisses it. That catcher sits above everything, so if it outlived the
    /// tip the symptom would be exactly the one above - nothing clickable, no error.
    Widget tipAndButton(void Function() onTap, {bool withTip = true}) => wrap(
      Column(
        children: <Widget>[
          if (withTip)
            const InfoTip(
              label: 'Net',
              children: <Widget>[Text('Money in less money out.')],
            ),
          AppButton(onPressed: onTap, label: 'Record money in'),
        ],
      ),
    );

    testWidgets('it opens and closes', (WidgetTester tester) async {
      await tester.pumpWidget(tipAndButton(() {}));
      expect(find.text('Money in less money out.'), findsNothing);

      await tester.tap(find.byType(InfoTip));
      await tester.pumpAndSettle();
      expect(find.text('Money in less money out.'), findsOneWidget);

      await tester.tap(find.byType(InfoTip));
      await tester.pumpAndSettle();
      expect(find.text('Money in less money out.'), findsNothing);
    });

    testWidgets('buttons still work once a tip has been opened and closed', (
      WidgetTester tester,
    ) async {
      int taps = 0;
      await tester.pumpWidget(tipAndButton(() => taps++));

      await tester.tap(find.byType(InfoTip));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(InfoTip));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Record money in'));
      await tester.pumpAndSettle();
      expect(taps, 1, reason: 'the catcher must be gone once the tip closes');
    });

    testWidgets('a tip disposed while open takes its catcher with it', (
      WidgetTester tester,
    ) async {
      // The navigation case: open a tip, then leave the screen without dismissing it.
      int taps = 0;
      await tester.pumpWidget(tipAndButton(() => taps++));
      await tester.tap(find.byType(InfoTip));
      await tester.pumpAndSettle();
      expect(find.text('Money in less money out.'), findsOneWidget);

      await tester.pumpWidget(tipAndButton(() => taps++, withTip: false));
      await tester.pumpAndSettle();

      expect(find.text('Money in less money out.'), findsNothing);
      await tester.tap(find.text('Record money in'));
      await tester.pumpAndSettle();
      expect(taps, 1, reason: 'the catcher must not outlive the tip');
    });
  });
}
