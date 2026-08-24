import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:stellarerp_desktop/core/decimal_input.dart';
import 'package:stellarerp_desktop/theme/oklch.dart';
import 'package:stellarerp_desktop/theme/tokens.dart';

/// The palette conversion, and the input filter.
///
/// The oklch cases are pinned to the values the CSS defines, so a change to the conversion
/// maths cannot silently shift the brand colour. Each expectation was computed by an
/// independent implementation of the same OKLab matrices rather than read back out of this
/// one - a test that asserts whatever the code already returns proves nothing. The tolerance
/// is one 8-bit step, which is the most two correct implementations can differ by from
/// rounding alone.
///
/// Two of these double as a check that the palette is the one the CSS intends: the light
/// theme's `--content` lands on #18181b and its `--canvas` on #fafafb, which are Tailwind's
/// zinc-900 and zinc-50 - exactly the near-neutral ramp the design tokens describe.
void main() {
  group('oklch', () {
    void expectColour(Color actual, int r, int g, int b) {
      final int actualR = (actual.r * 255).round();
      final int actualG = (actual.g * 255).round();
      final int actualB = (actual.b * 255).round();
      expect(actualR, closeTo(r, 1), reason: 'red');
      expect(actualG, closeTo(g, 1), reason: 'green');
      expect(actualB, closeTo(b, 1), reason: 'blue');
    }

    test('converts pure white and black', () {
      expectColour(oklch(1, 0, 0), 255, 255, 255);
      expectColour(oklch(0, 0, 0), 0, 0, 0);
    });

    test('converts the brand indigo', () {
      // --primary in the light theme: oklch(0.55 0.21 285) -> #6b53e4
      expectColour(oklch(0.55, 0.21, 285), 107, 83, 228);
      // --primary in the dark theme, lightened to hold its contrast ratio.
      expectColour(oklch(0.62, 0.19, 285), 126, 110, 242);
    });

    test('converts the surface ramp at both ends', () {
      // --canvas dark: oklch(0.145 0.004 285) -> #0a0a0c
      expectColour(oklch(0.145, 0.004, 285), 10, 10, 12);
      // --canvas light: oklch(0.985 0.001 285) -> #fafafb, Tailwind's zinc-50.
      expectColour(oklch(0.985, 0.001, 285), 250, 250, 251);
      // --content light: oklch(0.21 0.006 285) -> #18181b, Tailwind's zinc-900.
      expectColour(oklch(0.21, 0.006, 285), 24, 24, 27);
    });

    test('converts the status colours', () {
      expectColour(oklch(0.6, 0.15, 155), 0, 153, 86);
      expectColour(oklch(0.58, 0.21, 25), 219, 43, 51);
    });

    test('clips an out-of-gamut value rather than throwing', () {
      // Chroma far outside sRGB. Every real token is in gamut; this only proves the guard.
      expect(() => oklch(0.5, 0.9, 120), returnsNormally);
    });

    test('applies alpha', () {
      expect(oklch(1, 0, 0, 0.5).a, closeTo(0.5, 0.01));
    });
  });

  group('tokens', () {
    test('the dark theme lifts surfaces as they come forward', () {
      // Not an inversion: a raised panel is *lighter* than the canvas, mirroring how light
      // behaves. This is the property that makes the dark theme read as depth.
      final AppTokens t = AppTokens.dark;
      expect(
        t.surface.computeLuminance(),
        greaterThan(t.canvas.computeLuminance()),
      );
      expect(
        t.surfaceRaised.computeLuminance(),
        greaterThan(t.surface.computeLuminance()),
      );
      expect(
        t.surfaceSunken.computeLuminance(),
        lessThan(t.surface.computeLuminance()),
      );
    });

    test('the light theme sinks surfaces as they recede', () {
      final AppTokens t = AppTokens.light;
      expect(
        t.surfaceSunken.computeLuminance(),
        lessThan(t.surface.computeLuminance()),
      );
    });

    test('body text clears the WCAG AA contrast ratio in both themes', () {
      double ratio(Color foreground, Color background) {
        final double a = foreground.computeLuminance() + 0.05;
        final double b = background.computeLuminance() + 0.05;
        return a > b ? a / b : b / a;
      }

      expect(
        ratio(AppTokens.light.content, AppTokens.light.canvas),
        greaterThan(4.5),
      );
      expect(
        ratio(AppTokens.dark.content, AppTokens.dark.canvas),
        greaterThan(4.5),
      );
    });
  });

  group('sanitiseDecimal', () {
    test('keeps a partially typed decimal point', () {
      // Rejecting "12." would make the point impossible to type.
      expect(sanitiseDecimal('12.'), '12.');
    });

    test('reads a leading point as nought point something', () {
      expect(sanitiseDecimal('.5'), '0.5');
    });

    test('strips everything that is not a digit or a point', () {
      expect(sanitiseDecimal('₹1,23,456.78'), '123456.78');
      expect(sanitiseDecimal('12abc34'), '1234');
    });

    test('truncates past the allowed decimals', () {
      expect(sanitiseDecimal('1.23456', decimals: 2), '1.23');
      expect(sanitiseDecimal('1.9', decimals: 0), '1');
    });

    test('rejects a minus unless negatives are allowed', () {
      expect(sanitiseDecimal('-5'), '5');
      expect(sanitiseDecimal('-5', allowNegative: true), '-5');
    });

    test(
      'collapses a second point rather than dropping the digits after it',
      () {
        expect(sanitiseDecimal('1.2.3'), '1.23');
      },
    );

    test('returns empty for input with nothing numeric in it', () {
      expect(sanitiseDecimal('abc'), '');
    });
  });
}
