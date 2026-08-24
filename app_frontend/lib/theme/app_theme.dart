import 'package:flutter/material.dart';

import 'tokens.dart';

/// Material 3 themes built *from* the design tokens.
///
/// The direction matters. The usual Flutter approach is
/// `ColorScheme.fromSeed(seedColor: indigo)`, which invents a whole tonal palette
/// - and would produce a Material-flavoured indigo app that is recognisably not
/// this product. Here the `ColorScheme` is assembled from `AppTokens` instead, so
/// every Material widget that reaches for `colorScheme.surface` lands on the same
/// value a hand-built widget does, and the two cannot diverge.
///
/// Material 3 is genuinely on (`useMaterial3: true`): the ripples, the focus and
/// hover states, the dialog and menu geometry, the 40dp-plus touch targets are
/// all Material's. What is overridden is the *palette and the metrics* - a 9px
/// radius where Material wants 4, a 32px-tall dense row where Material wants 48 -
/// because those are what make the web app look like itself.
abstract final class AppTheme {
  /// The web app's font stack, in order.
  ///
  /// Inter first, exactly as the CSS asks, and the platform UI font behind it.
  /// This is the same resolution the browser performs: on a machine with Inter
  /// installed both surfaces use Inter, and on one without, both fall back to
  /// Segoe UI. Naming a single family instead would guarantee they differ.
  static const String _fontFamily = 'Inter';
  static const List<String> _fontFallback = <String>[
    'Segoe UI Variable Display',
    'Segoe UI',
    'Roboto',
    'Helvetica Neue',
    'Arial',
  ];

  /// `--font-mono`, for entry numbers, SKUs, GSTINs, and recognised text.
  static const String monoFamily = 'JetBrains Mono';
  static const List<String> monoFallback = <String>[
    'Cascadia Mono',
    'Consolas',
    'SF Mono',
    'Menlo',
    'monospace',
  ];

  static ThemeData light() => _build(AppTokens.light);

  static ThemeData dark() => _build(AppTokens.dark);

  static ThemeData _build(AppTokens t) {
    final ColorScheme scheme = ColorScheme(
      brightness: t.brightness,
      primary: t.primary,
      onPrimary: t.primaryContent,
      primaryContainer: t.primary.withValues(alpha: 0.12),
      onPrimaryContainer: t.primary,
      secondary: t.contentSecondary,
      onSecondary: t.contentInverted,
      secondaryContainer: t.surfaceSunken,
      onSecondaryContainer: t.content,
      tertiary: t.info,
      onTertiary: t.contentInverted,
      error: t.danger,
      onError: Colors.white,
      errorContainer: t.dangerBg,
      onErrorContainer: t.danger,
      surface: t.surface,
      onSurface: t.content,
      surfaceContainerLowest: t.canvas,
      surfaceContainerLow: t.surfaceSunken,
      surfaceContainer: t.surface,
      surfaceContainerHigh: t.surfaceRaised,
      surfaceContainerHighest: t.surfaceActive,
      onSurfaceVariant: t.contentSecondary,
      outline: t.border,
      outlineVariant: t.borderStrong,
      shadow: Colors.black,
      scrim: Colors.black,
      inverseSurface: t.content,
      onInverseSurface: t.contentInverted,
      inversePrimary: t.primaryHover,
    );

    final TextTheme text = _textTheme(t);

    return ThemeData(
      useMaterial3: true,
      brightness: t.brightness,
      colorScheme: scheme,
      extensions: <ThemeExtension<dynamic>>[t],
      scaffoldBackgroundColor: t.canvas,
      canvasColor: t.canvas,
      dividerColor: t.border,
      fontFamily: _fontFamily,
      fontFamilyFallback: _fontFallback,
      textTheme: text,
      primaryTextTheme: text,

      // `:focus-visible` in the CSS - a ring for keyboard users, nothing for a
      // mouse click. Flutter's focus highlight already follows that rule, so it
      // only needs the right colour.
      focusColor: t.ring.withValues(alpha: 0.25),
      hoverColor: t.surfaceHover,
      splashColor: t.primary.withValues(alpha: 0.08),
      highlightColor: t.surfaceActive,

      // Scrollbars: thin and neutral, so a dark UI does not get bright grey bars
      // bolted onto its edges.
      scrollbarTheme: ScrollbarThemeData(
        thickness: const WidgetStatePropertyAll<double>(6),
        radius: const Radius.circular(Radii.full),
        thumbColor: WidgetStateProperty.resolveWith((Set<WidgetState> states) {
          if (states.contains(WidgetState.hovered)) return t.contentMuted;
          return t.borderStrong;
        }),
        trackColor: const WidgetStatePropertyAll<Color>(Colors.transparent),
        trackBorderColor: const WidgetStatePropertyAll<Color>(
          Colors.transparent,
        ),
        crossAxisMargin: 2,
      ),

      dividerTheme: DividerThemeData(color: t.border, thickness: 1, space: 1),

      iconTheme: IconThemeData(color: t.contentSecondary, size: 16),

      tooltipTheme: TooltipThemeData(
        waitDuration: const Duration(milliseconds: 500),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
        decoration: BoxDecoration(
          color: t.surfaceRaised,
          borderRadius: BorderRadius.circular(Radii.md),
          border: Border.all(color: t.border),
          boxShadow: t.shadowMd,
        ),
        textStyle: TextStyle(
          fontSize: 12,
          color: t.contentSecondary,
          height: 1.4,
        ),
      ),

      dialogTheme: DialogThemeData(
        backgroundColor: t.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(Radii.xl),
          side: BorderSide(color: t.border),
        ),
        // `backdrop:bg-black/40 backdrop:backdrop-blur-sm`
        barrierColor: Colors.black.withValues(alpha: 0.4),
      ),

      popupMenuTheme: PopupMenuThemeData(
        color: t.surfaceRaised,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(Radii.lg),
          side: BorderSide(color: t.border),
        ),
        textStyle: TextStyle(fontSize: 13, color: t.contentSecondary),
      ),

      // The web app checkboxes are 14px (`h-3.5 w-3.5`) with a 4px radius, which
      // is well under Material's 18px. Scaled down at the call site rather than
      // here, so hit targets stay honest; this only fixes the colours.
      checkboxTheme: CheckboxThemeData(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(Radii.xs),
        ),
        side: BorderSide(color: t.borderStrong, width: 1.5),
        fillColor: WidgetStateProperty.resolveWith((Set<WidgetState> states) {
          if (states.contains(WidgetState.selected)) return t.primary;
          return Colors.transparent;
        }),
        checkColor: WidgetStatePropertyAll<Color>(t.primaryContent),
        visualDensity: VisualDensity.compact,
        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),

      radioTheme: RadioThemeData(
        fillColor: WidgetStateProperty.resolveWith((Set<WidgetState> states) {
          if (states.contains(WidgetState.selected)) return t.primary;
          return t.borderStrong;
        }),
        visualDensity: VisualDensity.compact,
      ),

      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((Set<WidgetState> states) {
          if (states.contains(WidgetState.selected)) return t.primaryContent;
          return t.surface;
        }),
        trackColor: WidgetStateProperty.resolveWith((Set<WidgetState> states) {
          if (states.contains(WidgetState.selected)) return t.primary;
          return t.surfaceActive;
        }),
        trackOutlineColor: WidgetStatePropertyAll<Color>(t.border),
      ),

      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: t.primary,
        linearTrackColor: t.surfaceSunken,
        circularTrackColor: t.surfaceSunken,
      ),

      // Every text field in the app goes through `AppInput`, which owns its own
      // decoration. This is the baseline that keeps a bare `TextField` - inside
      // a Material `DropdownMenu`, say - from arriving with Material's filled
      // underline treatment.
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: t.surface,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        hintStyle: TextStyle(color: t.contentMuted, fontSize: 14),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Radii.md),
          borderSide: BorderSide(color: t.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Radii.md),
          borderSide: BorderSide(color: t.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Radii.md),
          borderSide: BorderSide(color: t.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Radii.md),
          borderSide: BorderSide(color: t.danger),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Radii.md),
          borderSide: BorderSide(color: t.danger, width: 2),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Radii.md),
          borderSide: BorderSide(color: t.border),
        ),
      ),

      // `::selection { background-color: color-mix(primary 26%, transparent) }`
      textSelectionTheme: TextSelectionThemeData(
        cursorColor: t.primary,
        selectionColor: t.primary.withValues(alpha: 0.26),
        selectionHandleColor: t.primary,
      ),
    );
  }

  /// The type scale.
  ///
  /// Mapped from the literal pixel sizes the web app uses rather than from
  /// Material's `bodyLarge`/`titleMedium` names, because the web app sets sizes
  /// directly (`text-[13px]`) and a semantic remap would quietly change them.
  /// 13px body is the density this UI is designed at - Material's 16px default
  /// would put a third fewer rows on screen.
  ///
  /// `letterSpacing` is in logical pixels here where the CSS uses `em`, so each
  /// value is the em figure multiplied by its own font size.
  static TextTheme _textTheme(AppTokens t) {
    return TextTheme(
      // 26px / -0.03em - the auth screen headings.
      displaySmall: TextStyle(
        fontSize: 26,
        height: 1.15,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.78,
        color: t.content,
      ),
      // 24px / -0.03em - the 404 and error headings, and KPI figures.
      headlineMedium: TextStyle(
        fontSize: 24,
        height: 1.15,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.72,
        color: t.content,
      ),
      // 22px / -0.025em - `PageHeader`'s h1.
      headlineSmall: TextStyle(
        fontSize: 22,
        height: 1.2,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.55,
        color: t.content,
      ),
      // 20px - report stat tiles.
      titleLarge: TextStyle(
        fontSize: 20,
        height: 1.2,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.4,
        color: t.content,
      ),
      // 15px - card headings.
      titleMedium: TextStyle(
        fontSize: 15,
        height: 1.35,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.15,
        color: t.content,
      ),
      // 14px
      titleSmall: TextStyle(
        fontSize: 14,
        height: 1.4,
        fontWeight: FontWeight.w500,
        color: t.content,
      ),
      // 14px body.
      bodyLarge: TextStyle(fontSize: 14, height: 1.45, color: t.content),
      // 13px - the workhorse. Tables, forms, list rows.
      bodyMedium: TextStyle(fontSize: 13, height: 1.45, color: t.content),
      // 12px - hints and metadata.
      bodySmall: TextStyle(fontSize: 12, height: 1.45, color: t.contentMuted),
      // 13px medium - field labels.
      labelLarge: TextStyle(
        fontSize: 13,
        height: 1.4,
        fontWeight: FontWeight.w500,
        color: t.contentSecondary,
      ),
      // 12px medium - tile labels.
      labelMedium: TextStyle(
        fontSize: 12,
        height: 1.4,
        fontWeight: FontWeight.w500,
        color: t.contentMuted,
      ),
      // 11px semibold uppercase - table headers and section eyebrows.
      labelSmall: TextStyle(
        fontSize: 11,
        height: 1.4,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.44,
        color: t.contentMuted,
      ),
    );
  }
}

/// Monospace, for the columns that must line up character by character.
TextStyle monoStyle({
  double fontSize = 12,
  Color? color,
  FontWeight? fontWeight,
  double? height,
}) {
  return TextStyle(
    fontFamily: AppTheme.monoFamily,
    fontFamilyFallback: AppTheme.monoFallback,
    fontSize: fontSize,
    color: color,
    fontWeight: fontWeight,
    height: height,
  );
}

/// `tabular-nums`: digits on a fixed advance so figures form vertical columns.
///
/// Money in a proportional font is genuinely harder to scan, and every numeric
/// column in the web app sets this. Applied as a font feature rather than by
/// switching to the mono family, so the surrounding text keeps its own face.
const List<FontFeature> tabularFigures = <FontFeature>[
  FontFeature.tabularFigures(),
];
