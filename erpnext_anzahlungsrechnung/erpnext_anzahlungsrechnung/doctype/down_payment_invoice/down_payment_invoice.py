# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	cancel_down_payment_invoice_journals,
	get_income_tax_totals_for_down_payment_invoice,
	get_submitted_payment_entries_against_down_payment_invoice,
	post_down_payment_invoice_submission_journals,
)
from erpnext_anzahlungsrechnung.scripts.utils import require_down_payment_accounts_for_income


class DownPaymentInvoice(Document):
	def validate(self):
		_validate_sales_order_down_payment_flow(self)
		_validate_down_payment_amounts(self)
		_validate_company_down_payment_mapping(self)

	def before_cancel(self):
		pes = get_submitted_payment_entries_against_down_payment_invoice(self.name)
		if pes:
			frappe.throw(
				_("Cancel Payment Entries against this invoice before cancelling: {0}").format(", ".join(pes))
			)

	def on_cancel(self):
		cancel_down_payment_invoice_journals(self)

	def on_submit(self):
		post_down_payment_invoice_submission_journals(self)


def _validate_sales_order_down_payment_flow(doc):
	if not doc.sales_order:
		return
	so_type = frappe.db.get_value("Sales Order", doc.sales_order, "custom_invoice_type")
	if so_type != "Down Payment Invoice":
		frappe.throw(
			_(
				"The Invoice Type ({0}) of the Sales Order {1} does not match the down payment flow (expected Down Payment Invoice)."
			).format(_(so_type or ""), doc.sales_order)
		)


def _validate_down_payment_amounts(doc):
	if flt(doc.down_payment_amount) <= 0:
		frappe.throw(_("Down Payment Amount must be greater than zero."))

	total = flt(doc.total_sales_order_amount)
	if not total:
		return

	pct = flt(doc.down_payment_percentage)
	amt = flt(doc.down_payment_amount)
	p_amt = flt(pct * total / 100, doc.precision("down_payment_amount"))
	p_pct = flt(amt / total * 100, doc.precision("down_payment_percentage"))
	if abs(amt - p_amt) > 0.02 and abs(pct - p_pct) > 0.02:
		frappe.throw(_("Down Payment Amount and Down Payment Percentage do not match the Sales Order total."))

	if pct >= 100:
		frappe.throw(_("Down Payment Percentage must be less than 100."))


def _validate_company_down_payment_mapping(doc):
	if not doc.sales_order or not doc.company:
		return
	income_totals, _, _, _, _ = get_income_tax_totals_for_down_payment_invoice(doc)
	if income_totals:
		require_down_payment_accounts_for_income(doc.company, income_totals.keys())
