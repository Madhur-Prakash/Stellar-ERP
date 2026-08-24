import 'package:flutter_dotenv/flutter_dotenv.dart';

/// Validated runtime environment, read from `.env`.
///
/// The web app parses `import.meta.env` with Zod and throws on a bad value, because a
/// missing base URL silently becomes `undefined` and surfaces later as a request to
/// `undefined/api/v1/auth/login`. The same validation runs here, at first access, and
/// names the variable it is unhappy about.
///
/// **`.env` rather than `--dart-define`.** The defines had the same failure mode as an
/// unvalidated env - a typo'd key just yields the default and the app talks to the wrong
/// host without complaint - but they were also compiled in, so pointing a build at a
/// different server meant rebuilding it. And on Windows they were actively dangerous:
/// Git Bash's MSYS layer rewrites arguments that look like Unix paths, so
/// `--dart-define=API_V1_PREFIX=/api/v1` reached the compiler as
/// `C:/Program Files/Git/api/v1` and got baked into the binary. A file has no argument
/// parsing to survive.
///
/// ```
/// # app_frontend/.env
/// API_BASE_URL=https://erp.example.com
/// API_V1_PREFIX=/api/v1
/// ```
///
/// Loaded once in `main()` before anything reads it. See [_read] for what happens when
/// it is not loaded at all.
abstract final class Env {
  /// One value, with a default and no throwing.
  ///
  /// The `isInitialized` guard is load-bearing: reading `dotenv.env` before `load()`
  /// throws `NotInitializedError`, and the widget tests never load it - they have no
  /// asset bundle and no need of one. An unloaded read yields the default, which is
  /// what a test wants and what a fresh checkout gets.
  ///
  /// Empty counts as absent. `API_BASE_URL=` in a `.env` is a mistake, not an
  /// instruction to talk to the empty string, and the validation below would reject it
  /// with a message about an absolute URL rather than about a blank line.
  static String _read(String key, String fallback) {
    if (!dotenv.isInitialized) return fallback;
    final String? value = dotenv.maybeGet(key);
    if (value == null || value.trim().isEmpty) return fallback;
    return value.trim();
  }

  /// **`127.0.0.1`, not `localhost`, and that matters.**
  ///
  /// `docker compose` publishes a port on IPv4 only, while `localhost` on Windows resolves
  /// to `::1` before `127.0.0.1`. A client that takes the first answer gets "connection
  /// refused" against a server that is running perfectly well - and because a failed
  /// session restore is indistinguishable from "not signed in", it presents as the sign-in
  /// screen with no explanation at all.
  ///
  /// The web app does not hit this: it is *served from* the origin it calls, so the browser
  /// never resolves a hostname for the API. A desktop client has no such luck, so it names
  /// the address family it means.
  static String get _rawBaseUrl =>
      _read('API_BASE_URL', 'http://127.0.0.1:8000');

  static String get _rawPrefix => _read('API_V1_PREFIX', '/api/v1');

  static String get appName => _read('APP_NAME', 'Stellar ERP');

  /// True in a debug or profile build. Gates the stack trace on the error screen,
  /// which in a release build would leak internals to no benefit.
  ///
  /// Deliberately *not* from `.env`: this is the Dart VM's own build-mode flag, and a
  /// file that could claim otherwise would let a release build be talked into showing
  /// internals.
  static const bool isDev = !bool.fromEnvironment('dart.vm.product');

  /// Validated once, on first read - which is after `main()` has loaded `.env`.
  static final String apiBaseUrl = _validatedBaseUrl();
  static final String apiPrefix = _validatedPrefix();

  /// `http://host:8000/api/v1` - what the Dio instance is rooted at.
  static String get apiRoot => '$apiBaseUrl$apiPrefix';

  /// Absolute URL for a versioned API path.
  static String url(String path) =>
      '$apiRoot${path.startsWith('/') ? path : '/$path'}';

  static String _validatedBaseUrl() {
    final String raw = _rawBaseUrl;
    final Uri? parsed = Uri.tryParse(raw);
    if (parsed == null || !parsed.hasScheme || parsed.host.isEmpty) {
      throw StateError(
        'Invalid desktop environment in app_frontend/.env:\n'
        '  API_BASE_URL: must be an absolute URL, got "$raw"',
      );
    }
    // Trailing slash stripped so joining a path never yields a double slash,
    // which some proxies treat as a different route.
    return raw.endsWith('/') ? raw.substring(0, raw.length - 1) : raw;
  }

  static String _validatedPrefix() {
    final String raw = _rawPrefix;
    if (!raw.startsWith('/')) {
      throw StateError(
        'Invalid desktop environment in app_frontend/.env:\n'
        '  API_V1_PREFIX: must start with "/", got "$raw"',
      );
    }
    return raw;
  }
}
