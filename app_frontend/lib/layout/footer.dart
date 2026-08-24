import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../core/env.dart';
import '../core/format.dart';
import '../models/analytics.dart';
import '../models/auth.dart';
import '../state/auth_controller.dart';
import '../state/data_providers.dart';
import '../theme/oklch.dart';
import '../theme/tokens.dart';
import '../widgets/primitives.dart';
import 'app_shell.dart';
import 'nav.dart';

/// The application footer.
///
/// An application footer is not a marketing site's footer - nobody arrives here to
/// browse - so every line has to answer a question someone actually has while using
/// the product:
///
/// * **Do my books still balance?** The only place in the app that says so on *every*
///   screen. A control that stays invisible until it fails offers no reassurance while
///   it holds, and this is the one fact an accounting product should never make you go
///   and look for.
/// * **What period am I in?** Indian financial years run April to March, entries get
///   backdated, and "which year does this land in" is a question a footer can answer
///   for free. The month comes from the server, because the organization owns that rule.
/// * **Where else can I go?** The same navigation as the sidebar, from the same list,
///   so the two cannot disagree - and permission-filtered, so it never offers a screen
///   the user would be redirected away from.
/// * **Whose data is this?** Self-hosted is the whole premise.
///
/// Deliberately absent: a version number, a support address, and any external link.
/// There is no endpoint that reports a version (`/health` withholds it on purpose) and
/// no support channel to point at, so inventing either would be a footer that lies.
class AppFooter extends ConsumerWidget {
  const AppFooter({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final AuthState auth = ref.watch(authControllerProvider);
    final OrganizationSummary? organization = auth.organization;
    final bool canSeeMoney = auth.can('report:read');

    final ControlChecks? checks = canSeeMoney && organization != null
        ? ref.watch(controlChecksProvider).valueOrNull
        : null;
    final PeriodOptions? periods = organization != null
        ? ref.watch(periodOptionsProvider).valueOrNull
        : null;

    // Only sections with something the user may actually open. An empty column is
    // worse than a missing one: it reads as a screen that failed to load.
    final List<NavSection> columns =
        <NavSection>[
              for (final NavSection section in navSections)
                NavSection(
                  title: section.title,
                  items: section.items
                      .where(
                        (NavItem item) =>
                            item.isBuilt &&
                            (item.permission == null ||
                                auth.can(item.permission!)),
                      )
                      .toList(growable: false),
                ),
            ]
            .where((NavSection section) => section.items.isNotEmpty)
            .toList(growable: false);

    final TextStyle body = TextStyle(
      fontSize: 12,
      color: t.contentMuted,
      height: 1.6,
    );
    final TextStyle heading = TextStyle(
      fontSize: 11,
      fontWeight: FontWeight.w600,
      letterSpacing: 0.6,
      color: t.contentSecondary,
    );

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.only(left: 32, right: 32, top: 28, bottom: 24),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: t.border)),
      ),
      child: DefaultTextStyle(
        style: body,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Wrap(
              spacing: 48,
              runSpacing: 32,
              alignment: WrapAlignment.spaceBetween,
              children: <Widget>[
                // Identity and premise.
                SizedBox(
                  width: 300,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        Env.appName,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: t.contentSecondary,
                        ),
                      ),
                      const SizedBox(height: 4),
                      const Text(
                        'Self-hosted double-entry accounting. Your data stays in your '
                        'own database, on your own machine.',
                      ),
                      if (organization != null) ...<Widget>[
                        const SizedBox(height: 10),
                        Text.rich(
                          TextSpan(
                            children: <InlineSpan>[
                              TextSpan(
                                text: organization.name,
                                style: TextStyle(color: t.contentSecondary),
                              ),
                              TextSpan(
                                text: ' · you are ${organization.roleName}',
                              ),
                            ],
                          ),
                        ),
                      ],
                    ],
                  ),
                ),

                // Navigation, from the sidebar's own list.
                for (final NavSection section in columns)
                  SizedBox(
                    width: 140,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(section.title.toUpperCase(), style: heading),
                        const SizedBox(height: 8),
                        for (final NavItem item in section.items)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: AppTextLink(
                              label: item.label,
                              fontSize: 12,
                              fontWeight: FontWeight.w400,
                              colour: t.contentMuted,
                              // `hover:text-content transition-colors` on the web.
                              hoverColour: t.content,
                              onTap: () => context.go(item.path),
                            ),
                          ),
                      ],
                    ),
                  ),

                // Facts about the books, which is what makes this a footer for an
                // accounting product rather than a list of links.
                SizedBox(
                  width: 260,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text('YOUR BOOKS', style: heading),
                      const SizedBox(height: 8),
                      if (checks != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 6),
                          child: checks.allAgree
                              ? Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  spacing: 6,
                                  children: <Widget>[
                                    Padding(
                                      padding: const EdgeInsets.only(top: 3),
                                      child: Icon(
                                        LucideIcons.shieldCheck,
                                        size: 14,
                                        color: t.success,
                                      ),
                                    ),
                                    const Expanded(
                                      child: Text('Ledger and records agree'),
                                    ),
                                  ],
                                )
                              // Linked, not merely flagged: the point of surfacing a
                              // discrepancy on every screen is that it is one click
                              // from the page explaining it.
                              : MouseRegion(
                                  cursor: SystemMouseCursors.click,
                                  child: GestureDetector(
                                    onTap: () => context.go('/analytics'),
                                    child: Row(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      spacing: 6,
                                      children: <Widget>[
                                        Padding(
                                          padding: const EdgeInsets.only(
                                            top: 3,
                                          ),
                                          child: Icon(
                                            LucideIcons.triangleAlert,
                                            size: 14,
                                            color: t.danger,
                                          ),
                                        ),
                                        Expanded(
                                          child: Text(
                                            'They do not agree - review',
                                            style: TextStyle(
                                              color: t.danger,
                                              fontWeight: FontWeight.w500,
                                            ),
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),
                                ),
                        ),
                      if (periods != null) ...<Widget>[
                        Padding(
                          padding: const EdgeInsets.only(bottom: 6),
                          child: Text(
                            'Financial year: '
                            '${fiscalYearSpan(periods.fiscalYearStartMonth)}',
                          ),
                        ),
                        Padding(
                          padding: const EdgeInsets.only(bottom: 6),
                          child: Text(
                            'Books dated ${formatDate(periods.today)} '
                            'in your own timezone',
                          ),
                        ),
                      ],
                      const Text(
                        'Amounts kept as exact decimals, never rounded in transit',
                      ),
                    ],
                  ),
                ),
              ],
            ),

            const SizedBox(height: 28),
            Container(
              padding: const EdgeInsets.only(top: 20),
              decoration: BoxDecoration(
                border: Border(top: BorderSide(color: t.border.at(0.6))),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Expanded(
                    child: Text(
                      'This software is self-hosted ERP and accounting platform',
                    ),
                  ),
                  const SizedBox(width: 24),
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    spacing: 6,
                    children: <Widget>[
                      Icon(
                        LucideIcons.command,
                        size: 14,
                        color: t.contentMuted,
                      ),
                      const Text('Press'),
                      KeyHint(label: '$shortcutModifier K'),
                      const Text('to jump anywhere'),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// "1 April to 31 March", from the month the year opens in.
///
/// Derived rather than hardcoded to April: the backend lets an organization start its
/// year in any month, and a footer confidently stating the wrong period is worse than
/// no footer. Month names come from the locale, so this needs no table to maintain.
String fiscalYearSpan(int startMonth) {
  // The last month is the one before the start, wrapping December → January.
  final int endMonth = startMonth == 1 ? 12 : startMonth - 1;

  // February is 28 days in three years out of four, so naming a day would be wrong in
  // the fourth. Every other month has a fixed length and reads better with one.
  final String closing = endMonth == 2
      ? 'the end of ${monthName(2)}'
      : '${DateTime(2001, endMonth + 1, 0).day} ${monthName(endMonth)}';

  return '1 ${monthName(startMonth)} to $closing';
}
