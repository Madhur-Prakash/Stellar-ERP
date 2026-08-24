/// Reading JSON without a code generator.
///
/// The web app hand-writes its response types for the same reason this does: the
/// surface is small enough that a generator plus its build step is more machinery
/// than it saves, and hand-written types let the comments explain intent.
///
/// Field names are `snake_case` throughout because the API is `snake_case` end to
/// end - one name for each field everywhere, rather than a translation layer that
/// has to be kept in step.
library;

typedef Json = Map<String, dynamic>;

/// A required string.
String str(Json json, String key) => '${json[key]}';

/// A nullable string. Note that a *missing* key and an explicit `null` are treated
/// alike, because the API only ever means one thing by either.
String? strOrNull(Json json, String key) {
  final Object? value = json[key];
  return value == null ? null : '$value';
}

int intOf(Json json, String key, [int fallback = 0]) {
  final Object? value = json[key];
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value) ?? fallback;
  return fallback;
}

bool boolOf(Json json, String key, [bool fallback = false]) {
  final Object? value = json[key];
  return value is bool ? value : fallback;
}

/// A money field.
///
/// **Read as a string, always** - see `core/format.dart`. Typing these as `double`
/// would let a float creep in at the edge of the system, which is the one place it
/// is hardest to notice. A number arriving where a string was expected is coerced
/// rather than rejected, because a 0 serialised by an older endpoint should not
/// blank a whole table.
String money(Json json, String key) {
  final Object? value = json[key];
  if (value == null) return '0';
  return '$value';
}

String? moneyOrNull(Json json, String key) {
  final Object? value = json[key];
  return value == null ? null : '$value';
}

List<T> listOf<T>(Json json, String key, T Function(Json) parse) {
  final Object? value = json[key];
  if (value is! List) return const <Never>[];
  return value
      .whereType<Map<dynamic, dynamic>>()
      .map((Map<dynamic, dynamic> item) => parse(item.cast<String, dynamic>()))
      .toList(growable: false);
}

List<String> stringList(Json json, String key) {
  final Object? value = json[key];
  if (value is! List) return const <String>[];
  return value.map((Object? item) => '$item').toList(growable: false);
}

Json mapOf(Json json, String key) {
  final Object? value = json[key];
  return value is Map ? value.cast<String, dynamic>() : <String, dynamic>{};
}
