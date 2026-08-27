/// Proof-ledger contracts.
///
/// Two conventions carried over from the rest of this client, both because a Dart
/// `double` is the same IEEE-754 trap a JavaScript number is:
///
/// * `debitMinor` is a **string**. It is a count of paise and can exceed 2^53 over
///   a business's lifetime, so it never passes through a numeric type on the way
///   in.
/// * `sealedAt` is nullable, and the nullability is load-bearing. It is the
///   *network's* timestamp, so until the network has answered there is no time to
///   show - and rendering the device's clock there would undermine the exact claim
///   the screen exists to make.
library;

import 'json.dart';

enum SealStatus {
  pending('pending'),
  submitted('submitted'),
  confirmed('confirmed'),
  failed('failed');

  const SealStatus(this.wire);

  final String wire;

  static SealStatus parse(String value) => SealStatus.values.firstWhere(
    (SealStatus s) => s.wire == value,
    // An unknown status from a newer server reads as "queued" rather than
    // throwing: a desktop build in the field is often older than the API it talks
    // to, and a parse error would blank the whole screen over one enum member.
    orElse: () => SealStatus.pending,
  );
}

enum SealTrigger {
  periodClose('period_close'),
  schedule('schedule'),
  manual('manual'),
  backfill('backfill');

  const SealTrigger(this.wire);

  final String wire;

  static SealTrigger parse(String value) => SealTrigger.values.firstWhere(
    (SealTrigger t) => t.wire == value,
    orElse: () => SealTrigger.manual,
  );
}

enum SealCadence {
  onPeriodClose('on_period_close', 'Only when a period closes'),
  daily('daily', 'Every day, and when a period closes'),
  manual('manual', 'Only when I press the button');

  const SealCadence(this.wire, this.label);

  final String wire;
  final String label;

  static SealCadence parse(String value) => SealCadence.values.firstWhere(
    (SealCadence c) => c.wire == value,
    orElse: () => SealCadence.daily,
  );
}

class Seal {
  const Seal({
    required this.id,
    required this.seq,
    required this.status,
    required this.trigger,
    required this.merkleRoot,
    required this.prevRoot,
    required this.entryCount,
    required this.debitMinor,
    required this.entryDateFrom,
    required this.entryDateTo,
    required this.attempts,
    required this.treeDepth,
    this.network,
    this.contractId,
    this.txHash,
    this.sealedAt,
    this.confirmedAt,
    this.lastError,
    this.explorerUrl,
  });

  final String id;
  final int seq;
  final SealStatus status;
  final SealTrigger trigger;

  final String merkleRoot;
  final String prevRoot;
  final int entryCount;

  /// Total debits in minor units. A string - see the library docstring.
  final String debitMinor;

  /// The accounting dates this batch touches. Local only; these do not tile,
  /// because a bill dated in March can arrive in April.
  final String entryDateFrom;
  final String entryDateTo;

  final int attempts;

  /// Sibling hashes needed to prove one entry from this seal.
  final int treeDepth;

  final String? network;
  final String? contractId;
  final String? txHash;

  /// **The network's** timestamp, not the device's, and an ISO string rather than
  /// a `DateTime`: `formatDateTime` renders it in the *organization's* zone, and
  /// parsing to a Dart `DateTime` here would force the machine's zone instead.
  /// Null until confirmed - there is no time to show before the network answers.
  final String? sealedAt;
  final String? confirmedAt;
  final String? lastError;
  final String? explorerUrl;

  bool get isConfirmed => status == SealStatus.confirmed;

  factory Seal.fromJson(Json json) => Seal(
    id: str(json, 'id'),
    seq: intOf(json, 'seq'),
    status: SealStatus.parse(str(json, 'status')),
    trigger: SealTrigger.parse(str(json, 'trigger')),
    merkleRoot: str(json, 'merkle_root'),
    prevRoot: str(json, 'prev_root'),
    entryCount: intOf(json, 'entry_count'),
    debitMinor: money(json, 'debit_minor'),
    entryDateFrom: str(json, 'entry_date_from'),
    entryDateTo: str(json, 'entry_date_to'),
    attempts: intOf(json, 'attempts'),
    treeDepth: intOf(json, 'tree_depth'),
    network: strOrNull(json, 'network'),
    contractId: strOrNull(json, 'contract_id'),
    txHash: strOrNull(json, 'tx_hash'),
    sealedAt: strOrNull(json, 'sealed_at'),
    confirmedAt: strOrNull(json, 'confirmed_at'),
    lastError: strOrNull(json, 'last_error'),
    explorerUrl: strOrNull(json, 'explorer_url'),
  );
}

class ChainHealth {
  const ChainHealth({
    required this.reachable,
    this.head,
    this.root,
    this.entries,
    this.sealedAt,
    this.admin,
    this.agreesWithLocal,
    this.error,
  });

  final bool reachable;
  final int? head;
  final String? root;
  final int? entries;
  final String? sealedAt;
  final String? admin;

  /// `false` is the condition worth shouting about: the chain and this database
  /// disagree, and the chain is the authority.
  final bool? agreesWithLocal;
  final String? error;

  factory ChainHealth.fromJson(Json json) => ChainHealth(
    reachable: boolOf(json, 'reachable'),
    head: json['head'] == null ? null : intOf(json, 'head'),
    root: strOrNull(json, 'root'),
    entries: json['entries'] == null ? null : intOf(json, 'entries'),
    sealedAt: strOrNull(json, 'sealed_at'),
    admin: strOrNull(json, 'admin'),
    agreesWithLocal: json['agrees_with_local'] as bool?,
    error: strOrNull(json, 'error'),
  );
}

class AttestationStatus {
  const AttestationStatus({
    required this.enabled,
    required this.configured,
    required this.ready,
    required this.cadence,
    required this.effectiveSealHour,
    required this.timezone,
    this.sealHour,
    required this.externalSigner,
    required this.sealsConfirmed,
    required this.entriesSealed,
    required this.unsealedEntries,
    required this.chain,
    required this.warnings,
    this.network,
    this.contractId,
    this.contractUrl,
    this.orgNamespace,
    this.signerPublicKey,
    this.registeredAt,
    this.oldestUnsealedAt,
    this.daysUnsealed,
    this.lastSeal,
    this.openSeal,
  });

  final bool enabled;
  final bool configured;
  final bool ready;
  final SealCadence cadence;

  /// The hour actually in force for a daily seal, 0-23. Never null, so the
  /// screen can state a time whether or not this organization has chosen one.
  final int effectiveSealHour;

  /// Which clock [effectiveSealHour] is on. "Seal at 01:00" is not an
  /// instruction until the zone is named, and it is the organization's zone
  /// rather than the server's.
  final String timezone;

  /// The hour this organization chose, or null when it is following the
  /// install's default. Distinguished from [effectiveSealHour] because the
  /// screen says so - "following the server default" is worth knowing.
  final int? sealHour;

  /// True when the signing key is held outside this server - a stronger posture,
  /// and distinguished from "not configured" because a null column makes the two
  /// look identical while they mean opposite things about what a seal proves.
  final bool externalSigner;

  final int sealsConfirmed;
  final int entriesSealed;
  final int unsealedEntries;
  final ChainHealth chain;

  /// Already written for a human, worst first. Render in the order given.
  final List<String> warnings;

  final String? network;
  final String? contractId;
  final String? contractUrl;
  final String? orgNamespace;
  final String? signerPublicKey;
  final String? registeredAt;
  final String? oldestUnsealedAt;

  /// Age of the oldest unsealed entry. The figure that matters: a growing number
  /// is what sealing silently breaking looks like.
  final double? daysUnsealed;

  final Seal? lastSeal;
  final Seal? openSeal;

  bool get isSealing => enabled && ready;

  factory AttestationStatus.fromJson(Json json) => AttestationStatus(
    enabled: boolOf(json, 'enabled'),
    configured: boolOf(json, 'configured'),
    ready: boolOf(json, 'ready'),
    cadence: SealCadence.parse(str(json, 'cadence')),
    effectiveSealHour: intOf(json, 'effective_seal_hour', 1),
    timezone: strOrNull(json, 'timezone') ?? 'UTC',
    sealHour: (json['seal_hour'] as num?)?.toInt(),
    externalSigner: boolOf(json, 'external_signer'),
    sealsConfirmed: intOf(json, 'seals_confirmed'),
    entriesSealed: intOf(json, 'entries_sealed'),
    unsealedEntries: intOf(json, 'unsealed_entries'),
    chain: ChainHealth.fromJson(mapOf(json, 'chain')),
    warnings: stringList(json, 'warnings'),
    network: strOrNull(json, 'network'),
    contractId: strOrNull(json, 'contract_id'),
    contractUrl: strOrNull(json, 'contract_url'),
    orgNamespace: strOrNull(json, 'org_namespace'),
    signerPublicKey: strOrNull(json, 'signer_public_key'),
    registeredAt: strOrNull(json, 'registered_at'),
    oldestUnsealedAt: strOrNull(json, 'oldest_unsealed_at'),
    daysUnsealed: (json['days_unsealed'] as num?)?.toDouble(),
    lastSeal: json['last_seal'] == null
        ? null
        : Seal.fromJson(mapOf(json, 'last_seal')),
    openSeal: json['open_seal'] == null
        ? null
        : Seal.fromJson(mapOf(json, 'open_seal')),
  );
}

class SealPage {
  const SealPage({
    required this.items,
    required this.hasMore,
    required this.continuous,
    this.nextCursor,
  });

  final List<Seal> items;
  final bool hasMore;

  /// Computed server-side. A break in the chain is the most important thing this
  /// list can report, and three clients deriving it three ways would eventually
  /// disagree about it.
  final bool continuous;
  final String? nextCursor;

  factory SealPage.fromJson(Json json) => SealPage(
    items: listOf(json, 'items', Seal.fromJson),
    hasMore: boolOf(json, 'has_more'),
    continuous: boolOf(json, 'continuous', true),
    nextCursor: strOrNull(json, 'next_cursor'),
  );
}

class SealNowResult {
  const SealNowResult({required this.message, this.seal});

  final String message;
  final Seal? seal;

  factory SealNowResult.fromJson(Json json) => SealNowResult(
    message: str(json, 'message'),
    seal: json['seal'] == null ? null : Seal.fromJson(mapOf(json, 'seal')),
  );
}

class ReconcileResult {
  const ReconcileResult({
    required this.reconciled,
    this.chainHead,
    this.localHead,
    this.agrees,
    this.reason,
  });

  final bool reconciled;
  final int? chainHead;
  final int? localHead;
  final bool? agrees;
  final String? reason;

  factory ReconcileResult.fromJson(Json json) => ReconcileResult(
    reconciled: boolOf(json, 'reconciled'),
    chainHead: json['chain_head'] == null ? null : intOf(json, 'chain_head'),
    localHead: json['local_head'] == null ? null : intOf(json, 'local_head'),
    agrees: json['agrees'] as bool?,
    reason: strOrNull(json, 'reason'),
  );
}
