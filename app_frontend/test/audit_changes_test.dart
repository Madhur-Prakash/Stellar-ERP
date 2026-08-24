import 'package:flutter_test/flutter_test.dart';
import 'package:personalerp_desktop/models/json.dart';
import 'package:personalerp_desktop/models/organization.dart';

/// Reading an audit entry's `changes`, which is not one shape.
///
/// The backend writes this column two different ways. Most callers go through its `diff()`
/// and produce `{field: {before, after}}`. Document upload, re-extract, and confirm-into-bill
/// use the same column as a flat snapshot: `{status: 'uploaded', duplicate_of: null}`.
///
/// Assuming the first shape broke both clients from the same misreading. The web app crashed
/// outright - `change.before` on a `null` throws, so a single uploaded document made the whole
/// audit page unrenderable. This client was luckier and merely wrong: its guard was
/// `if (entry.value is Map)`, so snapshots were dropped and an uploaded document showed an
/// audit row with nothing underneath it. Losing the record quietly is a poor showing for an
/// audit log.
///
/// Both shapes are handled rather than one being migrated, because audit rows are immutable:
/// entries of both kinds are already written and cannot be rewritten.
void main() {
  /// The exact payload behind the crash, taken from `document.uploaded` rows in a real
  /// database - including the null that did it.
  Json uploadedDocument() => <String, dynamic>{
    'id': 'audit-1',
    'action': 'document.uploaded',
    'severity': 'info',
    'summary': 'Uploaded invoice.pdf',
    'actor': <String, dynamic>{
      'email': 'priya@example.com',
      'name': 'Jhon Doe',
    },
    'ip_address': '172.20.0.1',
    'changes': <String, dynamic>{
      'status': 'uploaded',
      'engine': 'pdf_text_layer',
      'overall_confidence': '0.94',
      'duplicate_of': null,
    },
    'created_at': '2026-08-02T18:25:22Z',
  };

  Json memberRoleChanged() => <String, dynamic>{
    'id': 'audit-2',
    'action': 'member.role_changed',
    'severity': 'warning',
    'summary': 'Changed role',
    'actor': <String, dynamic>{
      'email': 'priya@example.com',
      'name': 'Jhon Doe',
    },
    'ip_address': null,
    'changes': <String, dynamic>{
      'role': <String, dynamic>{'before': 'Staff', 'after': 'Accountant'},
    },
    'created_at': '2026-08-02T18:30:00Z',
  };

  group('the snapshot shape', () {
    test('a null value is read without throwing', () {
      // The whole bug in one line. `duplicate_of` is null, and reading `.before` off it is
      // what took the web page down.
      final AuditEntry entry = AuditEntry.fromJson(uploadedDocument());
      expect(entry.changes['duplicate_of'], isNotNull);
      expect(entry.changes['duplicate_of']!.isDiff, isFalse);
      expect(entry.changes['duplicate_of']!.value, isNull);
    });

    test('every field survives rather than being dropped', () {
      // Previously the `is Map` guard threw all four of these away, leaving the entry with
      // an empty detail block.
      final AuditEntry entry = AuditEntry.fromJson(uploadedDocument());
      expect(entry.changes.keys, hasLength(4));
      expect(
        entry.changes.keys,
        containsAll(<String>['status', 'engine', 'duplicate_of']),
      );
    });

    test('a scalar is carried as a value, not as a diff', () {
      final AuditEntry entry = AuditEntry.fromJson(uploadedDocument());
      final AuditChange status = entry.changes['status']!;
      expect(status.isDiff, isFalse);
      expect(status.value, 'uploaded');
      // Nothing to strike through: there was no previous value recorded.
      expect(status.before, isNull);
      expect(status.after, isNull);
    });
  });

  group('the diff shape', () {
    test('a before/after pair is still read as a diff', () {
      final AuditEntry entry = AuditEntry.fromJson(memberRoleChanged());
      final AuditChange role = entry.changes['role']!;
      expect(role.isDiff, isTrue);
      expect(role.before, 'Staff');
      expect(role.after, 'Accountant');
    });

    test('a diff with a null half is still a diff', () {
      // Setting a field that had no value: `before` is null but the pair is genuine, and
      // rendering it as "- → value" is correct here.
      final Json json = memberRoleChanged();
      json['changes'] = <String, dynamic>{
        'gstin': <String, dynamic>{'before': null, 'after': '29ABCDE1234F1Z5'},
      };
      final AuditChange change = AuditEntry.fromJson(json).changes['gstin']!;
      expect(change.isDiff, isTrue);
      expect(change.before, isNull);
      expect(change.after, '29ABCDE1234F1Z5');
    });

    test('a nested object snapshot is a value, not an empty diff', () {
      // The reason the check is for the `before`/`after` keys rather than just "is it a
      // map". A snapshot can hold a nested payload, and calling that a diff would render
      // an empty "- → -" over real content.
      final Json json = memberRoleChanged();
      json['changes'] = <String, dynamic>{
        'extracted': <String, dynamic>{
          'invoice_number': 'INV-1',
          'total': '1200.00',
        },
      };
      final AuditChange change = AuditEntry.fromJson(
        json,
      ).changes['extracted']!;
      expect(change.isDiff, isFalse);
      expect(change.value, isA<Map<String, dynamic>>());
    });
  });

  test('an entry with no changes at all is fine', () {
    // Most auth events - sign-in, sign-out - record `{}`.
    final Json json = memberRoleChanged();
    json['changes'] = <String, dynamic>{};
    expect(AuditEntry.fromJson(json).changes, isEmpty);
  });
}
