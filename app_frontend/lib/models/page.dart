import 'json.dart';

/// Pagination envelopes.
///
/// Two of them, because the backend deliberately uses two strategies. List screens
/// get offset pages, which give a page count a person can navigate. The audit trail
/// gets a cursor, because it is append-heavy: an offset both degrades with depth and
/// shifts rows under the reader as new events land.
class PageMeta {
  const PageMeta({
    required this.page,
    required this.pageSize,
    required this.totalItems,
    required this.totalPages,
    required this.hasNext,
    required this.hasPrevious,
  });

  final int page;
  final int pageSize;
  final int totalItems;
  final int totalPages;
  final bool hasNext;
  final bool hasPrevious;

  factory PageMeta.fromJson(Json json) => PageMeta(
    page: intOf(json, 'page', 1),
    pageSize: intOf(json, 'page_size', 25),
    totalItems: intOf(json, 'total_items'),
    totalPages: intOf(json, 'total_pages'),
    hasNext: boolOf(json, 'has_next'),
    hasPrevious: boolOf(json, 'has_previous'),
  );

  static const PageMeta empty = PageMeta(
    page: 1,
    pageSize: 25,
    totalItems: 0,
    totalPages: 0,
    hasNext: false,
    hasPrevious: false,
  );
}

class Paged<T> {
  const Paged({required this.items, required this.meta});

  final List<T> items;
  final PageMeta meta;

  factory Paged.fromJson(Json json, T Function(Json) parse) => Paged<T>(
    items: listOf<T>(json, 'items', parse),
    meta: PageMeta.fromJson(mapOf(json, 'meta')),
  );

  static Paged<T> empty<T>() =>
      Paged<T>(items: const <Never>[], meta: PageMeta.empty);
}

class CursorPage<T> {
  const CursorPage({
    required this.items,
    this.nextCursor,
    required this.hasMore,
  });

  final List<T> items;
  final String? nextCursor;
  final bool hasMore;

  factory CursorPage.fromJson(Json json, T Function(Json) parse) =>
      CursorPage<T>(
        items: listOf<T>(json, 'items', parse),
        nextCursor: strOrNull(json, 'next_cursor'),
        hasMore: boolOf(json, 'has_more'),
      );
}

/// `{ "message": ..., "detail": ... }` - the shape most write endpoints answer with.
class MessageResponse {
  const MessageResponse({required this.message, this.detail});

  final String message;
  final String? detail;

  factory MessageResponse.fromJson(Json json) => MessageResponse(
    message: str(json, 'message'),
    detail: strOrNull(json, 'detail'),
  );
}
