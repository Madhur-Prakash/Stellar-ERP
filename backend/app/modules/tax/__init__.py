"""Shared tax domain - GST computation used by both sales and purchasing.

Lives here rather than inside either module because the arithmetic is identical in
both directions: a purchase and a sale both split tax by place of supply, and both
apply discount before tax. Only the *ledger account* differs - output tax is a
liability, input tax is an asset.

Putting it in ``sales`` and importing it from ``purchasing`` would invert the
dependency (purchasing does not depend on selling), and duplicating it would let
the two drift until a purchase and a sale of the same item disagreed on tax.
"""
