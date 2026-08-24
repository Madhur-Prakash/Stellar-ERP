import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;

import 'app.dart';
import 'core/api_client.dart';
import 'core/env.dart';
import 'state/providers.dart';
import 'state/theme_controller.dart';

/// Start-up.
///
/// Four things have to finish before the first frame, and each is here rather than in a
/// widget because doing it later would be visible:
///
/// * **`.env`.** It has to be first: [Env] validates the base URL on first read, and
///   `ApiClient.create()` below is that first read. Loading it afterwards would build a
///   client rooted at the defaults and never mention it.
/// * **The cookie jar.** It is file-backed, so opening it is async. The session restore
///   depends on it, and a client built without it would report "not signed in" on every
///   launch.
/// * **The stored theme.** The web app runs an inline script before first paint for
///   exactly this reason - applying it after mount is a flash of the wrong theme.
/// * **The timezone database.** Every timestamp in the app is rendered in the
///   organization's zone, and `getLocation` throws until this is loaded.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await _loadEnv();

  tz_data.initializeTimeZones();

  final ApiClient client = await ApiClient.create();
  final ThemeChoice theme = await ThemeController.load();

  runApp(
    ProviderScope(
      overrides: <Override>[
        apiClientProvider.overrideWithValue(client),
        themeControllerProvider.overrideWith(
          (Ref ref) => ThemeController(theme),
        ),
      ],
      child: const StellarErpApp(),
    ),
  );
}

/// Load `.env`, and survive its absence.
///
/// A missing or malformed file is not fatal: [Env] falls back to the compiled defaults,
/// which are the local-development values, so a fresh checkout still starts and talks to
/// `127.0.0.1:8000`. Swallowing it silently would be worse than either - pointing a build
/// at a staging server and having it quietly use localhost is the failure this whole file
/// exists to prevent - so it is logged loudly enough to notice in a terminal.
///
/// `debugPrint` rather than a logger: nothing else here has one, and this runs before the
/// app that would own it.
Future<void> _loadEnv() async {
  try {
    await dotenv.load();
  } catch (error) {
    debugPrint(
      'WARNING: could not load app_frontend/.env ($error).\n'
      'Falling back to built-in defaults (${Env.apiRoot}).\n'
      'Copy .env.sample to .env, or run `make setup`.',
    );
  }
}
