import 'package:flutter/material.dart';

import 'oklch.dart';

/// The design tokens, ported one-for-one from `frontend/src/styles/globals.css`.
///
/// The token layer is semantic rather than literal, exactly as on the web:
/// widgets reference `tokens.surface` and `tokens.contentMuted`, never a zinc
/// value - so dark mode is one set of overrides instead of a conditional at
/// every paint, and a brand change is a handful of lines here.
///
/// Carried as a `ThemeExtension` so it travels with the `ThemeData` and animates
/// with it: `MaterialApp` cross-fades between the light and dark themes, which
/// is what reproduces the web app's `transition: background-color 200ms` on the
/// theme switch rather than a hard cut.
@immutable
class AppTokens extends ThemeExtension<AppTokens> {
  const AppTokens({
    required this.brightness,
    required this.canvas,
    required this.surface,
    required this.surfaceRaised,
    required this.surfaceSunken,
    required this.surfaceHover,
    required this.surfaceActive,
    required this.content,
    required this.contentSecondary,
    required this.contentMuted,
    required this.contentInverted,
    required this.border,
    required this.borderStrong,
    required this.primary,
    required this.primaryHover,
    required this.primaryContent,
    required this.ring,
    required this.success,
    required this.successBg,
    required this.warning,
    required this.warningBg,
    required this.danger,
    required this.dangerBg,
    required this.info,
    required this.infoBg,
    required this.shadowXs,
    required this.shadowSm,
    required this.shadowMd,
    required this.shadowLg,
    required this.shadowXl,
    required this.glassBg,
    required this.glassBorder,
  });

  final Brightness brightness;

  // Surfaces, from furthest back to furthest forward.
  final Color canvas;
  final Color surface;
  final Color surfaceRaised;
  final Color surfaceSunken;
  final Color surfaceHover;
  final Color surfaceActive;

  // Text, in descending emphasis.
  final Color content;
  final Color contentSecondary;
  final Color contentMuted;
  final Color contentInverted;

  // Lines.
  final Color border;
  final Color borderStrong;

  // Interactive.
  final Color primary;
  final Color primaryHover;
  final Color primaryContent;
  final Color ring;

  // Status: a saturated base paired with a tinted background, so badges and
  // alerts stay readable in both themes.
  final Color success;
  final Color successBg;
  final Color warning;
  final Color warningBg;
  final Color danger;
  final Color dangerBg;
  final Color info;
  final Color infoBg;

  // Elevation. Layered, low-alpha shadows read as depth; a single hard shadow
  // reads as a border.
  final List<BoxShadow> shadowXs;
  final List<BoxShadow> shadowSm;
  final List<BoxShadow> shadowMd;
  final List<BoxShadow> shadowLg;
  final List<BoxShadow> shadowXl;

  // Glass. Restrained on purpose: heavy blur over dense tables hurts
  // legibility, so it is used only on floating chrome.
  final Color glassBg;
  final Color glassBorder;

  bool get isDark => brightness == Brightness.dark;

  // ---------------------------------------------------------------------------
  // Light - the default
  // ---------------------------------------------------------------------------
  static final AppTokens light = AppTokens(
    brightness: Brightness.light,
    canvas: oklch(0.985, 0.001, 285),
    surface: oklch(1, 0, 0),
    surfaceRaised: oklch(1, 0, 0),
    surfaceSunken: oklch(0.968, 0.002, 285),
    surfaceHover: oklch(0.965, 0.002, 285),
    surfaceActive: oklch(0.945, 0.003, 285),
    content: oklch(0.21, 0.006, 285),
    contentSecondary: oklch(0.44, 0.008, 285),
    contentMuted: oklch(0.58, 0.008, 285),
    contentInverted: oklch(0.99, 0, 0),
    border: oklch(0.915, 0.003, 285),
    borderStrong: oklch(0.86, 0.004, 285),
    primary: oklch(0.55, 0.21, 285),
    primaryHover: oklch(0.5, 0.21, 285),
    primaryContent: oklch(0.99, 0, 0),
    ring: oklch(0.55, 0.21, 285),
    success: oklch(0.6, 0.15, 155),
    successBg: oklch(0.95, 0.04, 155),
    warning: oklch(0.72, 0.16, 75),
    warningBg: oklch(0.96, 0.05, 85),
    danger: oklch(0.58, 0.21, 25),
    dangerBg: oklch(0.95, 0.035, 25),
    info: oklch(0.6, 0.15, 240),
    infoBg: oklch(0.95, 0.035, 240),
    shadowXs: [
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.05),
        offset: const Offset(0, 1),
        blurRadius: 2,
      ),
    ],
    shadowSm: [
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.07),
        offset: const Offset(0, 1),
        blurRadius: 3,
      ),
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.07),
        offset: const Offset(0, 1),
        blurRadius: 2,
        spreadRadius: -1,
      ),
    ],
    shadowMd: [
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.08),
        offset: const Offset(0, 4),
        blurRadius: 12,
        spreadRadius: -2,
      ),
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.06),
        offset: const Offset(0, 2),
        blurRadius: 4,
        spreadRadius: -2,
      ),
    ],
    shadowLg: [
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.12),
        offset: const Offset(0, 12),
        blurRadius: 28,
        spreadRadius: -6,
      ),
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.07),
        offset: const Offset(0, 4),
        blurRadius: 8,
        spreadRadius: -4,
      ),
    ],
    shadowXl: [
      BoxShadow(
        color: oklch(0.2, 0.01, 285, 0.18),
        offset: const Offset(0, 24),
        blurRadius: 56,
        spreadRadius: -12,
      ),
    ],
    glassBg: oklch(1, 0, 0, 0.72),
    glassBorder: oklch(1, 0, 0, 0.6),
  );

  // ---------------------------------------------------------------------------
  // Dark
  //
  // Not an inversion. Surfaces lift as they come forward (a raised panel is
  // *lighter* than the canvas, mirroring how light behaves), text contrast is
  // stepped down slightly because pure white on near-black vibrates, and the
  // accent is lightened to hold its contrast ratio.
  // ---------------------------------------------------------------------------
  static final AppTokens dark = AppTokens(
    brightness: Brightness.dark,
    canvas: oklch(0.145, 0.004, 285),
    surface: oklch(0.185, 0.005, 285),
    surfaceRaised: oklch(0.215, 0.006, 285),
    surfaceSunken: oklch(0.165, 0.004, 285),
    surfaceHover: oklch(0.235, 0.006, 285),
    surfaceActive: oklch(0.265, 0.007, 285),
    content: oklch(0.965, 0.002, 285),
    contentSecondary: oklch(0.73, 0.008, 285),
    contentMuted: oklch(0.58, 0.008, 285),
    contentInverted: oklch(0.17, 0.005, 285),
    border: oklch(0.275, 0.006, 285),
    borderStrong: oklch(0.36, 0.008, 285),
    primary: oklch(0.62, 0.19, 285),
    primaryHover: oklch(0.68, 0.18, 285),
    primaryContent: oklch(0.99, 0, 0),
    ring: oklch(0.68, 0.18, 285),
    success: oklch(0.72, 0.16, 155),
    successBg: oklch(0.28, 0.06, 155),
    warning: oklch(0.8, 0.15, 85),
    warningBg: oklch(0.3, 0.06, 85),
    danger: oklch(0.68, 0.19, 25),
    dangerBg: oklch(0.29, 0.07, 25),
    info: oklch(0.7, 0.15, 240),
    infoBg: oklch(0.28, 0.06, 240),
    // Shadows barely register on dark surfaces, so elevation is carried by the
    // surface ramp above. These stay for focus rings and floating layers.
    shadowXs: const [
      BoxShadow(color: Color(0x4D000000), offset: Offset(0, 1), blurRadius: 2),
    ],
    shadowSm: const [
      BoxShadow(color: Color(0x66000000), offset: Offset(0, 1), blurRadius: 3),
    ],
    shadowMd: const [
      BoxShadow(
        color: Color(0x73000000),
        offset: Offset(0, 4),
        blurRadius: 12,
        spreadRadius: -2,
      ),
    ],
    shadowLg: const [
      BoxShadow(
        color: Color(0x8C000000),
        offset: Offset(0, 12),
        blurRadius: 28,
        spreadRadius: -6,
      ),
    ],
    shadowXl: const [
      BoxShadow(
        color: Color(0xA6000000),
        offset: Offset(0, 24),
        blurRadius: 56,
        spreadRadius: -12,
      ),
    ],
    glassBg: oklch(0.185, 0.005, 285, 0.72),
    glassBorder: oklch(1, 0, 0, 0.08),
  );

  @override
  AppTokens copyWith() => this;

  /// Interpolates every colour, so the theme switch animates instead of cutting.
  @override
  AppTokens lerp(ThemeExtension<AppTokens>? other, double t) {
    if (other is! AppTokens) return this;
    Color c(Color a, Color b) => Color.lerp(a, b, t)!;
    List<BoxShadow> s(List<BoxShadow> a, List<BoxShadow> b) => t < 0.5 ? a : b;

    return AppTokens(
      brightness: t < 0.5 ? brightness : other.brightness,
      canvas: c(canvas, other.canvas),
      surface: c(surface, other.surface),
      surfaceRaised: c(surfaceRaised, other.surfaceRaised),
      surfaceSunken: c(surfaceSunken, other.surfaceSunken),
      surfaceHover: c(surfaceHover, other.surfaceHover),
      surfaceActive: c(surfaceActive, other.surfaceActive),
      content: c(content, other.content),
      contentSecondary: c(contentSecondary, other.contentSecondary),
      contentMuted: c(contentMuted, other.contentMuted),
      contentInverted: c(contentInverted, other.contentInverted),
      border: c(border, other.border),
      borderStrong: c(borderStrong, other.borderStrong),
      primary: c(primary, other.primary),
      primaryHover: c(primaryHover, other.primaryHover),
      primaryContent: c(primaryContent, other.primaryContent),
      ring: c(ring, other.ring),
      success: c(success, other.success),
      successBg: c(successBg, other.successBg),
      warning: c(warning, other.warning),
      warningBg: c(warningBg, other.warningBg),
      danger: c(danger, other.danger),
      dangerBg: c(dangerBg, other.dangerBg),
      info: c(info, other.info),
      infoBg: c(infoBg, other.infoBg),
      shadowXs: s(shadowXs, other.shadowXs),
      shadowSm: s(shadowSm, other.shadowSm),
      shadowMd: s(shadowMd, other.shadowMd),
      shadowLg: s(shadowLg, other.shadowLg),
      shadowXl: s(shadowXl, other.shadowXl),
      glassBg: c(glassBg, other.glassBg),
      glassBorder: c(glassBorder, other.glassBorder),
    );
  }
}

/// Radii, from the `--radius-*` scale.
abstract final class Radii {
  static const double xs = 4; // 0.25rem
  static const double sm = 6; // 0.375rem
  static const double md = 8; // 0.5rem
  static const double lg = 12; // 0.75rem
  static const double xl = 16; // 1rem
  static const double xl2 = 20; // 1.25rem
  static const double full = 9999;
}

/// One shared easing curve and three durations.
///
/// Consistent timing is most of what makes an interface feel deliberate rather
/// than busy, so these are the same three the CSS defines and nothing picks its
/// own.
abstract final class Motion {
  /// `--ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1)`
  static const Curve easeOutQuart = Cubic(0.25, 1, 0.5, 1);

  /// `--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1)`
  static const Curve easeSpring = Cubic(0.34, 1.56, 0.64, 1);

  static const Duration fast = Duration(milliseconds: 120);
  static const Duration base = Duration(milliseconds: 200);
  static const Duration slow = Duration(milliseconds: 320);
}

/// `Theme.of(context).extension<AppTokens>()!`, without the ceremony.
extension TokensOf on BuildContext {
  AppTokens get tokens => Theme.of(this).extension<AppTokens>()!;
}
