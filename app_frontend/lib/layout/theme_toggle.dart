import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:flutter/material.dart';

import '../state/theme_controller.dart';
import '../widgets/app_button.dart';

/// Cycles light -> dark -> system.
///
/// A single button rather than a dropdown: three states are few enough to cycle
/// through, the current one is always visible as the icon, and it keeps the header
/// uncluttered. The tooltip names the *current* mode so the control is not ambiguous.
class ThemeToggle extends ConsumerWidget {
  const ThemeToggle({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeChoice choice = ref.watch(themeControllerProvider);
    final IconData icon = switch (choice) {
      ThemeChoice.light => LucideIcons.sun,
      ThemeChoice.dark => LucideIcons.moon,
      ThemeChoice.system => LucideIcons.monitor,
    };

    return AppIconButton(
      icon: icon,
      tooltip: '${choice.label}. Click to change.',
      onPressed: () => ref.read(themeControllerProvider.notifier).cycle(),
    );
  }
}
