import '../core/api_client.dart';
import '../models/json.dart';
import '../models/trust.dart';

/// Proof-ledger bindings.
///
/// Note what is *absent*: there is no `verifyBundle` here. Verification is
/// deliberately not a desktop feature - it belongs to the counterparty, in a
/// browser, on a page that talks to the Stellar network directly. A verifier who
/// had to install a desktop app to check an invoice would not check the invoice,
/// and one who trusted this client's verdict would have gained nothing over
/// trusting the business that sent the file.
///
/// What the desktop client does instead is what a business needs: see whether its
/// books are sealed, seal them, and export a proof to hand over.
class TrustApi {
  const TrustApi(this._client);

  final ApiClient _client;

  Future<AttestationStatus> status() async => AttestationStatus.fromJson(
    await _client.get<Json>('/attestation/status'),
  );

  Future<SealPage> seals({String? cursor, int limit = 20}) async =>
      SealPage.fromJson(
        await _client.get<Json>(
          '/attestation/seals',
          query: <String, dynamic>{'cursor': cursor, 'limit': limit},
        ),
      );

  /// Switch sealing on: create and fund a signer, and open the on-chain book.
  ///
  /// Idempotent server-side, so a retry after a closed window converges rather
  /// than failing - which matters more on desktop than on the web, because a
  /// native window closing mid-request looks like nothing happened at all.
  Future<AttestationStatus> enable({
    SealCadence cadence = SealCadence.daily,
    bool fundOnTestnet = true,
  }) async => AttestationStatus.fromJson(
    await _client.post<Json>(
      '/attestation/enable',
      body: <String, dynamic>{
        'cadence': cadence.wire,
        'fund_on_testnet': fundOnTestnet,
      },
    ),
  );

  Future<AttestationStatus> disable() async => AttestationStatus.fromJson(
    await _client.post<Json>('/attestation/disable', body: <String, dynamic>{}),
  );

  Future<AttestationStatus> setCadence(SealCadence cadence) async =>
      AttestationStatus.fromJson(
        await _client.patch<Json>(
          '/attestation/cadence',
          body: <String, dynamic>{'cadence': cadence.wire},
        ),
      );

  Future<SealNowResult> sealNow() async => SealNowResult.fromJson(
    await _client.post<Json>('/attestation/seals', body: <String, dynamic>{}),
  );

  /// Ask the chain what it holds and make the local database agree.
  ///
  /// Never the reverse: the chain is the authority on what has been sealed.
  Future<ReconcileResult> reconcile() async => ReconcileResult.fromJson(
    await _client.post<Json>(
      '/attestation/reconcile',
      body: <String, dynamic>{},
    ),
  );

  /// The proof bundle for one entry, as an opaque map.
  ///
  /// Returned unparsed on purpose. Its shape is defined by its own `format` tag and
  /// read by third-party verifiers, possibly written against a later version -
  /// modelling it here would give two answers to "what is in a bundle?" and this
  /// client only ever passes it through to a file.
  Future<Json> proof(String journalEntryId) async =>
      await _client.get<Json>('/attestation/proof/$journalEntryId');
}
