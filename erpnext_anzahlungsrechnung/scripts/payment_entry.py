from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext_anzahlungsrechnung.scripts.utils import (
	append_je_row,
	default_cost_center,
	get_company_liability_accounts,
	insert_and_submit_je,
	merge_je_account_rows,
	require_company_liability_accounts,
)


def on_submit(doc, event):
	if doc.payment_type != "Receive" or doc.party_type != "Customer":
		return
	post_receipt_clearing_journal_for_down_payment_invoices(doc)


def post_receipt_clearing_journal_for_down_payment_invoices(pe) -> str | None:
	"""For each allocated Down Payment Sales Invoice: debit Requested Payments, credit tax accounts and Received Prepayments (net/tax split)."""
	ref_rows = []
	for d in pe.get("references") or []:
		if d.reference_doctype != "Sales Invoice" or not d.reference_name or not flt(d.allocated_amount):
			continue
		inv_type = frappe.db.get_value("Sales Invoice", d.reference_name, "custom_invoice_type")
		if inv_type != "Down Payment Invoice":
			continue
		ref_rows.append(d)

	if not ref_rows:
		return None

	require_company_liability_accounts(pe.company, need_received=True)
	requested_acc, received_acc = get_company_liability_accounts(pe.company)

	rows = []
	ref_type, ref_name = "Payment Entry", pe.name

	for d in ref_rows:
		si = frappe.get_doc("Sales Invoice", d.reference_name)
		alloc_base = flt(pe.calculate_base_allocated_amount_for_reference(d))
		if not alloc_base:
			continue

		base_net = flt(si.base_net_total)
		base_grand = flt(si.base_grand_total)
		if not base_grand:
			continue

		net_portion = flt(alloc_base * base_net / base_grand, 2)
		tax_pool = flt(alloc_base - net_portion, 2)

		enable_discount_accounting = cint(
			frappe.get_single_value("Selling Settings", "enable_discount_accounting")
		)
		tax_amounts = []
		total_tax_base = 0.0
		for tax in si.get("taxes") or []:
			if not flt(tax.base_tax_amount_after_discount_amount):
				continue
			_tax_amt, base_amt = si.get_tax_amounts(tax, enable_discount_accounting)
			tax_amounts.append((tax.account_head, flt(base_amt), tax.cost_center))
			total_tax_base += flt(base_amt)

		tax_splits = defaultdict(float)
		if total_tax_base > 0 and tax_pool:
			for acc, base_amt, cc in tax_amounts:
				part = flt(tax_pool * base_amt / total_tax_base, 2)
				tax_splits[(acc, cc or "")] += part
		sum_tax = sum(tax_splits.values())
		if tax_splits and abs(sum_tax - tax_pool) > 0.01:
			first_key = next(iter(tax_splits))
			tax_splits[first_key] += flt(tax_pool - sum_tax)

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

		if net_portion:
			append_je_row(
				rows,
				received_acc,
				0,
				net_portion,
				None,
				None,
				ref_type,
				ref_name,
				party_type=pe.party_type,
				party=pe.party,
			)

	rows = merge_je_account_rows(rows)

	total_debit = sum(flt(r.get("debit_in_account_currency") or 0) for r in rows)
	total_credit = sum(flt(r.get("credit_in_account_currency") or 0) for r in rows)
	if abs(total_debit - total_credit) > 0.02:
		frappe.throw(
			_("Down payment receipt clearing journal does not balance (debit {0}, credit {1}).").format(
				total_debit, total_credit
			)
		)

	first_si = ref_rows[0].reference_name
	je = insert_and_submit_je(
		pe.company,
		pe.posting_date,
		rows,
		_("Down payment receipt clearing for {0}").format(pe.name),
		_("Down Payment Receipt Clearing"),
		sales_invoice=first_si,
		payment_entry=pe.name,
	)
	return je.name
