import 'dart:async';

import 'package:cookie_jar/cookie_jar.dart';
import 'package:dio/dio.dart';
import 'package:dio_cookie_manager/dio_cookie_manager.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'api_error.dart';
import 'env.dart';

/// The HTTP client, and the token lifecycle around it.
///
/// **The access token lives in memory only** - a private field that dies with the
/// process, exactly as the web app's module-scoped variable dies with the tab.
/// Writing it to disk would leave a bearer token in a file that any other process
/// running as this user can read, which is the desktop equivalent of putting it
/// in `localStorage`.
///
/// **The refresh token is never handled by this code at all**, and that is
/// deliberate rather than an omission. The backend does not return it in any
/// response body (`auth/schemas.py` says so explicitly); it arrives as a
/// `Set-Cookie` header and goes back out the same way. A cookie jar means this
/// client behaves like the browser does - it holds a credential it cannot read -
/// so there is no second code path in the backend to trust and nothing here that
/// could log the token by accident.
///
/// The jar is *persisted*, which is the one place desktop and web legitimately
/// differ: a browser keeps its cookie across a tab close, so an app that forgot
/// the session on every quit would be worse than the thing it is copying, not
/// safer. It lands in the OS application-support directory, which is per-user.
///
/// **Refresh is single-flight.** When a token expires, every in-flight request
/// 401s at once. Naively each would start its own refresh - and because the
/// server *rotates* refresh tokens and treats reuse as a breach, the second
/// refresh would present an already-rotated token and get the whole session
/// revoked. So the first 401 starts a refresh, the rest await that same future,
/// and all of them retry afterwards.
class ApiClient {
  ApiClient._(this._dio, this._jar);

  final Dio _dio;
  final CookieJar _jar;

  /// The access token, in memory only - see the class docstring.
  ///
  /// A plain field rather than a getter/setter pair: there is nothing to validate,
  /// and wrapping it would only make the one property this class exists to guard look
  /// more ceremonious than it is.
  String? accessToken;

  Future<String>? _refreshFuture;

  /// Called when a refresh fails - the session is genuinely over, not merely
  /// stale. Wired to the auth controller's teardown.
  void Function()? onSessionExpired;

  Dio get dio => _dio;

  /// Endpoints that must never trigger a refresh-and-retry.
  ///
  /// A 401 from any of these *is* the answer - wrong password, dead magic link,
  /// no session to refresh - and retrying it would turn one failed sign-in into
  /// two requests against a rate limit.
  static const List<String> _noRefreshPaths = <String>[
    '/auth/login',
    '/auth/refresh',
    '/auth/register',
    '/auth/logout',
    '/auth/otp/verify',
    '/auth/magic-link/verify',
    // A 401 here means the handle is spent or expired, not that the access token
    // needs refreshing - and this one is polled, so a retry per tick would double
    // every request on the screen.
    '/auth/magic-link/device/poll',
  ];

  /// Build the client.
  ///
  /// [storageDirectory] overrides where the cookie jar lives, and exists so the session
  /// round-trip can be tested against a real backend: `getApplicationSupportDirectory`
  /// is a platform channel, which is not available in a plain test runner. Production
  /// passes nothing and gets the per-user application-support directory.
  static Future<ApiClient> create({String? storageDirectory}) async {
    final String root =
        storageDirectory ?? (await getApplicationSupportDirectory()).path;
    final PersistCookieJar jar = PersistCookieJar(
      // `ignoreExpires: false` so an expired refresh cookie is dropped rather
      // than replayed into a guaranteed 401 on every launch.
      ignoreExpires: false,
      storage: FileStorage(p.join(root, 'cookies')),
    );

    final Dio dio = Dio(
      BaseOptions(
        baseUrl: Env.apiRoot,
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 30),
        sendTimeout: const Duration(seconds: 30),
        headers: <String, String>{'Content-Type': 'application/json'},
        // Every non-2xx is surfaced as a `DioException` so `ApiError.from` is the
        // single place a failure is interpreted.
        validateStatus: (int? status) =>
            status != null && status >= 200 && status < 300,
      ),
    );

    final ApiClient client = ApiClient._(dio, jar);
    dio.interceptors.add(CookieManager(jar));
    dio.interceptors.add(_AuthInterceptor(client));
    return client;
  }

  // ---------------------------------------------------------------------------
  // Typed helpers
  // ---------------------------------------------------------------------------
  Future<T> get<T>(String path, {Map<String, dynamic>? query}) =>
      _send<T>(() => _dio.get<dynamic>(path, queryParameters: _clean(query)));

  /// Fetch a response body as raw bytes - a report export, not JSON.
  ///
  /// Goes through the same [Dio] instance as everything else on purpose, so the export
  /// carries the access token and passes through the refresh interceptor. A separate plain
  /// HTTP call would have to reimplement both, and would 401 the moment a token aged out
  /// mid-session.
  Future<List<int>> bytes(String path, {Map<String, dynamic>? query}) =>
      _send<List<int>>(
        () => _dio.get<dynamic>(
          path,
          queryParameters: _clean(query),
          options: Options(responseType: ResponseType.bytes),
        ),
      );

  Future<T> post<T>(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    Options? options,
  }) => _send<T>(
    () => _dio.post<dynamic>(
      path,
      data: body,
      queryParameters: _clean(query),
      options: options,
    ),
  );

  Future<T> patch<T>(String path, {Object? body}) =>
      _send<T>(() => _dio.patch<dynamic>(path, data: body));

  Future<T> put<T>(String path, {Object? body}) =>
      _send<T>(() => _dio.put<dynamic>(path, data: body));

  Future<T> delete<T>(String path, {Object? body}) =>
      _send<T>(() => _dio.delete<dynamic>(path, data: body));

  Future<T> _send<T>(Future<Response<dynamic>> Function() request) async {
    try {
      final Response<dynamic> response = await request();
      return response.data as T;
    } catch (error) {
      throw ApiError.from(error);
    }
  }

  /// Drops query keys with no value.
  ///
  /// Dio serialises a null parameter as `?status=`, and the backend's enum
  /// validators reject an empty string - so an unset filter would 422 instead of
  /// meaning "no filter". The web app avoids this by conditionally spreading the
  /// key; doing it centrally here is the same rule with fewer places to forget it.
  static Map<String, dynamic>? _clean(Map<String, dynamic>? query) {
    if (query == null) return null;
    final Map<String, dynamic> cleaned = <String, dynamic>{
      for (final MapEntry<String, dynamic> e in query.entries)
        if (e.value != null && e.value != '') e.key: e.value,
    };
    return cleaned.isEmpty ? null : cleaned;
  }

  // ---------------------------------------------------------------------------
  // Session lifecycle
  // ---------------------------------------------------------------------------
  /// Exchange the stored refresh cookie for an access token.
  ///
  /// Returns false when there is no valid cookie, which simply means "not signed
  /// in" - the normal first-launch path, not an error.
  Future<bool> bootstrapSession() async {
    try {
      // Through the same single-flight future the interceptor uses, never a
      // direct call: two refreshes on one cookie race, and the loser looks like a
      // replayed token to the server, which revokes the whole lineage. That is a
      // sign-out nobody asked for.
      await (_refreshFuture ??= _refreshAccessToken().whenComplete(() {
        _refreshFuture = null;
      }));
      return true;
    } catch (_) {
      accessToken = null;
      return false;
    }
  }

  Future<String> _refreshAccessToken() async {
    // A bare Dio, not `_dio`: going through the instance would re-enter the auth
    // interceptor and attach the dead access token. The cookie manager is added
    // explicitly, because the refresh cookie is the entire point of the call.
    final Dio bare = Dio(
      BaseOptions(
        baseUrl: Env.apiRoot,
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 15),
        headers: <String, String>{'Content-Type': 'application/json'},
      ),
    )..interceptors.add(CookieManager(_jar));

    final Response<dynamic> response = await bare.post<dynamic>(
      '/auth/refresh',
      data: const <String, dynamic>{},
    );

    final String token =
        (response.data as Map<String, dynamic>)['access_token'] as String;
    accessToken = token;
    return token;
  }

  Future<String> refreshSingleFlight() {
    return _refreshFuture ??= _refreshAccessToken().whenComplete(() {
      _refreshFuture = null;
    });
  }

  /// Forget everything about the signed-in user.
  ///
  /// The jar is emptied as well as the token: leaving a valid refresh cookie on
  /// disk after a sign-out would let the next person to open the app be silently
  /// restored into the previous user's books.
  Future<void> clearSession() async {
    accessToken = null;
    await _jar.deleteAll();
  }
}

class _AuthInterceptor extends Interceptor {
  _AuthInterceptor(this._client);

  final ApiClient _client;

  /// Marks a request that has already been retried, so a loop is impossible.
  static const String _retriedKey = 'stellarerp_retried';

  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    final String? token = _client.accessToken;
    if (token != null) {
      options.headers['Authorization'] = 'Bearer $token';
    }
    handler.next(options);
  }

  @override
  Future<void> onError(
    DioException err,
    ErrorInterceptorHandler handler,
  ) async {
    final RequestOptions options = err.requestOptions;
    final bool alreadyRetried = options.extra[_retriedKey] == true;
    final bool exempt = ApiClient._noRefreshPaths.any(
      (String path) => options.path.contains(path),
    );

    if (err.response?.statusCode != 401 || alreadyRetried || exempt) {
      // A 401 that survived a refresh is not stale - the brand-new token was
      // rejected too, which means revoked or a bumped token epoch. Retrying
      // cannot fix it, and leaving the session in place leaves the open screen
      // firing requests that will every one of them 401. End it, so the router's
      // guard sends the user to sign in.
      //
      // Deliberately not applied to the exempt paths: a 401 from `/auth/login`
      // is a wrong password, and signing the user out for mistyping one would be
      // absurd. Those never reach here already retried.
      if (err.response?.statusCode == 401 && alreadyRetried && !exempt) {
        _client.accessToken = null;
        _client.onSessionExpired?.call();
      }
      handler.next(err);
      return;
    }

    options.extra[_retriedKey] = true;

    try {
      final String token = await _client.refreshSingleFlight();
      options.headers['Authorization'] = 'Bearer $token';
      final Response<dynamic> retried = await _client.dio.fetch<dynamic>(
        options,
      );
      handler.resolve(retried);
    } catch (_) {
      // The refresh token is gone, expired, or was revoked. This is a real
      // sign-out, not a transient failure.
      _client.accessToken = null;
      _client.onSessionExpired?.call();
      handler.next(err);
    }
  }
}
