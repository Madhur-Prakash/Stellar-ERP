import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'core/env.dart';
import 'router.dart';
import 'state/theme_controller.dart';
import 'theme/app_theme.dart';
import 'widgets/toast.dart';

/// The application root.
///
/// Both themes are handed to `MaterialApp` with a `themeMode`, rather than one theme
/// rebuilt on change. That is what makes the switch animate: Flutter cross-fades between
/// the two `ThemeData`s over 200ms, which reproduces the web app's
/// `transition: background-color 200ms` instead of a hard cut.
///
/// The toast host wraps the router rather than sitting inside it, so a toast raised by a
/// mutation survives the navigation that mutation triggers - "invoice posted" should not
/// vanish because the screen behind it moved on.
class StellarErpApp extends ConsumerWidget {
  const StellarErpApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final GoRouter router = ref.watch(routerProvider);
    final ThemeChoice theme = ref.watch(themeControllerProvider);

    return MaterialApp.router(
      title: Env.appName,
      debugShowCheckedModeBanner: false,
      routerConfig: router,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: theme.mode,
      themeAnimationDuration: const Duration(milliseconds: 200),
      themeAnimationCurve: Curves.easeOutQuart,
      builder: (BuildContext context, Widget? child) =>
          ToastScope(child: child ?? const SizedBox.shrink()),
    );
  }
}
