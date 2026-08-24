import 'dart:math' as math;
import 'dart:ui';

/// Converting the web app's `oklch()` tokens to `Color`.
///
/// **The tokens are kept as oklch triples rather than pre-converted to hex, and
/// that is the whole point of this file.** `frontend/src/styles/globals.css`
/// defines every colour as `oklch(0.55 0.21 285)`; pasting a hand-converted
/// `#5b47d6` here would leave two independent copies of the palette that drift
/// the first time either is touched, with no way to notice - a slightly wrong
/// indigo is not something anyone spots in review.
///
/// So the literals in `tokens.dart` are the same numbers the CSS holds, and the
/// conversion happens here, once, using the standard OKLab matrices. The two
/// surfaces are then the same colour by construction rather than by diligence.
///
/// The pipeline is OKLCH → OKLab → LMS → linear sRGB → gamma-encoded sRGB,
/// matching what a browser does for `oklch()`.
Color oklch(double l, double c, double h, [double alpha = 1.0]) {
  // Polar to rectangular. Hue is degrees in CSS, radians here.
  final double hRad = h * math.pi / 180.0;
  final double a = c * math.cos(hRad);
  final double b = c * math.sin(hRad);

  // OKLab to non-linear LMS (Björn Ottosson's M2 inverse).
  final double lPrime = l + 0.3963377774 * a + 0.2158037573 * b;
  final double mPrime = l - 0.1055613458 * a - 0.0638541728 * b;
  final double sPrime = l - 0.0894841775 * a - 1.2914855480 * b;

  final double lms0 = lPrime * lPrime * lPrime;
  final double lms1 = mPrime * mPrime * mPrime;
  final double lms2 = sPrime * sPrime * sPrime;

  // LMS to linear sRGB (M1 inverse).
  final double rLinear =
      4.0767416621 * lms0 - 3.3077115913 * lms1 + 0.2309699292 * lms2;
  final double gLinear =
      -1.2684380046 * lms0 + 2.6097574011 * lms1 - 0.3413193965 * lms2;
  final double bLinear =
      -0.0041960863 * lms0 - 0.7034186147 * lms1 + 1.7076147010 * lms2;

  return Color.fromARGB(
    (alpha.clamp(0.0, 1.0) * 255).round(),
    _encode(rLinear),
    _encode(gLinear),
    _encode(bLinear),
  );
}

/// Linear light to an 8-bit sRGB channel.
///
/// Values outside the sRGB gamut are clipped rather than gamut-mapped. Every
/// token in this palette is already in gamut - the brand ramp tops out at
/// chroma 0.21, well inside it - so clipping never actually fires; it is here so
/// an out-of-range value produces a colour rather than an exception.
int _encode(double linear) {
  final double clamped = linear.clamp(0.0, 1.0);
  final double encoded = clamped <= 0.0031308
      ? 12.92 * clamped
      : 1.055 * math.pow(clamped, 1 / 2.4) - 0.055;
  return (encoded * 255).round().clamp(0, 255);
}

/// A token at partial opacity, which is what Tailwind's `/12` suffix means.
///
/// `bg-primary/12` and `border-danger/30` appear throughout the web app, so the
/// same shorthand exists here rather than every call site spelling out
/// `withValues`.
extension TokenOpacity on Color {
  Color at(double opacity) => withValues(alpha: opacity);
}
