import 'package:flutter_test/flutter_test.dart';
import 'package:stellarerp_desktop/core/card_number.dart';
import 'package:stellarerp_desktop/features/billing/accounts_panel.dart';
import 'package:stellarerp_desktop/models/billing.dart';
import 'package:stellarerp_desktop/widgets/app_select.dart';

/// Accounts, cards, and the one thing about them that is easy to get wrong.
///
/// **A debit card arrives from the API with the same `id` as the bank account it draws
/// on**, because it is not a separate place money lives. Everything in the first group
/// below exists to keep that fact from becoming a picker that cannot represent what the
/// user chose, or a transfer form that offers the same account twice under two names.
void main() {
  /// A card whose account is its own - what a credit card looks like.
  MoneyAccount creditCard({
    required String id,
    required String cardId,
    String name = 'HDFC Millennia ··4242',
  }) => MoneyAccount(
    id: id,
    name: name,
    isDefault: false,
    kind: MoneyAccountKind.creditCard,
    cardId: cardId,
    cardLast4: '4242',
  );

  group('the debit card / bank account collision', () {
    // The shape the API actually returns: cash and bank first, card entries appended,
    // and the debit card carrying the bank account's id.
    final MoneyAccount cash = MoneyAccount(
      id: 'acct-cash',
      name: 'Cash on Hand',
      isDefault: true,
      kind: MoneyAccountKind.cash,
    );
    final MoneyAccount bank = MoneyAccount(
      id: 'acct-bank',
      name: 'Primary Bank Account',
      isDefault: false,
      kind: MoneyAccountKind.bank,
    );
    final MoneyAccount debitCard = MoneyAccount(
      id: 'acct-bank', // <- the same account, deliberately
      name: 'SBI Debit ··1234',
      isDefault: false,
      kind: MoneyAccountKind.bank,
      cardId: 'card-debit',
      cardLast4: '1234',
    );
    final MoneyAccount credit = creditCard(
      id: 'acct-card-liability',
      cardId: 'card-credit',
    );

    final List<MoneyAccount> all = <MoneyAccount>[
      cash,
      bank,
      debitCard,
      credit,
    ];

    test('a debit card and its bank account get different picker keys', () {
      // The whole reason `key` exists. Were these equal, selecting the card in a
      // dropdown would silently snap the selection back to the bank account.
      expect(debitCard.id, bank.id);
      expect(debitCard.key, isNot(bank.key));
    });

    test('a non-card account keys on its own id', () {
      expect(bank.key, 'acct-bank');
      expect(cash.key, 'acct-cash');
    });

    test('a key resolves back to the account the API wants posted', () {
      // Posting is by account id, so both the card and the bank account must resolve to
      // the same id - that is what makes a debit card a way of *using* the account
      // rather than a second place the same money lives.
      expect(accountForKey(all, debitCard.key)?.id, 'acct-bank');
      expect(accountForKey(all, bank.key)?.id, 'acct-bank');
      expect(accountForKey(all, credit.key)?.id, 'acct-card-liability');
    });

    test(
      'an unknown key resolves to nothing rather than the first account',
      () {
        expect(accountForKey(all, 'gone'), isNull);
      },
    );

    test('a transfer offers each real account exactly once', () {
      final List<MoneyAccount> transferable = BillingOptions(
        categories: const <Category>[],
        moneyAccounts: all,
        cards: const <PaymentCard>[],
        today: '2026-08-02',
        currency: 'INR',
      ).transferableAccounts;

      // The debit card is gone; the credit card is not. Paying a card bill off a bank
      // account is the transfer people most want to record.
      expect(transferable.map((MoneyAccount a) => a.id), <String>[
        'acct-cash',
        'acct-bank',
        'acct-card-liability',
      ]);
      expect(
        transferable.any((MoneyAccount a) => a.cardId == 'card-debit'),
        isFalse,
      );
    });

    test('the survivor of a duplicated pair is the bank account, not the card', () {
      // Relies on the API's ordering. If that ever changed, the transfer form would
      // start naming an account "SBI Debit ··1234", which is the visible symptom.
      final MoneyAccount survivor =
          BillingOptions(
            categories: const <Category>[],
            moneyAccounts: all,
            cards: const <PaymentCard>[],
            today: '2026-08-02',
            currency: 'INR',
          ).transferableAccounts.firstWhere(
            (MoneyAccount a) => a.id == 'acct-bank',
          );
      expect(survivor.name, 'Primary Bank Account');
      expect(survivor.isCard, isFalse);
    });

    test('the picker separates money you have from money you may owe', () {
      final List<SelectGroup> groups = moneyAccountGroups(all);
      expect(groups.map((SelectGroup g) => g.label), <String>[
        'Cash & bank',
        'Cards',
      ]);
      // Both card entries land under "Cards" - including the debit card, which is a
      // card to the person using it even though it is a bank account to the ledger.
      expect(groups[0].options.length, 2);
      expect(groups[1].options.length, 2);
      expect(groups[1].options.map((SelectOption o) => o.value), <String>[
        'card-debit',
        'card-credit',
      ]);
    });
  });

  group('MoneyAccountKind', () {
    test('reads the wire values the API sends', () {
      expect(MoneyAccountKind.parse('cash'), MoneyAccountKind.cash);
      expect(MoneyAccountKind.parse('bank'), MoneyAccountKind.bank);
      expect(
        MoneyAccountKind.parse('credit_card'),
        MoneyAccountKind.creditCard,
      );
    });

    test('falls back to bank for anything unrecognised', () {
      // A server that grew a fourth kind should not make the picker unusable on an
      // older build, and `bank` claims less than `cash` does.
      expect(MoneyAccountKind.parse('crypto'), MoneyAccountKind.bank);
      expect(MoneyAccountKind.parse(null), MoneyAccountKind.bank);
    });

    test('only a credit card is not cash', () {
      expect(MoneyAccountKind.cash.isCard, isFalse);
      expect(MoneyAccountKind.bank.isCard, isFalse);
      expect(MoneyAccountKind.creditCard.isCard, isTrue);
    });
  });

  group('CardNetwork', () {
    test('names the schemes the way their holders do', () {
      expect(CardNetwork.parse('rupay').label, 'RuPay');
      expect(CardNetwork.parse('mastercard').label, 'Mastercard');
      expect(CardNetwork.parse('diners').label, 'Diners Club');
    });

    test('an unknown scheme reads as a card, not as an error', () {
      // The card works perfectly well; the software simply does not claim to recognise
      // the scheme. "Unknown" would suggest something is wrong with it.
      expect(CardNetwork.parse('elo'), CardNetwork.other);
      expect(CardNetwork.parse(null).label, 'Card');
    });
  });

  group('bank details on an account', () {
    test('the subtitle prefers the bank and holder over the account code', () {
      const MoneyAccount account = MoneyAccount(
        id: 'acct-bank',
        name: 'HDFC Current',
        isDefault: false,
        kind: MoneyAccountKind.bank,
        code: '1121',
        bankName: 'HDFC Bank',
        holderName: 'Jhon Doe',
      );
      expect(account.subtitle, 'HDFC Bank · Jhon Doe');
    });

    test('the code is the fallback when nothing has been recorded', () {
      // The seeded "Primary Bank Account" starts like this - created by the chart
      // template before anyone has said which bank it is.
      const MoneyAccount account = MoneyAccount(
        id: 'acct-bank',
        name: 'Primary Bank Account',
        isDefault: false,
        kind: MoneyAccountKind.bank,
        code: '1120',
      );
      expect(account.subtitle, '1120');
    });

    test('a half-filled account shows what it has', () {
      const MoneyAccount account = MoneyAccount(
        id: 'acct-bank',
        name: 'HDFC Current',
        isDefault: false,
        kind: MoneyAccountKind.bank,
        code: '1121',
        bankName: 'HDFC Bank',
      );
      expect(account.subtitle, 'HDFC Bank');
    });

    test('the picker payload carries the tail but never the whole number', () {
      final MoneyAccount account = MoneyAccount.fromJson(<String, dynamic>{
        'id': 'acct-bank',
        'name': 'HDFC Current',
        'is_default': false,
        'kind': 'bank',
        'code': '1121',
        'bank_name': 'HDFC Bank',
        'holder_name': 'Jhon Doe',
        'account_number_last4': '4321',
      });
      expect(account.accountNumberLast4, '4321');
      // There is no field on this model that could hold the full number, which is what
      // keeps it off the payload that every load of the billing screen fetches.
      expect(account.bankName, 'HDFC Bank');
    });

    test(
      'BankDetails carries the full number, because that is its whole job',
      () {
        final BankDetails details = BankDetails.fromJson(<String, dynamic>{
          'account_id': 'acct-bank',
          'bank_name': 'HDFC Bank',
          'holder_name': 'Jhon Doe',
          'account_number': '50100123454321',
          'account_number_last4': '4321',
        });
        // Kept in order to be quoted on an invoice and matched against a statement, so it
        // must survive the round trip intact rather than being masked here.
        expect(details.accountNumber, '50100123454321');
        expect(details.accountNumberLast4, '4321');
      },
    );

    test('an account with no details decodes to empty rather than throwing', () {
      // A cash box will never have any, and the API answers with nulls rather than a 404.
      final BankDetails details = BankDetails.fromJson(<String, dynamic>{
        'account_id': 'acct-cash',
        'bank_name': null,
        'holder_name': null,
        'account_number': null,
        'account_number_last4': null,
      });
      expect(details.accountNumber, isNull);
      expect(details.bankName, isNull);
    });
  });

  group('PaymentCard', () {
    test('carries no number - only what a receipt already prints', () {
      final PaymentCard card = PaymentCard.fromJson(<String, dynamic>{
        'id': 'card-1',
        'label': 'HDFC Millennia',
        'kind': 'credit',
        'network': 'visa',
        'last4': '4242',
        'account_id': 'acct-1',
        'account_name': 'HDFC Millennia',
        'is_active': true,
      });
      expect(card.displayName, 'HDFC Millennia ··4242');
      expect(card.kind, CardKind.credit);
      // Four digits as a String: a card ending 0042 is not the number 42.
      expect(card.last4, isA<String>());
    });

    test('the holder name is kept, unlike the number', () {
      final PaymentCard card = PaymentCard.fromJson(<String, dynamic>{
        'id': 'card-1',
        'label': 'HDFC Millennia',
        'kind': 'credit',
        'network': 'visa',
        'last4': '4242',
        'account_id': 'acct-1',
        'account_name': 'HDFC Millennia',
        'is_active': true,
        'holder_name': 'Jhon Doe',
      });
      // PCI DSS permits retaining a cardholder name; it is the number and the
      // authentication data that may not be kept. A name alone cannot transact.
      expect(card.holderName, 'Jhon Doe');
      expect(card.subtitle, 'Visa · Jhon Doe · HDFC Millennia');
    });

    test('the subtitle omits a holder name that was never given', () {
      final PaymentCard card = PaymentCard.fromJson(<String, dynamic>{
        'id': 'card-1',
        'label': 'HDFC Millennia',
        'kind': 'credit',
        'network': 'visa',
        'last4': '4242',
        'account_id': 'acct-1',
        'account_name': 'HDFC Millennia',
        'is_active': true,
      });
      expect(card.holderName, isNull);
      // No empty segment and no stray separator.
      expect(card.subtitle, 'Visa · HDFC Millennia');
    });

    test('keeps a leading zero in the last four digits', () {
      final PaymentCard card = PaymentCard.fromJson(<String, dynamic>{
        'id': 'card-2',
        'label': 'SBI',
        'kind': 'debit',
        'network': 'rupay',
        'last4': '0042',
        'account_id': 'acct-2',
        'account_name': 'Primary Bank Account',
        'is_active': true,
      });
      expect(card.last4, '0042');
      expect(card.displayName, 'SBI ··0042');
    });
  });

  group('the card number, which is never kept', () {
    // Every scheme's published test number. These are the ones issuers hand out
    // precisely so they can appear in a test file - none is a real account.
    const String visa = '4111111111111111';
    const String mastercard = '5555555555554444';
    const String amex = '378282246310005';
    const String discover = '6011111111111117';
    // RuPay publishes none, so this is a valid-Luhn number in its 6521 range. The final
    // 7 is computed, not chosen: the twelve zeros contribute nothing, 6 doubles to 12
    // and digit-sums to 3, and 3 + 5 + 4 + 1 = 13 needs a 7 to reach a multiple of ten.
    const String rupay = '6521000000000007';

    test('accepts every published test number', () {
      for (final String number in <String>[
        visa,
        mastercard,
        amex,
        discover,
        rupay,
      ]) {
        expect(passesLuhn(number), isTrue, reason: number);
      }
    });

    test('catches the single-digit typo it exists to catch', () {
      // Changing one digit of a valid number must fail. This is the entire class of
      // mistake someone makes copying a number off a card.
      expect(passesLuhn('4111111111111112'), isFalse);
      expect(passesLuhn('4111111111111121'), isFalse);
    });

    test('catches a transposition', () {
      expect(passesLuhn('378282246310050'), isFalse);
    });

    test('rejects anything that is not digits', () {
      expect(passesLuhn(''), isFalse);
      expect(passesLuhn('4111-1111-1111-1111'), isFalse);
      expect(passesLuhn('four'), isFalse);
    });

    test('strips the separators a person actually types', () {
      expect(normaliseCardNumber('4111 1111 1111 1111'), visa);
      expect(normaliseCardNumber('4111-1111-1111-1111'), visa);
      expect(normaliseCardNumber('  4111 1111-1111 1111  '), visa);
      expect(passesLuhn(normaliseCardNumber('4111 1111 1111 1111')), isTrue);
    });

    test('plausibility is about shape, not about the check digit', () {
      // The two are separate so a form can tell "that is not a card number" from "that
      // is a card number with a typo in it" - different things to say to someone.
      expect(isPlausibleCardNumber('4111111111111112'), isTrue);
      expect(passesLuhn('4111111111111112'), isFalse);
    });

    test('rejects numbers outside the lengths ISO/IEC 7812 allows', () {
      expect(isPlausibleCardNumber('41111111111'), isFalse); // 11 digits
      expect(isPlausibleCardNumber('411111111111'), isTrue); // 12, the minimum
      expect(
        isPlausibleCardNumber('4111111111111111111'),
        isTrue,
      ); // 19, the maximum
      expect(isPlausibleCardNumber('41111111111111111111'), isFalse); // 20
    });
  });
}
