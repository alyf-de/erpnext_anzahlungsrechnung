# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

"""Tax breakdown for **Down Payment Invoice** print, read off the invoice's own
``item_wise_tax_details`` (populated for free by ``AccountsController.on_update``).
"""

import re
from collections import defaultdict

from frappe.utils import flt


def get_down_payment_invoice_print_tax_rows(doc) -> list[dict]:
	"""Jinja helper: one row per tax rate with net, tax, gross, label, and ``tax_description``."""
	return _rows_from_buckets(_aggregate_item_wise_by_rate(doc))


def get_down_payment_invoice_item_rows(doc) -> list[dict]:
	"""Jinja helper: one row per item, with that item's own rate and tax, for the print body.

	Read the rate/tax off the item's own ``item_wise_tax_details`` rows, not off
	``item_tax_template`` -- the template is usually unset (the ordinary setup keeps the rate
	on the tax row instead), which made every body row print "0% / 0,00" regardless of what
	the footer's per-rate buckets showed.
	"""
	rows = []
	item_wise_tax_details = doc.get("item_wise_tax_details") or []
	for item in doc.get("items") or []:
		taxed = [r for r in item_wise_tax_details if r.item_row == item.name and flt(r.amount) != 0]
		rate = flt(taxed[0].rate) if taxed else 0.0
		tax = flt(sum(flt(r.amount) for r in taxed))
		net = flt(item.net_amount)
		rows.append(
			{
				"item_name": item.item_name,
				"description": item.description,
				"net_amount": net,
				"rate_label": _format_rate_label(rate),
				"tax_amount": tax,
			}
		)
	return rows


def _aggregate_item_wise_by_rate(doc) -> dict[float, dict]:
	"""Build VAT buckets without double-counting.

	``item_wise_tax_details`` has one row per (invoice item, tax row). Rows where that tax row
	does not apply still carry the item net as ``taxable_amount`` with ``amount`` 0 and ``rate`` 0 --
	they must not be merged into the real 0% VAT bucket.
	"""
	by_item: dict[str, list] = defaultdict(list)
	legacy_rows: list = []
	for row in doc.get("item_wise_tax_details") or []:
		item_row = row.get("item_row")
		if item_row:
			by_item[item_row].append(row)
		else:
			legacy_rows.append(row)

	items_by_name = {i.name: i for i in (doc.get("items") or []) if getattr(i, "name", None)}
	out: dict[float, dict] = {}

	for row in legacy_rows:
		rate = flt(row.rate)
		cell = out.setdefault(rate, _new_rate_bucket())
		cell["taxable"] += flt(row.taxable_amount)
		cell["tax"] += flt(row.amount)
		_set_bucket_tax_description(doc, cell, row.get("tax_row"))

	for item_row, rows in by_item.items():
		item = items_by_name.get(item_row)
		if not item:
			continue
		net = flt(item.net_amount)
		taxed = [r for r in rows if flt(r.amount) != 0]
		if taxed:
			for r in taxed:
				rate = flt(r.rate)
				cell = out.setdefault(rate, _new_rate_bucket())
				cell["taxable"] += flt(r.taxable_amount)
				cell["tax"] += flt(r.amount)
				_set_bucket_tax_description(doc, cell, r.get("tax_row"))
		else:
			rate = _rate_from_item_tax_template(item)
			cell = out.setdefault(rate, _new_rate_bucket())
			cell["taxable"] += net

	for rate, cell in out.items():
		if not cell.get("tax_description"):
			desc = _tax_description_for_rate(doc, rate)
			if desc:
				cell["tax_description"] = desc

	return out


def _rows_from_buckets(buckets: dict[float, dict]) -> list[dict]:
	rates_sorted = sorted(buckets.keys(), key=lambda r: (-flt(r), flt(r)))
	rows: list[dict] = []
	for rate in rates_sorted:
		b = buckets[rate]
		net = flt(b["taxable"])
		tax = flt(b["tax"])
		rows.append(
			{
				"rate": rate,
				"rate_label": _format_rate_label(rate),
				"net_amount": net,
				"tax_amount": tax,
				"gross_amount": flt(net + tax),
				"tax_description": b.get("tax_description"),
			}
		)
	return rows


def _new_rate_bucket() -> dict:
	return {"taxable": 0.0, "tax": 0.0, "tax_description": None}


def _set_bucket_tax_description(doc, cell: dict, tax_row_name: str | None):
	if cell.get("tax_description"):
		return
	desc = _tax_charge_description(doc, tax_row_name)
	if desc:
		cell["tax_description"] = desc


def _tax_charge_description(doc, tax_row_name: str | None) -> str | None:
	if not tax_row_name:
		return None
	for t in doc.get("taxes") or []:
		if t.name == tax_row_name:
			return (t.description or "").strip() or None
	return None


def _tax_description_for_rate(doc, rate: float) -> str | None:
	taxes = list(doc.get("taxes") or [])
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


def _rate_from_item_tax_template(item) -> float:
	label = (getattr(item, "item_tax_template", None) or "").strip()
	if not label:
		return 0.0
	m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*%", label)
	if m:
		return flt(m.group(1))
	return 0.0


def _format_rate_label(rate: float) -> str:
	r = flt(rate)
	if r == int(r):
		return f"{int(r)}%"
	return f"{r:g}%"
