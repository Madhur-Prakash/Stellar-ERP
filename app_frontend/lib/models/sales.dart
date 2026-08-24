import 'json.dart';

/// Sales contracts. Money is always a `String` - see `models/accounting.dart`.
class Customer {
  const Customer({
    required this.id,
    required this.code,
    required this.name,
    this.email,
    this.phone,
    this.gstin,
    this.billingCity,
    required this.paymentTermsDays,
    required this.creditLimit,
    required this.isActive,
  });

  final String id;
  final String code;
  final String name;
  final String? email;
  final String? phone;
  final String? gstin;
  final String? billingCity;
  final int paymentTermsDays;
  final String creditLimit;
  final bool isActive;

  factory Customer.fromJson(Json json) => Customer(
    id: str(json, 'id'),
    code: strOrNull(json, 'code') ?? '',
    name: str(json, 'name'),
    email: strOrNull(json, 'email'),
    phone: strOrNull(json, 'phone'),
    gstin: strOrNull(json, 'gstin'),
    billingCity: strOrNull(json, 'billing_city'),
    paymentTermsDays: intOf(json, 'payment_terms_days', 30),
    creditLimit: money(json, 'credit_limit'),
    isActive: boolOf(json, 'is_active', true),
  );
}

class SalesLine {
  const SalesLine({
    required this.id,
    required this.description,
    required this.quantity,
    required this.unitPrice,
    required this.taxRate,
    required this.taxableAmount,
    required this.taxAmount,
    required this.lineTotal,
  });

  final String id;
  final String description;
  final String quantity;
  final String unitPrice;
  final String taxRate;
  final String taxableAmount;
  final String taxAmount;
  final String lineTotal;

  factory SalesLine.fromJson(Json json) => SalesLine(
    id: str(json, 'id'),
    description: str(json, 'description'),
    quantity: money(json, 'quantity'),
    unitPrice: money(json, 'unit_price'),
    taxRate: money(json, 'tax_rate'),
    taxableAmount: money(json, 'taxable_amount'),
    taxAmount: money(json, 'tax_amount'),
    lineTotal: money(json, 'line_total'),
  );
}

/// One line as the create endpoints take it.
///
/// Quantities and prices are strings on the way out too: a line typed as `12.5`
/// must reach the server as `"12.5"`, not as whatever a `double` round-trips to.
class SalesLineInput {
  const SalesLineInput({
    required this.description,
    required this.quantity,
    required this.unitPrice,
    required this.taxRate,
  });

  final String description;
  final String quantity;
  final String unitPrice;
  final String taxRate;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'description': description,
    'quantity': quantity,
    'unit_price': unitPrice,
    'tax_rate': taxRate,
  };
}

class Invoice {
  const Invoice({
    required this.id,
    required this.invoiceNumber,
    required this.customerName,
    required this.invoiceDate,
    required this.dueDate,
    required this.status,
    required this.grandTotal,
    required this.paidAmount,
    required this.outstanding,
    required this.isOverdue,
    required this.daysOverdue,
    required this.taxTotal,
    required this.taxableTotal,
    required this.lines,
  });

  final String id;
  final String invoiceNumber;
  final String customerName;
  final String invoiceDate;
  final String dueDate;

  /// `draft`, `posted`, `partially_paid`, `paid`, or `cancelled`.
  final String status;
  final String grandTotal;
  final String paidAmount;
  final String outstanding;
  final bool isOverdue;
  final int daysOverdue;
  final String taxTotal;
  final String taxableTotal;
  final List<SalesLine> lines;

  factory Invoice.fromJson(Json json) => Invoice(
    id: str(json, 'id'),
    invoiceNumber: str(json, 'invoice_number'),
    customerName: strOrNull(json, 'customer_name') ?? '',
    invoiceDate: str(json, 'invoice_date'),
    dueDate: str(json, 'due_date'),
    status: strOrNull(json, 'status') ?? 'draft',
    grandTotal: money(json, 'grand_total'),
    paidAmount: money(json, 'paid_amount'),
    outstanding: money(json, 'outstanding'),
    isOverdue: boolOf(json, 'is_overdue'),
    daysOverdue: intOf(json, 'days_overdue'),
    taxTotal: money(json, 'tax_total'),
    taxableTotal: money(json, 'taxable_total'),
    lines: listOf(json, 'lines', SalesLine.fromJson),
  );
}

class Payment {
  const Payment({
    required this.id,
    required this.paymentNumber,
    required this.customerName,
    required this.paymentDate,
    required this.amount,
    required this.unallocatedAmount,
    required this.method,
  });

  final String id;
  final String paymentNumber;
  final String customerName;
  final String paymentDate;
  final String amount;
  final String unallocatedAmount;
  final String method;

  factory Payment.fromJson(Json json) => Payment(
    id: str(json, 'id'),
    paymentNumber: str(json, 'payment_number'),
    customerName: strOrNull(json, 'customer_name') ?? '',
    paymentDate: str(json, 'payment_date'),
    amount: money(json, 'amount'),
    unallocatedAmount: money(json, 'unallocated_amount'),
    method: strOrNull(json, 'method') ?? 'other',
  );
}

class AgeingBucket {
  const AgeingBucket({
    required this.label,
    required this.amount,
    required this.count,
  });

  final String label;
  final String amount;

  /// `invoice_count` on receivables, `bill_count` on payables - the same idea, so
  /// one field reads it from whichever key is present.
  final int count;

  factory AgeingBucket.fromJson(Json json) => AgeingBucket(
    label: str(json, 'label'),
    amount: money(json, 'amount'),
    count: json.containsKey('invoice_count')
        ? intOf(json, 'invoice_count')
        : intOf(json, 'bill_count'),
  );
}

class Ageing {
  const Ageing({
    required this.asOf,
    required this.buckets,
    required this.totalOutstanding,
    required this.totalOverdue,
  });

  final String asOf;
  final List<AgeingBucket> buckets;
  final String totalOutstanding;
  final String totalOverdue;

  factory Ageing.fromJson(Json json) => Ageing(
    asOf: str(json, 'as_of'),
    buckets: listOf(json, 'buckets', AgeingBucket.fromJson),
    totalOutstanding: money(json, 'total_outstanding'),
    totalOverdue: money(json, 'total_overdue'),
  );
}

/// The subset of a created customer or supplier the party form needs back.
///
/// `Customer` and `Supplier` are not interchangeable - a supplier has `city` where
/// a customer has `billing_city` - so the shared form types its result to what both
/// genuinely have rather than to a union every caller would then have to narrow.
class CreatedParty {
  const CreatedParty({
    required this.id,
    required this.name,
    required this.code,
  });

  final String id;
  final String name;
  final String code;

  factory CreatedParty.fromJson(Json json) => CreatedParty(
    id: str(json, 'id'),
    name: str(json, 'name'),
    code: strOrNull(json, 'code') ?? '',
  );
}
