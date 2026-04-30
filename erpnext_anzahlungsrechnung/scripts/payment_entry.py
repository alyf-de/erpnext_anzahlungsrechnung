from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	JE_TITLE_DOWN_PAYMENT_RECEIPT_CLEARING,
	build_down_payment_receipt_clearing_journal_accounts,
	fifo_split_sales_order_payment_to_dpis,
)
from erpnext_anzahlungsrechnung.scripts.utils import insert_and_submit_je, merge_je_account_rows


def on_submit(doc, event):
	if doc.payment_type != "Receive" or doc.party_type != "Customer":
		return
	post_receipt_clearing_journal_for_down_payment_invoices(doc)


def post_receipt_clearing_journal_for_down_payment_invoices(pe) -> list[str]:
	"""
	Step 2: For each **Down Payment Invoice** slice from **Payment Entry** references (DPI and/or **Sales Order**),
	submit one balanced receipt-clearing **Journal Entry** per DPI.
	"""
	ref_type, ref_name = "Payment Entry", pe.name
	by_dpi: dict[str, float] = defaultdict(float)

	for d in pe.get("references") or []:
		if not d.reference_doctype or not d.reference_name or not flt(d.allocated_amount):
			continue

		if d.reference_doctype == "Down Payment Invoice":
			alloc_base = flt(pe.calculate_base_allocated_amount_for_reference(d))
			if alloc_base:
				by_dpi[d.reference_name] += alloc_base
			continue

		if d.reference_doctype == "Sales Order":
			so_type = frappe.db.get_value("Sales Order", d.reference_name, "custom_invoice_type")
			if so_type != "Down Payment Invoice":
				continue
			alloc_base = flt(pe.calculate_base_allocated_amount_for_reference(d))
			if not alloc_base:
				continue
			for dpi_name, slice_amt in fifo_split_sales_order_payment_to_dpis(
				d.reference_name, alloc_base, pe.company
			):
				if slice_amt:
					by_dpi[dpi_name] += slice_amt

	if not by_dpi:
		return []

	created = []
	for dpi_name, total_alloc in sorted(by_dpi.items()):
		if not total_alloc:
			continue
		dpi = frappe.get_doc("Down Payment Invoice", dpi_name)
		rows = build_down_payment_receipt_clearing_journal_accounts(pe, dpi, total_alloc, ref_type, ref_name)
		if not rows:
			continue
		rows = merge_je_account_rows(rows)
		total_debit = sum(flt(r.get("debit_in_account_currency") or 0) for r in rows)
		total_credit = sum(flt(r.get("credit_in_account_currency") or 0) for r in rows)
		if abs(total_debit - total_credit) > 0.02:
			frappe.throw(
				_("Down payment receipt clearing journal does not balance (debit {0}, credit {1}).").format(
					total_debit, total_credit
				)
			)

		je = insert_and_submit_je(
			pe.company,
			pe.posting_date,
			rows,
			_("Down payment receipt clearing for {0} ({1})").format(pe.name, dpi_name),
			JE_TITLE_DOWN_PAYMENT_RECEIPT_CLEARING,
			sales_invoice=None,
			payment_entry=pe.name,
			down_payment_invoice=dpi_name,
		)
		created.append(je.name)
	return created
