from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	aggregate_tax_amounts_for_down_payment_invoice,
	get_income_tax_totals_for_down_payment_invoice,
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


def on_submit(doc, event):
	if doc.payment_type != "Receive" or doc.party_type != "Customer":
		return
	post_receipt_clearing_journal_for_down_payment_invoices(doc)


def post_receipt_clearing_journal_for_down_payment_invoices(pe) -> str | None:
	"""For each allocated **Down Payment Invoice**: debit Requested Payments, credit tax and Received Down Payment accounts."""
	ref_rows = []
	for d in pe.get("references") or []:
		if (
			d.reference_doctype != "Down Payment Invoice"
			or not d.reference_name
			or not flt(d.allocated_amount)
		):
			continue
		ref_rows.append(d)

	if not ref_rows:
		return None

	require_requested_payments_account(pe.company)
	requested_acc = get_requested_payments_account(pe.company)

	rows = []
	ref_type, ref_name = "Payment Entry", pe.name

	for d in ref_rows:
		dpi = frappe.get_doc("Down Payment Invoice", d.reference_name)
		income_totals, _tax_totals, _icc, _ipr, _tcc = get_income_tax_totals_for_down_payment_invoice(dpi)
		require_down_payment_accounts_for_income(pe.company, income_totals.keys())
		dp_map = get_company_down_payment_map(pe.company)

		alloc_base = flt(pe.calculate_base_allocated_amount_for_reference(d))
		if not alloc_base:
			continue

		base_net = flt(sum(income_totals.values()))
		base_grand = flt(dpi.down_payment_amount)
		if not base_grand:
			continue

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

	rows = merge_je_account_rows(rows)

	total_debit = sum(flt(r.get("debit_in_account_currency") or 0) for r in rows)
	total_credit = sum(flt(r.get("credit_in_account_currency") or 0) for r in rows)
	if abs(total_debit - total_credit) > 0.02:
		frappe.throw(
			_("Down payment receipt clearing journal does not balance (debit {0}, credit {1}).").format(
				total_debit, total_credit
			)
		)

	first_dpi = ref_rows[0].reference_name
	je = insert_and_submit_je(
		pe.company,
		pe.posting_date,
		rows,
		_("Down payment receipt clearing for {0}").format(pe.name),
		_("Down Payment Receipt Clearing"),
		sales_invoice=None,
		payment_entry=pe.name,
		down_payment_invoice=first_dpi,
	)
	return je.name
