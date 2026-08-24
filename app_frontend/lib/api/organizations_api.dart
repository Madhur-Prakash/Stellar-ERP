import '../core/api_client.dart';
import '../models/auth.dart';
import '../models/json.dart';
import '../models/organization.dart';
import '../models/page.dart';

/// Organization, member, role, and audit bindings.
///
/// Note that none of these URLs carries an organization id. The active organization
/// comes from the signed access token, so `/organizations/current` always means "the
/// one this session is operating in" - there is no id in the URL for a client to
/// tamper with, which is what makes cross-tenant access structurally impossible
/// rather than merely checked.
class OrganizationsApi {
  const OrganizationsApi(this._client);

  final ApiClient _client;

  // --- Organizations ---
  Future<List<OrganizationListItem>> list() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/organizations',
    );
    return raw
        .cast<Json>()
        .map(OrganizationListItem.fromJson)
        .toList(growable: false);
  }

  Future<Organization> create({required String name}) async =>
      Organization.fromJson(
        await _client.post<Json>(
          '/organizations',
          body: <String, dynamic>{'name': name},
        ),
      );

  Future<Organization> current() async =>
      Organization.fromJson(await _client.get<Json>('/organizations/current'));

  Future<Organization> update({
    String? name,
    String? gstin,
    String? currency,
    String? timezone,
    int? fiscalYearStartMonth,
  }) async => Organization.fromJson(
    await _client.patch<Json>(
      '/organizations/current',
      body: <String, dynamic>{
        if (name != null && name.isNotEmpty) 'name': name,
        if (gstin != null && gstin.isNotEmpty) 'gstin': gstin,
        'currency': ?currency,
        'timezone': ?timezone,
        'fiscal_year_start_month': ?fiscalYearStartMonth,
      },
    ),
  );

  /// Delete the active organization. Owner only; the server refuses anyone else.
  ///
  /// Soft on the server side - the books stay recoverable, because statutory
  /// retention means a company's ledger cannot simply vanish - but it disappears
  /// from the owner's account immediately and there is no self-service undo.
  Future<void> deleteCurrent() =>
      _client.delete<Json>('/organizations/current');

  /// Leave the active organization.
  ///
  /// Anyone except the owner, whom the server refuses - they must hand over or
  /// delete instead. The membership row is hard-deleted, so rejoining needs a
  /// fresh invitation.
  Future<void> leaveCurrent() =>
      _client.post<Json>('/organizations/current/leave');

  // --- Members ---
  Future<List<Member>> listMembers() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/organizations/current/members',
    );
    return raw.cast<Json>().map(Member.fromJson).toList(growable: false);
  }

  Future<Member> updateMember(
    String memberId, {
    String? roleId,
    String? jobTitle,
  }) async => Member.fromJson(
    await _client.patch<Json>(
      '/organizations/current/members/$memberId',
      body: <String, dynamic>{'role_id': ?roleId, 'job_title': ?jobTitle},
    ),
  );

  Future<void> suspendMember(String memberId) =>
      _client.post<Json>('/organizations/current/members/$memberId/suspend');

  Future<void> reactivateMember(String memberId) =>
      _client.post<Json>('/organizations/current/members/$memberId/reactivate');

  Future<void> removeMember(String memberId) =>
      _client.delete<Json>('/organizations/current/members/$memberId');

  // --- Invitations ---
  Future<List<Invitation>> listInvitations() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/organizations/current/invitations',
    );
    return raw.cast<Json>().map(Invitation.fromJson).toList(growable: false);
  }

  Future<Invitation> invite({required String email, String? roleId}) async =>
      Invitation.fromJson(
        await _client.post<Json>(
          '/organizations/current/invitations',
          body: <String, dynamic>{
            'email': email,
            if (roleId != null && roleId.isNotEmpty) 'role_id': roleId,
          },
        ),
      );

  Future<void> resendInvitation(String invitationId) => _client.post<Json>(
    '/organizations/current/invitations/$invitationId/resend',
  );

  Future<void> revokeInvitation(String invitationId) =>
      _client.delete<Json>('/organizations/current/invitations/$invitationId');

  /// Unauthenticated - the recipient has not signed in yet.
  Future<InvitationPreview> previewInvitation(String token) async =>
      InvitationPreview.fromJson(
        await _client.get<Json>('/invitations/$token'),
      );

  Future<MessageResponse> acceptInvitation(String token) async =>
      MessageResponse.fromJson(
        await _client.post<Json>(
          '/invitations/accept',
          body: <String, dynamic>{'token': token},
        ),
      );

  // --- Roles ---
  Future<List<Role>> listRoles() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>('/roles');
    return raw.cast<Json>().map(Role.fromJson).toList(growable: false);
  }

  Future<PermissionCatalogue> permissionCatalogue() async =>
      PermissionCatalogue.fromJson(
        await _client.get<Json>('/roles/permissions'),
      );

  Future<Role> createRole({
    required String name,
    required List<String> permissions,
    String? description,
  }) async => Role.fromJson(
    await _client.post<Json>(
      '/roles',
      body: <String, dynamic>{
        'name': name,
        'permissions': permissions,
        if (description != null && description.isNotEmpty)
          'description': description,
      },
    ),
  );

  /// Rename a custom role or change what it can do.
  ///
  /// Only the fields passed are sent: the server treats an absent key as "leave
  /// it alone", so sending nulls for the untouched ones would clear them.
  /// Permission changes apply immediately to everyone holding the role.
  Future<Role> updateRole(
    String roleId, {
    String? name,
    List<String>? permissions,
    String? description,
  }) async => Role.fromJson(
    await _client.patch<Json>(
      '/roles/$roleId',
      body: <String, dynamic>{
        'name': ?name,
        'permissions': ?permissions,
        'description': ?description,
      },
    ),
  );

  Future<void> deleteRole(String roleId) =>
      _client.delete<Json>('/roles/$roleId');

  // --- Audit ---
  Future<CursorPage<AuditEntry>> listAudit({
    String? cursor,
    int limit = 25,
    String? action,
    String? severity,
  }) async => CursorPage<AuditEntry>.fromJson(
    await _client.get<Json>(
      '/audit',
      query: <String, dynamic>{
        'limit': limit,
        'cursor': cursor,
        'action': action,
        'severity': severity,
      },
    ),
    AuditEntry.fromJson,
  );

  Future<List<String>> auditActions() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/audit/actions',
    );
    return raw.map((Object? value) => '$value').toList(growable: false);
  }
}

/// Profile bindings - scoped to the caller's own record.
class UsersApi {
  const UsersApi(this._client);

  final ApiClient _client;

  Future<UserProfile> me() async =>
      UserProfile.fromJson(await _client.get<Json>('/users/me'));

  Future<UserProfile> updateProfile({
    String? fullName,
    String? phone,
  }) async => UserProfile.fromJson(
    await _client.patch<Json>(
      '/users/me',
      body: <String, dynamic>{
        'full_name': ?fullName,
        // Sent even when cleared, so a number can be removed rather than only
        // changed.
        'phone': ?phone,
      },
    ),
  );

  Future<UserStats> stats() async =>
      UserStats.fromJson(await _client.get<Json>('/users/me/stats'));
}
