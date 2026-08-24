import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Theme state.
///
/// Three modes, not two: `system` is the default because it is the behaviour users
/// expect from a modern app, and it must remain a distinct stored value. Collapsing
/// it to whichever concrete theme was current at the time would freeze the choice,
/// and the app would stop following the OS at sunset.
///
/// The web app prevents a flash of the wrong theme with an inline script that runs
/// before first paint. The desktop equivalent is reading the stored value *before*
/// `runApp`, which is why [load] is called from `main` and the initial value is
/// passed in rather than defaulted and corrected.
enum ThemeChoice {
  light,
  dark,
  system;

  static ThemeChoice parse(String? value) => switch (value) {
    'light' => ThemeChoice.light,
    'dark' => ThemeChoice.dark,
    _ => ThemeChoice.system,
  };

  ThemeMode get mode => switch (this) {
    ThemeChoice.light => ThemeMode.light,
    ThemeChoice.dark => ThemeMode.dark,
    ThemeChoice.system => ThemeMode.system,
  };

  String get label => switch (this) {
    ThemeChoice.light => 'Light theme',
    ThemeChoice.dark => 'Dark theme',
    ThemeChoice.system => 'System theme',
  };

  String get shortLabel => switch (this) {
    ThemeChoice.light => 'Light',
    ThemeChoice.dark => 'Dark',
    ThemeChoice.system => 'System',
  };
}

/// Must match the key the web app writes, purely so a developer switching between
/// the two surfaces on one machine is not surprised. Nothing depends on it.
const String _storageKey = 'personalerp-theme';

class ThemeController extends StateNotifier<ThemeChoice> {
  ThemeController(super.initial);

  /// The stored choice, read before the first frame.
  static Future<ThemeChoice> load() async {
    try {
      final SharedPreferences prefs = await SharedPreferences.getInstance();
      return ThemeChoice.parse(prefs.getString(_storageKey));
    } catch (_) {
      // A locked-down profile can fail here. Fall through to the default rather
      // than refusing to start.
      return ThemeChoice.system;
    }
  }

  Future<void> set(ThemeChoice choice) async {
    state = choice;
    try {
      final SharedPreferences prefs = await SharedPreferences.getInstance();
      await prefs.setString(_storageKey, choice.name);
    } catch (_) {
      // Non-fatal: the theme still applies for this session.
    }
  }

  /// Cycles light -> dark -> system, for the toolbar button.
  ///
  /// A single button rather than a dropdown: three states are few enough to cycle
  /// through, the current one is always visible as the icon, and it keeps the header
  /// uncluttered.
  Future<void> cycle() => set(switch (state) {
    ThemeChoice.light => ThemeChoice.dark,
    ThemeChoice.dark => ThemeChoice.system,
    ThemeChoice.system => ThemeChoice.light,
  });
}

final StateNotifierProvider<ThemeController, ThemeChoice>
themeControllerProvider = StateNotifierProvider<ThemeController, ThemeChoice>(
  (Ref ref) => throw StateError(
    'themeControllerProvider must be overridden in main() with the stored choice',
  ),
);
