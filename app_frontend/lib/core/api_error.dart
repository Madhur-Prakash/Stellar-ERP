import 'dart:convert';

import 'package:dio/dio.dart';

/// A normalised API failure.
///
/// Every call site gets the same shape - a machine-readable [code], a displayable
/// [message], and [fieldErrors] ready to attach to a form - instead of each one
/// unwrapping `error.response.data['error']['details']['fields']` for itself.
///
/// Mirrors `frontend/src/lib/api.ts`, including the two awkward cases: the
/// password policy answers with a *list* of reasons rather than a field map, and
/// a request that never reached the app has no envelope at all.
class ApiError implements Exception {
  ApiError(
    this.message, {
    this.code = 'unknown_error',
    this.status = 0,
    this.details = const <String, dynamic>{},
    this.requestId,
  });

  final String message;
  final String code;
  final int status;
  final Map<String, dynamic> details;

  /// The backend's per-request id. Shown on the error screen because it is what
  /// makes a user report actionable - it maps directly to the log lines for that
  /// exact request.
  final String? requestId;

  /// Per-field messages from a 422, keyed by field name.
  Map<String, String> get fieldErrors {
    final Object? fields = details['fields'];
    if (fields is Map) {
      return fields.map(
        (Object? key, Object? value) =>
            MapEntry<String, String>('$key', '$value'),
      );
    }
    // The password policy returns a list of reasons rather than a field map.
    final Object? password = details['password'];
    if (password is List) {
      return <String, String>{'password': password.join('. ')};
    }
    return const <String, String>{};
  }

  bool get isValidation => status == 422;
  bool get isUnauthenticated => status == 401;
  bool get isForbidden => status == 403;
  bool get isNotFound => status == 404;
  bool get isRateLimited => status == 429;

  /// True for conditions a retry might resolve - offline, timeout, 5xx.
  bool get isRetryable => status == 0 || status >= 500;

  /// How long to wait before retrying, in seconds, or null if unknown.
  ///
  /// The API sends this two ways - a `Retry-After` header and
  /// `details.retry_after_seconds` - and this reads the body, because the header is only
  /// legible cross-origin when the server remembers to list it in `expose_headers`. The
  /// body always survives.
  int? get retryAfterSeconds {
    final Object? raw = details['retry_after_seconds'];
    final num? seconds = raw is num ? raw : num.tryParse('$raw');
    if (seconds == null || seconds <= 0) return null;
    return seconds.ceil();
  }

  /// A displayable "too many requests" message, with the wait when the server gave one.
  ///
  /// Exists because the server's own message is `"Too many requests. Slow down."` -
  /// correct but unhelpful, since the one thing a user needs is *how long*. A rate limit
  /// with no stated wait is indistinguishable from the app being broken, so people retry
  /// immediately, which is exactly what keeps the bucket empty.
  ///
  /// Kept worded identically to `frontend/src/lib/api.ts` so the same condition does not
  /// read as two different problems depending on which client the user is holding.
  String get rateLimitMessage {
    final int? seconds = retryAfterSeconds;
    if (seconds == null) {
      return 'Too many attempts. Please wait a moment and try again.';
    }
    if (seconds < 60) {
      return 'Too many attempts. Please try again in $seconds '
          'second${seconds == 1 ? '' : 's'}.';
    }
    final int minutes = (seconds / 60).ceil();
    return 'Too many attempts. Please try again in $minutes '
        'minute${minutes == 1 ? '' : 's'}.';
  }

  /// The error envelope, whatever response type the failed request asked for.
  ///
  /// **A `ResponseType.bytes` request gets its *error* body as bytes too.** Dio applies
  /// the response type it was given regardless of status, so a 410 from
  /// `/documents/{id}/file` arrives as a `List<int>` holding the JSON envelope rather
  /// than as the decoded map. Without this, the envelope check in [from] fails, and the
  /// user is shown Dio's own "The request returned an invalid status code of 410"
  /// instead of "The stored file is missing." - the server said the right thing and the
  /// client discarded it.
  ///
  /// Anything that is not UTF-8 JSON is returned untouched, so a proxy's HTML error page
  /// still falls through to the generic branches rather than being misread.
  static Object? _decodeBody(Object? body) {
    if (body is! List<int>) return body;
    try {
      return jsonDecode(utf8.decode(body));
    } catch (_) {
      return body;
    }
  }

  /// Normalise anything thrown by Dio, or by us, into one of these.
  static ApiError from(Object error) {
    if (error is ApiError) return error;

    if (error is DioException) {
      final Response<dynamic>? response = error.response;
      final Object? body = _decodeBody(response?.data);

      if (body is Map && body['error'] is Map) {
        final Map<Object?, Object?> envelope =
            body['error'] as Map<Object?, Object?>;
        final Object? rawDetails = envelope['details'];
        return ApiError(
          '${envelope['message'] ?? 'Something went wrong'}',
          code: '${envelope['code'] ?? 'unknown_error'}',
          status: response?.statusCode ?? 0,
          details: rawDetails is Map
              ? rawDetails.map(
                  (Object? k, Object? v) => MapEntry<String, dynamic>('$k', v),
                )
              : const <String, dynamic>{},
          requestId: envelope['request_id'] as String?,
        );
      }

      // No envelope: the request never reached the app - the host is down, the
      // laptop is offline, a proxy answered. Say so plainly rather than
      // rendering "null".
      if (response == null) {
        final bool timedOut =
            error.type == DioExceptionType.connectionTimeout ||
            error.type == DioExceptionType.sendTimeout ||
            error.type == DioExceptionType.receiveTimeout;
        return ApiError(
          timedOut
              ? 'The request timed out. Check your connection and try again.'
              : 'Could not reach the server. Check your connection and try again.',
          code: 'network_error',
        );
      }

      // A response, but not one of ours. Something between the app and the API answered:
      // a proxy, a load balancer, the host guard in front of the router. Never
      // `error.message` here - Dio's is four paragraphs of developer prose ending in a
      // link to MDN, and it went straight under the password field on the sign-in screen.
      return ApiError(
        _withoutEnvelope(response.statusCode ?? 0, response.data),
        code: 'http_error',
        status: response.statusCode ?? 0,
      );
    }

    return ApiError(error is Exception ? '$error' : 'Something went wrong');
  }

  /// A readable message for a response that carried no error envelope.
  ///
  /// The status is always named, because this class of failure is a deployment problem -
  /// a misrouted request, a missing hostname on the allow-list - and the number is the
  /// first thing anyone diagnosing it will ask for.
  ///
  /// A short plain-text body is appended when there is one. `Invalid host header` is
  /// eleven characters that say exactly what is wrong, and swallowing it in favour of a
  /// generic sentence would be throwing away the answer. HTML is not appended: a proxy's
  /// error page is kilobytes of markup, and none of it belongs on screen.
  ///
  /// Kept worded in step with `frontend/src/lib/api.ts`, so one deployment fault does not
  /// read as two different problems depending on which client is in hand.
  static String _withoutEnvelope(int status, Object? body) {
    final String base = switch (status) {
      400 => 'The server rejected that request',
      401 => 'Your session has expired. Sign in again',
      403 => 'You do not have permission to do that',
      404 => 'That address does not exist on this server',
      408 || 504 => 'The server took too long to respond',
      413 => 'That upload is too large',
      429 => 'Too many requests. Wait a moment and try again',
      >= 500 => 'The server ran into a problem. Try again in a moment',
      _ => 'The request failed',
    };

    final String detail = _shortText(body);
    return detail.isEmpty
        ? '$base (HTTP $status).'
        : '$base: $detail (HTTP $status).';
  }

  /// A body worth showing: short, present, and neither markup nor JSON.
  ///
  /// Bytes are decoded here rather than through [_decodeBody], which answers a different
  /// question - it parses JSON and hands the original bytes back when that fails, which is
  /// precisely the case this method is reached in.
  static String _shortText(Object? body) {
    String text;
    if (body is String) {
      text = body;
    } else if (body is List<int>) {
      try {
        text = utf8.decode(body);
      } catch (_) {
        return '';
      }
    } else {
      return '';
    }

    text = text.trim();
    // JSON reaching this point has no `error` envelope, so it is some other service's
    // shape: readable to a developer, noise to everyone else. Markup is a proxy's error
    // page - kilobytes of it, and none of it belongs on screen.
    if (text.isEmpty ||
        text.length > 200 ||
        text.startsWith('<') ||
        text.startsWith('{') ||
        text.startsWith('[')) {
      return '';
    }
    return text;
  }

  @override
  String toString() => message;
}
