# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

"""Backfill ``items`` / ``taxes`` / ``item_wise_tax_details`` on existing **Down Payment Invoice**
documents that predate the items/taxes-table refactor (see PLAN.md / PLAN-AMENDMENTS.md).

Design (PLAN.md §6, PLAN-AMENDMENTS A7):

- The legacy derivation (``build_tax_rows_for_down_payment`` /  ``_aggregate_item_wise_by_rate`` /
  the pre-refactor body of ``get_income_tax_totals_for_down_payment_invoice``) is **vendored
  verbatim** into this module as private helpers. A later cleanup of ``down_payment_tax_allocation``
  must not be able to silently change history — this patch must keep reproducing the exact numbers
  that were printed on a legacy document, forever.
- Legacy columns (``down_payment_amount``, ``total_sales_order_amount``, ``position_name``,
  ``position_description``) are read with **raw SQL**, not ``doc.<field>`` — those fields are
  removed from the DocType JSON in a later step, but Frappe never drops the DB column on migrate,
  so the data survives and this patch stays independent of that step's ordering.
- **Guard, never throw.** A malformed / mis-configured legacy document (unresolvable tax account,
  a purged Sales Order, a residual that does not reconcile) is skipped and logged; it must never
  block ``bench migrate``.
- Migrated ``taxes`` rows use ``charge_type = "On Net Total"``, *not* ``"Actual"`` as PLAN.md §6
  originally specified. ``"Actual"`` distributes its ``tax_amount`` across *every* item by net share,
  regardless of each item's own rate for that tax account, and ``adjust_rounding_in_item_wise_tax_
  details`` only counts an item's share when its own rate for that account is non-zero -- for any
  document with more than one ``(income_account, rate)`` bucket that combination can never
  reconcile with itself, so re-validating a migrated draft (or its amended copy) always threw
  ``"Item Wise Tax Details do not match with Taxes and Charges"``. ``"On Net Total"`` computes each
  item's share from its own rate and is always self-consistent on re-validation. The numbers stored
  by this patch are still bit-identical to what was printed as long as the document is never
  re-saved; a later edit-and-save (or amend) may recompute this row's ``tax_amount`` by a rounding
  cent, which is expected and acceptable -- only the frozen, untouched state needs to match exactly.
- Insertion order inside one DPI is fixed by a data dependency: ``Down Payment Invoice Item`` rows
  first, then ``Sales Taxes and Charges`` rows, and only then ``Item Wise Tax Detail`` rows — its
  ``item_row`` / ``tax_row`` are the child *row names* of the two tables above, so those names must
  exist before ``Item Wise Tax Detail`` rows can reference them.
"""

import re
from collections import defaultdict

import frappe
from frappe.model.document import bulk_insert
from frappe.utils import flt

# ---------------------------------------------------------------------------
# Vendored legacy derivation (frozen copy of down_payment_tax_allocation.py /
# down_payment_invoice_accounting.py as they were before the refactor). Do not
# "fix" anything found in here to match the new engine -- these functions
# exist only to reproduce old numbers exactly.
# ---------------------------------------------------------------------------


def _new_rate_bucket() -> dict:
	return {"taxable": 0.0, "tax": 0.0, "tax_description": None}


def _format_rate_label(rate: float) -> str:
	r = flt(rate)
	if r == int(r):
		return f"{int(r)}%"
	return f"{r:g}%"


def _rate_from_item_tax_template(so_item) -> float:
	label = (getattr(so_item, "item_tax_template", None) or "").strip()
	if not label:
		return 0.0
	m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*%", label)
	if m:
		return flt(m.group(1))
	return 0.0


def _tax_charge_description(sales_order, tax_row_name: str | None) -> str | None:
	if not tax_row_name:
		return None
	for t in sales_order.get("taxes") or []:
		if t.name == tax_row_name:
			return (t.description or "").strip() or None
	return None


def _set_bucket_tax_description(sales_order, cell: dict, tax_row_name: str | None):
	if cell.get("tax_description"):
		return
	desc = _tax_charge_description(sales_order, tax_row_name)
	if desc:
		cell["tax_description"] = desc


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


def _aggregate_item_wise_by_rate(sales_order) -> dict[float, dict]:
	"""Rate-only VAT buckets, exactly as the pre-refactor print derivation built them."""
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

	for rate, cell in out.items():
		if not cell.get("tax_description"):
			desc = _tax_description_for_rate(sales_order, rate)
			if desc:
				cell["tax_description"] = desc

	return out


def _rounding_adjust_row_index(rows: list[dict]) -> int:
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


def _build_tax_rows_for_down_payment(sales_order, down_payment_amount: float) -> list[dict]:
	buckets = _aggregate_item_wise_by_rate(sales_order)
	if not buckets:
		return _fallback_rows(down_payment_amount)
	return _scale_buckets_to_down_payment(sales_order, buckets, flt(down_payment_amount))


def _get_income_tax_totals(company, sales_order, down_payment_amount):
	"""Pre-refactor body of ``get_income_tax_totals_for_down_payment_invoice``, minus the DPI arg."""
	rows = _build_tax_rows_for_down_payment(sales_order, down_payment_amount)
	G = flt(sales_order.grand_total)
	D = flt(down_payment_amount)
	income_totals = defaultdict(float)
	income_cc = {}
	income_proj = {}
	if not D or not G:
		return income_totals, defaultdict(float), income_cc, income_proj, {}, rows

	factor = D / G
	for item in sales_order.get("items") or []:
		acc = getattr(item, "income_account", None)
		if not acc:
			continue
		amt = flt(flt(item.net_amount) * factor, item.precision("net_amount"))
		if amt:
			income_totals[acc] += amt
			income_cc.setdefault(acc, item.cost_center)
			income_proj.setdefault(acc, item.project or sales_order.project)

	target_net = sum(flt(r["net_amount"]) for r in rows)
	cur_net = sum(income_totals.values())
	diff = flt(target_net - cur_net, frappe.get_precision("Sales Order", "net_total") or 2)
	if abs(diff) > 0.0001 and income_totals:
		big = max(income_totals.keys(), key=lambda k: income_totals[k])
		income_totals[big] += diff

	dp_map = _get_company_down_payment_map(company)
	tax_totals = defaultdict(float)
	tax_cc = {}
	unresolved_rate = None
	for r in rows:
		rate = flt(r["rate"])
		tax_amt = flt(r["tax_amount"])
		if not tax_amt:
			continue
		tax_acc = None
		for cfg in dp_map.values():
			if abs(flt(cfg.get("tax_rate")) - rate) <= 0.011:
				tax_acc = cfg.get("tax_account")
				break
		if not tax_acc:
			unresolved_rate = rate
			continue
		tax_totals[tax_acc] += tax_amt
		tax_cc.setdefault(tax_acc, _default_cost_center(company))

	if unresolved_rate is not None:
		frappe.throw(f"Unresolvable tax account for rate {unresolved_rate}%")

	return income_totals, tax_totals, income_cc, income_proj, tax_cc, rows


def _get_company_down_payment_map(company):
	rows = frappe.get_all(
		"Company Down Payment Account",
		filters={"parent": company, "parenttype": "Company", "parentfield": "custom_down_payment_accounts"},
		fields=["income_account", "tax_rate", "tax_account", "received_down_payment_account"],
	)
	return {r["income_account"]: r for r in rows}


def _default_cost_center(company):
	return frappe.db.get_value("Company", company, "cost_center")


# ---------------------------------------------------------------------------
# New: (income_account, rate) buckets -- the shape the new Down Payment
# Invoice Item rows need, that the rate-only buckets above never carried.
# ---------------------------------------------------------------------------


def _aggregate_item_wise_by_income_and_rate(sales_order):
	"""Like ``_aggregate_item_wise_by_rate`` but keyed by ``(income_account, rate)``.

	Returns ``(buckets, has_unattributable_rows)`` -- the second value is True when the Sales
	Order has ``item_wise_tax_details`` rows without an ``item_row`` link (pre-item_wise_tax_details
	era data); such rows cannot be attributed to an income account and the caller must skip the DPI.
	"""
	by_item: dict[str, list] = defaultdict(list)
	has_unattributable_rows = False
	for row in sales_order.get("item_wise_tax_details") or []:
		item_row = row.get("item_row")
		if item_row:
			by_item[item_row].append(row)
		else:
			has_unattributable_rows = True

	items_by_name = {i.name: i for i in (sales_order.get("items") or []) if getattr(i, "name", None)}
	out: dict[tuple, dict] = {}

	def bucket(income_account, rate):
		return out.setdefault(
			(income_account, rate),
			{"taxable": 0.0, "tax": 0.0, "item_tax_template": None, "cost_center": None, "project": None},
		)

	for item_row, rows in by_item.items():
		so_item = items_by_name.get(item_row)
		if not so_item:
			continue
		income_account = getattr(so_item, "income_account", None)
		if not income_account:
			has_unattributable_rows = True
			continue
		net = flt(so_item.net_amount)
		taxed = [r for r in rows if flt(r.amount) != 0]
		if taxed:
			for r in taxed:
				rate = flt(r.rate)
				b = bucket(income_account, rate)
				b["taxable"] += flt(r.taxable_amount)
				b["tax"] += flt(r.amount)
				b["item_tax_template"] = b["item_tax_template"] or getattr(so_item, "item_tax_template", None)
				b["cost_center"] = b["cost_center"] or so_item.cost_center
				b["project"] = b["project"] or (so_item.project or sales_order.project)
		else:
			rate = _rate_from_item_tax_template(so_item)
			b = bucket(income_account, rate)
			b["taxable"] += net
			b["item_tax_template"] = b["item_tax_template"] or getattr(so_item, "item_tax_template", None)
			b["cost_center"] = b["cost_center"] or so_item.cost_center
			b["project"] = b["project"] or (so_item.project or sales_order.project)

	return out, has_unattributable_rows


# ---------------------------------------------------------------------------
# Patch entry point
# ---------------------------------------------------------------------------


def execute():
	skipped = []
	migrated = 0

	rows = frappe.get_all(
		"Down Payment Invoice",
		fields=["name", "docstatus", "sales_order", "company"],
		order_by="creation",
	)
	for i, row in enumerate(rows):
		try:
			if _migrate_one(row.name, row.docstatus, row.sales_order, row.company):
				migrated += 1
		except Exception:
			frappe.db.rollback()
			skipped.append(row.name)
			frappe.log_error(
				title="backfill_down_payment_invoice_items: skipped",
				message=f"{row.name}: {frappe.get_traceback()}",
			)
		if (i + 1) % 200 == 0:
			frappe.db.commit()

	frappe.db.commit()
	print(
		f"backfill_down_payment_invoice_items: migrated {migrated}, skipped {len(skipped)}"
		+ (f" ({', '.join(skipped)})" if skipped else "")
	)


def _migrate_one(name: str, docstatus: int, sales_order: str | None, company: str | None) -> bool:
	# 1. Idempotency.
	if frappe.db.exists("Down Payment Invoice Item", {"parent": name}):
		return False

	# 2. Raw-read legacy columns (survive the field removal in a later step).
	legacy = frappe.db.get_value(
		"Down Payment Invoice",
		name,
		["down_payment_amount", "position_name", "position_description", "total_sales_order_amount"],
		as_dict=True,
	)
	down_payment_amount = flt(legacy.down_payment_amount)
	if not sales_order or not company or not down_payment_amount:
		return False

	# 3. Load the Sales Order (skip + log on a purged order -- A7.2).
	so = frappe.get_doc("Sales Order", sales_order)

	# 4. Per-(income_account, rate) buckets for the item rows.
	item_buckets, has_unattributable_rows = _aggregate_item_wise_by_income_and_rate(so)
	if has_unattributable_rows or not item_buckets:
		raise ValueError(f"{name}: Sales Order {sales_order} has no attributable item_wise_tax_details")

	# income/tax totals + rate-only rows (tax account resolution throws internally -- A7.1).
	_income_totals, _tax_totals, _icc, _iproj, _tcc, rate_rows = _get_income_tax_totals(
		company, so, down_payment_amount
	)
	dp_map = _get_company_down_payment_map(company)

	G = flt(so.grand_total)
	D = down_payment_amount
	factor = D / G if G else 0.0
	item_precision = frappe.get_precision("Down Payment Invoice Item", "net_amount") or 2

	# Round each (income_account, rate) bucket net, then push the residual against the
	# rate-only target (rate_rows) onto the largest bucket -- same "diff onto the biggest
	# bucket" trick the legacy code applied to income_totals, so sum(item net) lands
	# exactly on sum(rate_rows net) and therefore net_total + total_taxes == down_payment_amount.
	bucket_keys = sorted(item_buckets.keys(), key=lambda k: (k[0] or "", -flt(k[1]), flt(k[1])))
	bucket_net = {k: flt(item_buckets[k]["taxable"] * factor, item_precision) for k in bucket_keys}
	target_net = flt(sum(flt(r["net_amount"]) for r in rate_rows), item_precision)
	cur_net = flt(sum(bucket_net.values()), item_precision)
	diff = flt(target_net - cur_net, item_precision)
	if diff and bucket_net:
		biggest = max(bucket_net, key=lambda k: bucket_net[k])
		bucket_net[biggest] = flt(bucket_net[biggest] + diff, item_precision)

	# tax_amount per rate, keyed the same way rate_rows are (rate -> row).
	tax_by_rate = {flt(r["rate"]): flt(r["tax_amount"]) for r in rate_rows}
	desc_by_rate = {flt(r["rate"]): r.get("tax_description") for r in rate_rows}

	# 5a. Down Payment Invoice Item rows.
	item_docs = []
	item_name_by_bucket = {}
	for idx, key in enumerate(bucket_keys, start=1):
		income_account, rate = key
		net = bucket_net[key]
		b = item_buckets[key]
		item = frappe.new_doc("Down Payment Invoice Item")
		item.update(
			{
				"item_name": legacy.position_name,
				"description": legacy.position_description if idx == 1 else None,
				"qty": 1,
				"rate": net,
				"amount": net,
				"net_rate": net,
				"net_amount": net,
				"base_rate": net,
				"base_amount": net,
				"base_net_rate": net,
				"base_net_amount": net,
				"income_account": income_account,
				"cost_center": b.get("cost_center"),
				"project": b.get("project"),
				"item_tax_template": b.get("item_tax_template"),
			}
		)
		item.parent, item.parenttype, item.parentfield = name, "Down Payment Invoice", "items"
		item.idx, item.docstatus = idx, docstatus
		item.set_new_name()
		item_docs.append(item)
		item_name_by_bucket[key] = item.name

	# 5b. Sales Taxes and Charges rows -- one per distinct rate with non-zero tax.
	tax_docs = []
	tax_row_name_by_rate = {}
	running_total = 0.0
	rate_order = sorted(tax_by_rate.keys(), key=lambda r: (-flt(r), flt(r)))
	idx = 0
	for rate in rate_order:
		amt = tax_by_rate[rate]
		if not amt:
			continue
		idx += 1
		cfg = next((c for c in dp_map.values() if abs(flt(c.get("tax_rate")) - rate) <= 0.011), None)
		account_head = cfg.get("tax_account") if cfg else None
		if not account_head:
			raise ValueError(f"{name}: unresolvable tax account for rate {rate}%")
		net_for_rate = flt(next((r["net_amount"] for r in rate_rows if flt(r["rate"]) == rate), 0))
		running_total = flt(running_total + net_for_rate + amt, item_precision)
		tax = frappe.new_doc("Sales Taxes and Charges")
		tax.update(
			{
				# "On Net Total", not "Actual": erpnext.controllers.taxes_and_totals
				# distributes an "Actual" row's tax_amount proportionally across *every*
				# item by net share (item.net_amount / doc.net_total), regardless of that
				# item's own rate for this account, and then only counts items whose own
				# rate for this account is non-zero when reconciling the total (see
				# adjust_rounding_in_item_wise_tax_details). For any document with more
				# than one (income_account, rate) bucket that structurally never
				# reconciles, so a later re-validate (edit-and-save a draft, or amend)
				# throws "Item Wise Tax Details do not match with Taxes and Charges".
				# "On Net Total" computes each item's share from *its own* rate (via
				# item_tax_template -> item_tax_rate), so a re-validate always reconciles
				# with itself, at the cost of possibly recomputing this row's tax_amount by
				# a rounding cent if the document is ever actually re-saved -- acceptable;
				# only the untouched, never-resaved values need to be bit-identical.
				"charge_type": "On Net Total",
				"account_head": account_head,
				"description": desc_by_rate.get(rate) or f"Tax {_format_rate_label(rate)}",
				"rate": rate,
				"tax_amount": amt,
				"tax_amount_after_discount_amount": amt,
				"base_tax_amount": amt,
				"base_tax_amount_after_discount_amount": amt,
				"net_amount": net_for_rate,
				"base_net_amount": net_for_rate,
				"total": running_total,
				"base_total": running_total,
				"cost_center": _tcc.get(account_head) or _default_cost_center(company),
				"included_in_print_rate": 0,
			}
		)
		tax.parent, tax.parenttype, tax.parentfield = name, "Down Payment Invoice", "taxes"
		tax.idx, tax.docstatus = idx, docstatus
		tax.set_new_name()
		tax_docs.append(tax)
		tax_row_name_by_rate[rate] = tax.name

	# 5c. Item Wise Tax Detail rows -- one per (item row, tax row) pair, splitting each
	# rate's tax proportionally across the income-account buckets that share it.
	#
	# A rate with zero total tax has no Sales Taxes and Charges row of its own to link to
	# (step 5b only creates rows for non-zero rates). The retargeted print helper (step 5,
	# _aggregate_item_wise_by_rate reused on the DPI itself) only recognises an item's own
	# rate/bucket via a *phantom* item_wise_tax_details row -- exactly like a live document,
	# where such an item still gets a zero-amount row against whichever real tax row exists.
	# Without at least one such row the item silently disappears from every rate bucket, so
	# we manufacture one phantom row (amount 0) against an arbitrary existing tax row for
	# every zero-tax bucket, as long as the document has at least one real tax row at all.
	any_tax_row_name = tax_docs[0].name if tax_docs else None
	iwtd_docs = []
	iwtd_idx = 0

	def _append_iwtd(item_row, tax_row, rate, amount, taxable_amount):
		nonlocal iwtd_idx
		iwtd_idx += 1
		d = frappe.new_doc("Item Wise Tax Detail")
		d.update(
			{
				"item_row": item_row,
				"tax_row": tax_row,
				"rate": rate,
				"amount": amount,
				"taxable_amount": taxable_amount,
			}
		)
		d.parent, d.parenttype, d.parentfield = name, "Down Payment Invoice", "item_wise_tax_details"
		d.idx, d.docstatus = iwtd_idx, docstatus
		d.set_new_name()
		iwtd_docs.append(d)

	for rate in rate_order:
		tax_row_name = tax_row_name_by_rate.get(rate)
		keys_for_rate = [k for k in bucket_keys if flt(k[1]) == rate]

		if not tax_row_name:
			if not any_tax_row_name:
				continue  # whole document is zero-tax; nothing to phantom-link against.
			for k in keys_for_rate:
				_append_iwtd(item_name_by_bucket[k], any_tax_row_name, 0, 0, bucket_net[k])
			continue

		rate_total_net = flt(sum(bucket_net[k] for k in keys_for_rate), item_precision)
		rate_total_tax = tax_by_rate[rate]
		splits = {}
		for k in keys_for_rate:
			share = (
				flt(bucket_net[k] * rate_total_tax / rate_total_net, item_precision) if rate_total_net else 0
			)
			splits[k] = share
		diff = flt(rate_total_tax - sum(splits.values()), item_precision)
		if diff and keys_for_rate:
			biggest = max(keys_for_rate, key=lambda k: bucket_net[k])
			splits[biggest] = flt(splits[biggest] + diff, item_precision)

		for k in keys_for_rate:
			_append_iwtd(item_name_by_bucket[k], tax_row_name, rate, splits[k], bucket_net[k])

	# 6. Guard: totals must reconcile with the frozen legacy grand total.
	net_total = flt(sum(bucket_net.values()), item_precision)
	total_taxes = flt(sum(tax_by_rate.values()), item_precision)
	if abs(net_total + total_taxes - down_payment_amount) > 0.005:
		raise ValueError(
			f"{name}: net_total {net_total} + taxes {total_taxes} != down_payment_amount {down_payment_amount}"
		)

	# 7. Insert child rows in dependency order (items, then taxes, then item_wise_tax_details).
	if item_docs:
		bulk_insert("Down Payment Invoice Item", item_docs)
	if tax_docs:
		bulk_insert("Sales Taxes and Charges", tax_docs)
	if iwtd_docs:
		bulk_insert("Item Wise Tax Detail", iwtd_docs)

	# 8. Parent scalars.
	currency = frappe.db.get_value("Company", company, "default_currency")
	frappe.db.set_value(
		"Down Payment Invoice",
		name,
		{
			"net_total": net_total,
			"base_net_total": net_total,
			"total": net_total,
			"base_total": net_total,
			"total_taxes_and_charges": total_taxes,
			"base_total_taxes_and_charges": total_taxes,
			"grand_total": down_payment_amount,
			"base_grand_total": down_payment_amount,
			"currency": currency,
			"conversion_rate": 1,
			# "Net Total", not "Grand Total" (the DocType default written by the parent
			# controller): taxes_and_totals.calculate_taxes() reads doc.discount_amount /
			# doc.additional_discount_percentage as bare attributes -- which don't exist on
			# this doctype -- whenever apply_discount_on == "Grand Total" and doc.taxes is
			# non-empty. "Net Total" short-circuits that branch. See down_payment_invoice.py.
			"apply_discount_on": "Net Total",
		},
		update_modified=False,
	)
	return True
