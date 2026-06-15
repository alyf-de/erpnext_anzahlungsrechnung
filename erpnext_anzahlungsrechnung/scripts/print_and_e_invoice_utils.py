import frappe
from frappe import _
from frappe.utils import flt, getdate


def before_print(doc, method, print_settings):
	prepare_invoice_data_according_to_invoice_type(doc)


def prepare_invoice_data_according_to_invoice_type(doc):
	_add_tax_rates_to_items(doc)
	if doc.custom_invoice_type == "Final Invoice":
		if doc.get("custom_down_payments"):
			doc.prior_down_payment_print_rows = build_prior_down_payment_print_rows(doc)
		else:
			doc.prior_down_payment_print_rows = []


def _add_tax_rates_to_items(doc):
	by_item = {}
	for row in doc.get("item_wise_tax_details") or []:
		if flt(row.amount) == 0 or flt(row.taxable_amount) == 0:
			continue
		by_item.setdefault(row.item_row, []).append(flt(row.rate))

	for key in by_item:
		by_item[key] = sorted(set(by_item[key]))

	for item in doc.items:
		rates = list(by_item.get(item.name) or [])
		if not rates:
			rates = [0.0]
		item.tax_rate = rates


def build_prior_down_payment_print_rows(doc):
	"""
	Build a dictionary in following format:
	down_payment_invoices = [
		{
			"invoice_no": "",
			"posting_date": "",
			"down_payment_amount": "",
			"payment_entries": [
				{
					"amount": "",
					"taxes": [
						{
							"description": "",
							"amount": "",
						}
					]
				}
			],
		},
		{
			...
		},
	]
	"""
	down_payment_invoices = []
	for dpi in doc.custom_down_payments:
		down_payment_invoices.append(
			{
				"invoice_no": dpi.invoice_no,
				"posting_date": dpi.date,
				"down_payment_amount": dpi.grand_total,
				"payment_entries": [],
			}
		)

	down_payment_invoices = add_payment_entries_to_down_payment_invoices(down_payment_invoices, doc)

	return down_payment_invoices


def add_payment_entries_to_down_payment_invoices(down_payment_invoices, doc):
	for pe in doc.advances:
		if pe.reference_type != "Payment Entry" or not pe.reference_name:
			continue

		pe_doc = frappe.get_doc("Payment Entry", pe.reference_name)
		reference_date = pe_doc.reference_date or pe_doc.posting_date
		journal_entry = frappe.db.get_all(
			"Journal Entry",
			filters={"custom_dp_payment_entry": pe.reference_name, "docstatus": 1},
			pluck="name",
		)
		if journal_entry:
			journal_entry_doc = frappe.get_doc("Journal Entry", journal_entry[0])
			tax_rows = []
			for je_row in journal_entry_doc.accounts:
				if je_row.account_type == "Tax":
					tax_rows.append(
						{
							"description": je_row.account,
							"amount": je_row.debit_in_account_currency or je_row.credit_in_account_currency,
						}
					)

			down_payment_invoice = next(
				(
					dpi
					for dpi in down_payment_invoices
					if dpi["invoice_no"] == journal_entry_doc.custom_dp_down_payment_invoice
				),
				None,
			)
			if down_payment_invoice:
				down_payment_invoice["payment_entries"].append(
					{
						"amount": pe_doc.paid_amount,
						"paid_on": reference_date,
						"taxes": tax_rows,
					}
				)
