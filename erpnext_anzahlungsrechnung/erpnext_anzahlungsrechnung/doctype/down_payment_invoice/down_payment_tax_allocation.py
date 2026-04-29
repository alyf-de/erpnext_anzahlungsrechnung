# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

"""Proportional tax breakdown for Down Payment Invoice print (from Sales Order item_wise_tax_details).

Gross rounding vs. ``down_payment_amount`` is applied on the row with the largest positive tax amount
(not the last rate row), so a mis-sized 0% bucket does not absorb the whole adjustment as a bogus
negative tax line. After changing this module, reload workers / run ``bench migrate`` so the site
picks up the code.
"""

import re
from collections import defaultdict

import frappe
from frappe.utils import flt


def get_down_payment_invoice_print_tax_rows(sales_order: str | None, down_payment_amount) -> list[dict]:
	"""Jinja helper: one row per tax rate with net, tax, gross, label, and ``tax_description`` from **Sales Order** ``taxes`` when known."""
	if not sales_order:
		return []
	so = frappe.get_cached_doc("Sales Order", sales_order)
	return build_tax_rows_for_down_payment(so, flt(down_payment_amount))


def build_tax_rows_for_down_payment(sales_order, down_payment_amount: float) -> list[dict]:
	buckets = _aggregate_item_wise_by_rate(sales_order)
	if not buckets:
		return _fallback_rows(down_payment_amount)
	return _scale_buckets_to_down_payment(sales_order, buckets, flt(down_payment_amount))


def _fallback_rows(down_payment_amount: float) -> list[dict]:
	if not down_payment_amount:
		return []
	return [
		{
			"rate": 0.0,
			"rate_label": _format_rate_label(0.0),
			"net_amount": flt(down_payment_amount),
			"tax_amount": 0.0,
			"gross_amount": flt(down_payment_amount),
			"tax_description": None,
		}
	]


def _aggregate_item_wise_by_rate(sales_order) -> dict[float, dict]:
	"""Build VAT buckets without double-counting.

	``item_wise_tax_details`` has one row per (order item, sales tax row). Rows where
	that tax row does not apply still carry the item net as ``taxable_amount`` with
	``amount`` 0 and ``rate`` 0 — they must not be merged into the real 0% VAT bucket.
	"""
	by_item: dict[str, list] = defaultdict(list)
	legacy_rows: list = []
	for row in sales_order.get("item_wise_tax_details") or []:
		item_row = row.get("item_row")
		if item_row:
			by_item[item_row].append(row)
		else:
			legacy_rows.append(row)

	items_by_name = {i.name: i for i in (sales_order.get("items") or []) if getattr(i, "name", None)}
	out: dict[float, dict] = {}

	for row in legacy_rows:
		rate = flt(row.rate)
		cell = out.setdefault(rate, _new_rate_bucket())
		cell["taxable"] += flt(row.taxable_amount)
		cell["tax"] += flt(row.amount)
		_set_bucket_tax_description(sales_order, cell, row.get("tax_row"))

	for item_row, rows in by_item.items():
		so_item = items_by_name.get(item_row)
		if not so_item:
			continue
		net = flt(so_item.net_amount)
		taxed = [r for r in rows if flt(r.amount) != 0]
		if taxed:
			for r in taxed:
				rate = flt(r.rate)
				cell = out.setdefault(rate, _new_rate_bucket())
				cell["taxable"] += flt(r.taxable_amount)
				cell["tax"] += flt(r.amount)
				_set_bucket_tax_description(sales_order, cell, r.get("tax_row"))
		else:
			rate = _rate_from_item_tax_template(so_item)
			cell = out.setdefault(rate, _new_rate_bucket())
			cell["taxable"] += net
			cell["tax"] += 0.0

	for rate, cell in out.items():
		if not cell.get("tax_description"):
			desc = _tax_description_for_rate(sales_order, rate)
			if desc:
				cell["tax_description"] = desc

	return out


def _new_rate_bucket() -> dict:
	return {"taxable": 0.0, "tax": 0.0, "tax_description": None}


def _set_bucket_tax_description(sales_order, cell: dict, tax_row_name: str | None):
	if cell.get("tax_description"):
		return
	desc = _tax_charge_description(sales_order, tax_row_name)
	if desc:
		cell["tax_description"] = desc


def _tax_charge_description(sales_order, tax_row_name: str | None) -> str | None:
	if not tax_row_name:
		return None
	for t in sales_order.get("taxes") or []:
		if t.name == tax_row_name:
			return (t.description or "").strip() or None
	return None


def _tax_description_for_rate(sales_order, rate: float) -> str | None:
	taxes = list(sales_order.get("taxes") or [])
	r = flt(rate)
	if r == 0:
		pat = re.compile(r"(?<!\d)0\s*%")
		for t in taxes:
			desc = t.description or ""
			if pat.search(desc):
				return desc.strip()
		return None
	label = _format_rate_label(r)
	alt = label.replace("%", " %")
	for t in taxes:
		desc = (t.description or "").strip()
		if not desc:
			continue
		dl = desc.lower()
		if label.lower() in dl or alt.lower() in dl:
			return desc
	return None


def _rate_from_item_tax_template(so_item) -> float:
	label = (getattr(so_item, "item_tax_template", None) or "").strip()
	if not label:
		return 0.0
	m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*%", label)
	if m:
		return flt(m.group(1))
	return 0.0


def _scale_buckets_to_down_payment(
	sales_order, buckets: dict[float, dict], down_payment_amount: float
) -> list[dict]:
	G = flt(sales_order.grand_total)
	D = flt(down_payment_amount)
	if D <= 0:
		return []
	if G <= 0:
		return _fallback_rows(D)

	factor = D / G
	precision = frappe.get_precision("Sales Order", "grand_total")

	rates_sorted = sorted(buckets.keys(), key=lambda r: (-flt(r), flt(r)))

	rows: list[dict] = []
	for rate in rates_sorted:
		b = buckets[rate]
		net = flt(b["taxable"] * factor, precision)
		tax = flt(b["tax"] * factor, precision)
		rows.append(
			{
				"rate": rate,
				"rate_label": _format_rate_label(rate),
				"net_amount": net,
				"tax_amount": tax,
				"gross_amount": flt(net + tax, precision),
				"tax_description": b.get("tax_description"),
			}
		)

	_round_gross_total(rows, D, precision)
	return rows


def _round_gross_total(rows: list[dict], target_gross: float, precision: int):
	if not rows:
		return
	sum_gross = sum(flt(r["net_amount"]) + flt(r["tax_amount"]) for r in rows)
	diff = flt(target_gross - sum_gross, precision)
	if not diff:
		return
	idx = _rounding_adjust_row_index(rows)
	row = rows[idx]
	row["tax_amount"] = flt(row["tax_amount"] + diff, precision)
	row["gross_amount"] = flt(row["net_amount"] + row["tax_amount"], precision)


def _rounding_adjust_row_index(rows: list[dict]) -> int:
	"""Prefer the row with the largest positive tax so gross correction does not land on a 0% line by default."""
	best_i = len(rows) - 1
	best_tax = 0.0
	for i, r in enumerate(rows):
		t = flt(r["tax_amount"])
		if t > best_tax:
			best_tax = t
			best_i = i
	if best_tax > 0:
		return best_i
	return len(rows) - 1


def _format_rate_label(rate: float) -> str:
	r = flt(rate)
	if r == int(r):
		return f"{int(r)}%"
	return f"{r:g}%"
