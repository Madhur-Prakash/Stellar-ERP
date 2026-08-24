import '../core/api_client.dart';
import '../models/accounting.dart';
import '../models/analytics.dart';
import '../models/json.dart';

/// Analytics bindings.
class AnalyticsApi {
  const AnalyticsApi(this._client);

  final ApiClient _client;

  Future<PeriodOptions> periods() async =>
      PeriodOptions.fromJson(await _client.get<Json>('/analytics/periods'));

  Future<Dashboard> dashboard([Period period = Period.thisMonth]) async =>
      Dashboard.fromJson(
        await _client.get<Json>(
          '/analytics/dashboard',
          query: <String, dynamic>{'period': period.wire},
        ),
      );

  /// Explicit dates override the preset, so a chart can match a filtered report.
  Future<Trend> trend([
    Period period = Period.last12Months,
    DateRange? range,
  ]) async => Trend.fromJson(
    await _client.get<Json>(
      '/analytics/trend',
      query: range != null
          ? range.query
          : <String, dynamic>{'period': period.wire},
    ),
  );

  Future<Ranking> topCustomers([
    Period period = Period.thisFiscalYear,
    int limit = 5,
  ]) async => Ranking.fromJson(
    await _client.get<Json>(
      '/analytics/top-customers',
      query: <String, dynamic>{'period': period.wire, 'limit': limit},
    ),
  );

  Future<Ranking> topProducts([
    Period period = Period.thisFiscalYear,
    int limit = 5,
  ]) async => Ranking.fromJson(
    await _client.get<Json>(
      '/analytics/top-products',
      query: <String, dynamic>{'period': period.wire, 'limit': limit},
    ),
  );

  Future<ControlChecks> controlChecks({String? asOf}) async =>
      ControlChecks.fromJson(
        await _client.get<Json>(
          '/analytics/control-checks',
          query: <String, dynamic>{'as_of': asOf},
        ),
      );
}
