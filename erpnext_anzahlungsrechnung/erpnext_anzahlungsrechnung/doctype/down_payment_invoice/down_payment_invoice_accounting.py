# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

"""Journal entries and income/tax splits for **Down Payment Invoice** (no **Sales Invoice**)."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Coalesce, Sum
from frappe.utils import flt

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_tax_allocation import (
	build_tax_rows_for_down_payment,
)
from erpnext_anzahlungsrechnung.scripts.utils import (
	append_je_row,
	default_cost_center,
	get_company_down_payment_map,
	get_requested_payments_account,
	insert_and_submit_je,
	merge_je_account_rows,
	require_down_payment_accounts_for_income,
	require_requested_payments_account,
)

# Passed to new **Journal Entry** documents. ERPNext ``validate`` overwrites ``title`` with
# ``get_title()`` (first account, etc.), so do **not** look up automation JEs by ``title`` —
# use ``custom_dp_*`` links instead.
JE_TITLE_DOWN_PAYMENT_OPENING = "Down Payment Opening"
JE_TITLE_DOWN_PAYMENT_RECEIPT_CLEARING = "Down Payment Receipt Clearing"
JE_TITLE_FINAL_INVOICE_DPI_NEUTRALIZATION = "Final Invoice Down Payment Neutralization"


def get_receivable_account_for_down_payment_invoice(dpi) -> str:
	"""Receivable account for Step 1 **Journal Entry** from **Sales Order** ``debit_to``."""
	return frappe.db.get_value("Sales Order", dpi.sales_order, "debit_to")


def get_income_tax_totals_for_down_payment_invoice(dpi):
	"""Net per income account and tax per tax account for this down payment (aligned with print tax rows)."""
	so = frappe.get_cached_doc("Sales Order", dpi.sales_order)
	rows = build_tax_rows_for_down_payment(so, flt(dpi.down_payment_amount))
	G = flt(so.grand_total)
	D = flt(dpi.down_payment_amount)
	income_totals = defaultdict(float)
	income_cc = {}
	income_proj = {}
	if not D or not G:
		return income_totals, defaultdict(float), income_cc, income_proj, {}

	factor = D / G
	for item in so.get("items") or []:
		acc = getattr(item, "income_account", None)
		if not acc:
			continue
		amt = flt(flt(item.net_amount) * factor, item.precision("net_amount"))
		if amt:
			income_totals[acc] += amt
			income_cc.setdefault(acc, item.cost_center)
			income_proj.setdefault(acc, item.project or so.project)

	target_net = sum(flt(r["net_amount"]) for r in rows)
	cur_net = sum(income_totals.values())
	diff = flt(target_net - cur_net, frappe.get_precision("Sales Order", "net_total") or 2)
	if abs(diff) > 0.0001 and income_totals:
		big = max(income_totals.keys(), key=lambda k: income_totals[k])
		income_totals[big] += diff

	dp_map = get_company_down_payment_map(dpi.company)
	tax_totals = defaultdict(float)
	tax_cc = {}
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
			frappe.throw(_("Set Tax Account on Company Down Payment Account for tax rate {0}%.").format(rate))
		tax_totals[tax_acc] += tax_amt
		tax_cc.setdefault(tax_acc, default_cost_center(dpi.company))

	return income_totals, tax_totals, income_cc, income_proj, tax_cc


def get_down_payment_net_total(dpi):
	income_totals, *_ = get_income_tax_totals_for_down_payment_invoice(dpi)
	return flt(sum(income_totals.values()))


def get_down_payment_tax_total(dpi):
	_, tax_totals, *_ = get_income_tax_totals_for_down_payment_invoice(dpi)
	return flt(sum(tax_totals.values()))


def post_down_payment_invoice_submission_journals(dpi) -> str:
	"""Step 1: Dr receivable, Cr *Requested Payments Account* (single **Journal Entry**)."""
	require_requested_payments_account(dpi.company)
	requested_acc = get_requested_payments_account(dpi.company)
	receivable = get_receivable_account_for_down_payment_invoice(dpi)
	grand = flt(dpi.down_payment_amount)

	rows = []
	append_je_row(
		rows,
		receivable,
		grand,
		0,
		None,
		None,
		party_type="Customer",
		party=dpi.customer,
	)
	append_je_row(rows, requested_acc, 0, grand, None, None)

	je = insert_and_submit_je(
		dpi.company,
		dpi.posting_date,
		rows,
		_("Down payment opening for {0}").format(dpi.name),
		JE_TITLE_DOWN_PAYMENT_OPENING,
		sales_invoice=None,
		payment_entry=None,
		down_payment_invoice=dpi.name,
	)
	return je.name


def cancel_down_payment_invoice_journals(dpi):
	"""Cancel automation **Journal Entry** records linked to this down payment invoice (newest first)."""
	names = frappe.get_all(
		"Journal Entry",
		filters={"custom_dp_down_payment_invoice": dpi.name, "docstatus": 1},
		pluck="name",
		order_by="creation desc",
	)
	for name in names:
		frappe.get_doc("Journal Entry", name).cancel()


def get_submitted_payment_entries_against_down_payment_invoice(dpi_name: str) -> list[str]:
	pe_ref = DocType("Payment Entry Reference")
	pe = DocType("Payment Entry")
	return (
		frappe.qb.from_(pe_ref)
		.inner_join(pe)
		.on(pe_ref.parent == pe.name)
		.select(pe.name)
		.distinct()
		.where(
			(pe_ref.reference_doctype == "Down Payment Invoice")
			& (pe_ref.reference_name == dpi_name)
			& (pe.docstatus == 1)
		)
	).run(pluck=True)


def get_submitted_payment_entries_linked_via_clearing_journal(dpi_name: str) -> list[str]:
	"""**Payment Entry** names that have a submitted receipt-clearing **Journal Entry** for this DPI."""
	names = frappe.get_all(
		"Journal Entry",
		filters=[
			["custom_dp_down_payment_invoice", "=", dpi_name],
			["docstatus", "=", 1],
			["custom_dp_sales_invoice", "is", "not set"],
			["custom_dp_payment_entry", "is", "set"],
		],
		pluck="custom_dp_payment_entry",
	)
	return list(dict.fromkeys(n for n in names if n))


def opening_journal_exists_for_down_payment_invoice(dpi_name: str) -> bool:
	return bool(
		frappe.get_all(
			"Journal Entry",
			filters=[
				["custom_dp_down_payment_invoice", "=", dpi_name],
				["docstatus", "=", 1],
				["custom_dp_sales_invoice", "is", "not set"],
				["custom_dp_payment_entry", "is", "not set"],
			],
			limit=1,
		)
	)


def aggregate_tax_amounts_for_down_payment_invoice(dpi):
	"""``(tax_account, base_amount, cost_center)`` list for splitting payment tax pool (like **Sales Invoice** ``get_tax_amounts``)."""
	_, tax_totals, _, _, tax_cc = get_income_tax_totals_for_down_payment_invoice(dpi)
	out = []
	for acc, base_amt in tax_totals.items():
		if base_amt:
			out.append((acc, flt(base_amt), tax_cc.get(acc)))
	return out


def get_total_receipt_clearing_debit_on_requested_for_dpi(dpi_name: str, company: str) -> float:
	"""Sum Dr on *Requested Payments Account* from submitted receipt-clearing **Journal Entry** records for this DPI."""
	requested = get_requested_payments_account(company)
	if not requested:
		return 0.0
	je = DocType("Journal Entry")
	jea = DocType("Journal Entry Account")
	q = (
		frappe.qb.from_(jea)
		.inner_join(je)
		.on(jea.parent == je.name)
		.select(Sum(jea.debit_in_account_currency))
		.where(
			(je.custom_dp_down_payment_invoice == dpi_name)
			& (je.docstatus == 1)
			& (Coalesce(je.custom_dp_sales_invoice, "") == "")
			& (Coalesce(je.custom_dp_payment_entry, "") != "")
			& (jea.account == requested)
		)
	)
	row = q.run()
	return flt(row[0][0]) if row and row[0] else 0.0


def fifo_split_sales_order_payment_to_dpis(
	sales_order: str, alloc_base: float, company: str
) -> list[tuple[str, float]]:
	"""
	Split a **Payment Entry** allocation against **Sales Order** across submitted **Down Payment Invoice**
	documents FIFO by ``posting_date``, ``creation``. Each slice is capped by remaining opening capacity on that DPI.
	"""
	alloc_base = flt(alloc_base)
	if not alloc_base:
		return []

	dpis = frappe.get_all(
		"Down Payment Invoice",
		filters={"sales_order": sales_order, "docstatus": 1},
		fields=["name", "down_payment_amount"],
		order_by="posting_date asc, creation asc",
	)
	remaining = alloc_base
	out = []
	for row in dpis:
		if remaining <= 0:
			break
		dpi_name = row.name
		gross = flt(row.down_payment_amount)
		cleared = get_total_receipt_clearing_debit_on_requested_for_dpi(dpi_name, company)
		capacity = flt(gross - cleared)
		if capacity <= 0:
			continue
		take = min(remaining, capacity)
		if take > 0:
			out.append((dpi_name, take))
			remaining -= take
	if remaining > 0.02:
		frappe.throw(
			_("Payment against Sales Order {0} exceeds remaining down payment clearing capacity.").format(
				frappe.bold(sales_order)
			)
		)
	return out


def build_down_payment_receipt_clearing_journal_accounts(
	pe,
	dpi,
	alloc_base: float,
	ref_type: str,
	ref_name: str,
) -> list[dict]:
	"""Account rows for one receipt-clearing **Journal Entry** (Step 2) for ``alloc_base`` in company currency."""
	income_totals, _tax_totals, _icc, _ipr, _tcc = get_income_tax_totals_for_down_payment_invoice(dpi)
	require_down_payment_accounts_for_income(pe.company, income_totals.keys())
	dp_map = get_company_down_payment_map(pe.company)

	require_requested_payments_account(pe.company)
	requested_acc = get_requested_payments_account(pe.company)

	alloc_base = flt(alloc_base)
	if not alloc_base:
		return []

	base_net = flt(sum(income_totals.values()))
	base_grand = flt(dpi.down_payment_amount)
	if not base_grand:
		return []

	net_portion = flt(alloc_base * base_net / base_grand, 2)
	tax_pool = flt(alloc_base - net_portion, 2)

	tax_amounts = aggregate_tax_amounts_for_down_payment_invoice(dpi)
	tax_splits = defaultdict(float)
	total_tax_base = sum(flt(a[1]) for a in tax_amounts)
	if total_tax_base > 0 and tax_pool:
		for acc, base_amt, cc in tax_amounts:
			part = flt(tax_pool * flt(base_amt) / total_tax_base, 2)
			tax_splits[(acc, cc or "")] += part
		sum_tax = sum(tax_splits.values())
		if tax_splits and abs(sum_tax - tax_pool) > 0.01:
			first_key = next(iter(tax_splits))
			tax_splits[first_key] += flt(tax_pool - sum_tax)

	rows = []
	append_je_row(rows, requested_acc, alloc_base, 0, None, None, ref_type, ref_name)

	for (acc, cc_key), amt in tax_splits.items():
		if amt:
			cc = cc_key or None
			append_je_row(
				rows,
				acc,
				0,
				amt,
				cc or default_cost_center(pe.company),
				None,
				ref_type,
				ref_name,
			)

	if net_portion and base_net:
		recv_lines = []
		for acc, amt in income_totals.items():
			if not amt:
				continue
			part = flt(net_portion * flt(amt) / base_net, 2)
			if part:
				received_acc = dp_map[acc]["received_down_payment_account"]
				recv_lines.append((received_acc, part))
		sum_recv = sum(x[1] for x in recv_lines)
		if recv_lines and abs(sum_recv - net_portion) > 0.01:
			first_acc, first_amt = recv_lines[0]
			recv_lines[0] = (first_acc, flt(first_amt + (net_portion - sum_recv)))
		for received_acc, part in recv_lines:
			append_je_row(
				rows,
				received_acc,
				0,
				part,
				None,
				None,
				ref_type,
				ref_name,
				party_type=pe.party_type,
				party=pe.party,
			)

	return rows


def get_interim_journal_entry_names_for_down_payment_invoice(dpi_name: str) -> list[str]:
	"""Submitted Step 1 and Step 2 automation **Journal Entry** names for this DPI (chronological)."""
	return frappe.get_all(
		"Journal Entry",
		filters=[
			["custom_dp_down_payment_invoice", "=", dpi_name],
			["docstatus", "=", 1],
			["custom_dp_sales_invoice", "is", "not set"],
		],
		pluck="name",
		order_by="creation asc",
	)


def build_reversed_journal_rows_from_entries(je_names: list[str]) -> list[dict]:
	"""Invert all account lines from the given **Journal Entry** documents into one merged row list."""
	rows = []
	for name in je_names:
		je = frappe.get_doc("Journal Entry", name)
		for line in je.accounts:
			debit = flt(line.debit_in_account_currency)
			credit = flt(line.credit_in_account_currency)
			if debit:
				append_je_row(
					rows,
					line.account,
					0,
					debit,
					line.cost_center,
					line.project,
					line.reference_type or None,
					line.reference_name or None,
					party_type=line.party_type or None,
					party=line.party or None,
				)
			if credit:
				append_je_row(
					rows,
					line.account,
					credit,
					0,
					line.cost_center,
					line.project,
					line.reference_type or None,
					line.reference_name or None,
					party_type=line.party_type or None,
					party=line.party or None,
				)
	return merge_je_account_rows(rows)


def post_final_invoice_down_payment_neutralization_journals(si) -> str | None:
	"""Step 3-4: For each linked DPI, post one **Journal Entry** that reverses opening + receipt-clearing interim bookings."""
	last_je = None
	for dp in si.get("custom_down_payments") or []:
		dpsi = frappe.get_doc("Down Payment Invoice", dp.invoice_no)
		if dpsi.docstatus != 1:
			frappe.throw(_("Down payment invoice {0} must be submitted.").format(dp.invoice_no))
		if not opening_journal_exists_for_down_payment_invoice(dp.invoice_no):
			frappe.throw(
				_("Down payment invoice {0} has no opening journal entry yet.").format(dp.invoice_no)
			)

		je_names = get_interim_journal_entry_names_for_down_payment_invoice(dp.invoice_no)
		if not je_names:
			continue
		rows = build_reversed_journal_rows_from_entries(je_names)
		if not rows:
			continue
		rows = merge_je_account_rows(rows)
		total_debit = sum(flt(r.get("debit_in_account_currency") or 0) for r in rows)
		total_credit = sum(flt(r.get("credit_in_account_currency") or 0) for r in rows)
		if abs(total_debit - total_credit) > 0.02:
			frappe.throw(
				_("Final invoice down payment neutralization does not balance for {0}.").format(dp.invoice_no)
			)

		je = insert_and_submit_je(
			si.company,
			si.posting_date,
			rows,
			_("Final invoice neutralization for down payment {0} ({1})").format(dp.invoice_no, si.name),
			JE_TITLE_FINAL_INVOICE_DPI_NEUTRALIZATION,
			sales_invoice=si.name,
			payment_entry=None,
			down_payment_invoice=dp.invoice_no,
		)
		last_je = je.name
	return last_je
