# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

"""Journal entries and income/tax splits for **Down Payment Invoice** (no **Sales Invoice**)."""

from collections import defaultdict

import frappe
from erpnext.accounts.party import get_party_account
from frappe import _
from frappe.query_builder import DocType
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
	require_down_payment_accounts_for_income,
	require_requested_payments_account,
)


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


def post_down_payment_invoice_submission_journals(dpi) -> tuple[str, str]:
	"""Receivable + revenue/tax mirror, then neutralization to Requested Payments. Returns (initial_je, neutralization_je)."""
	income_totals, tax_totals, income_cc, income_proj, tax_cc = (
		get_income_tax_totals_for_down_payment_invoice(dpi)
	)
	if income_totals:
		require_down_payment_accounts_for_income(dpi.company, income_totals.keys())

	grand = flt(dpi.down_payment_amount)
	sum_inc = sum(income_totals.values())
	sum_tax = sum(tax_totals.values())
	adj = flt(grand - sum_inc - sum_tax)
	if abs(adj) > 0.0001 and income_totals:
		big = max(income_totals.keys(), key=lambda k: income_totals[k])
		income_totals[big] += adj
		sum_inc = sum(income_totals.values())
	if abs(sum_inc + sum_tax - grand) > 0.06:
		frappe.throw(
			_("Down payment split ({0} + {1}) does not match gross amount ({2}).").format(
				sum_inc, sum_tax, grand
			)
		)

	receivable = get_party_account("Customer", dpi.customer, dpi.company, include_advance=False)
	rows = []
	credit_total = 0.0
	for acc, amt in sorted(income_totals.items()):
		if not amt:
			continue
		append_je_row(
			rows,
			acc,
			0,
			amt,
			income_cc.get(acc) or default_cost_center(dpi.company),
			income_proj.get(acc),
		)
		credit_total += amt
	for acc, amt in sorted(tax_totals.items()):
		if not amt:
			continue
		append_je_row(rows, acc, 0, amt, tax_cc.get(acc) or default_cost_center(dpi.company), None)
		credit_total += amt

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

	je1 = insert_and_submit_je(
		dpi.company,
		dpi.posting_date,
		rows,
		_("Down payment invoice booking for {0}").format(dpi.name),
		_("Down Payment Invoice"),
		sales_invoice=None,
		payment_entry=None,
		down_payment_invoice=dpi.name,
	)

	je2_name = _post_neutralization_journal(dpi, income_totals, tax_totals, income_cc, income_proj, tax_cc)
	return je1.name, je2_name


def _post_neutralization_journal(dpi, income_totals, tax_totals, income_cc, income_proj, tax_cc) -> str:
	require_requested_payments_account(dpi.company)
	requested_acc = get_requested_payments_account(dpi.company)

	rows = []
	for acc, amt in sorted(income_totals.items()):
		append_je_row(
			rows,
			acc,
			amt,
			0,
			income_cc.get(acc) or default_cost_center(dpi.company),
			income_proj.get(acc),
		)
	for acc, amt in sorted(tax_totals.items()):
		append_je_row(rows, acc, amt, 0, tax_cc.get(acc) or default_cost_center(dpi.company), None)

	total_debit = sum(flt(r.get("debit_in_account_currency") or 0) for r in rows)
	if not total_debit:
		frappe.throw(_("No income or tax lines to neutralize for {0}.").format(dpi.name))

	append_je_row(rows, requested_acc, 0, total_debit, None, None)

	je = insert_and_submit_je(
		dpi.company,
		dpi.posting_date,
		rows,
		_("Down payment invoice neutralization for {0}").format(dpi.name),
		_("Down Payment Neutralization"),
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


def get_latest_submitted_je_for_down_payment_invoice(dpi_name: str) -> str | None:
	"""Newest submitted automation **Journal Entry** linked to this **Down Payment Invoice**."""
	names = frappe.get_all(
		"Journal Entry",
		filters={"custom_dp_down_payment_invoice": dpi_name, "docstatus": 1},
		pluck="name",
		order_by="creation desc",
		limit_page_length=1,
	)
	return names[0] if names else None


def neutralization_journal_exists_for_down_payment_invoice(dpi_name: str) -> bool:
	"""Whether the neutralization **Journal Entry** for this DPI was submitted (not only the receivable booking)."""
	neutral_title = _("Down Payment Neutralization")
	return bool(
		frappe.get_all(
			"Journal Entry",
			filters={
				"custom_dp_down_payment_invoice": dpi_name,
				"docstatus": 1,
				"title": neutral_title,
			},
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
