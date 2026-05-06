# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, fmt_money

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	cancel_down_payment_invoice_journals,
	get_income_tax_totals_for_down_payment_invoice,
	get_submitted_payment_entries_against_down_payment_invoice,
	get_submitted_payment_entries_linked_via_clearing_journal,
	post_down_payment_invoice_submission_journals,
)
from erpnext_anzahlungsrechnung.scripts.utils import require_down_payment_accounts_for_income


class DownPaymentInvoice(Document):
	def validate(self):
		_validate_sales_order_down_payment_flow(self)
		_validate_down_payment_amounts(self)
		_validate_company_down_payment_mapping(self)

	def before_cancel(self):
		pes = set(get_submitted_payment_entries_against_down_payment_invoice(self.name))
		pes.update(get_submitted_payment_entries_linked_via_clearing_journal(self.name))
		if pes:
			frappe.throw(
				_("Cancel Payment Entries linked to this down payment before cancelling: {0}").format(
					", ".join(sorted(pes))
				)
			)

	def on_cancel(self):
		cancel_down_payment_invoice_journals(self)

	def on_submit(self):
		post_down_payment_invoice_submission_journals(self)

	@frappe.whitelist()
	def refresh_totals_from_sales_order(self):
		"""Set total_sales_order_amount from the Sales Order grand total and recompute down_payment_amount from down_payment_percentage."""
		if self.docstatus != 0:
			frappe.throw(_("Only draft Down Payment Invoices can be refreshed."))
		frappe.has_permission("Down Payment Invoice", "write", doc=self, throw=True)

		self.total_sales_order_amount = frappe.db.get_value("Sales Order", self.sales_order, "grand_total")
		self.down_payment_amount = flt(
			flt(self.down_payment_percentage) * flt(self.total_sales_order_amount) / 100,
			self.precision("down_payment_amount"),
		)
		self.save()
		return {
			"total_sales_order_amount": self.total_sales_order_amount,
			"down_payment_amount": self.down_payment_amount,
		}


def _validate_sales_order_down_payment_flow(doc):
	"""
	Validate:
	- Sales Order's invoice type is "Down Payment Invoice".
	- Sales Order is submitted.
	- Sales Order is not billed.
	- Requested down payment does not exceed the Sales Order outstanding (grand total - advance paid).
	- "Total Sales Order Amount" is not outdated.
	"""
	so_doc = frappe.get_doc("Sales Order", doc.sales_order)
	# 1. Sales Order's invoice type is "Down Payment Invoice".
	if so_doc.custom_invoice_type != "Down Payment Invoice":
		frappe.throw(
			_(
				"The Invoice Type ({0}) of the Sales Order {1} does not match the down payment flow (expected Down Payment Invoice)."
			).format(_(so_doc.custom_invoice_type or ""), so_doc.name)
		)

	# 2. Sales Order is submitted.
	if so_doc.docstatus != 1:
		frappe.throw(_("The Sales Order {0} is not submitted.").format(so_doc.name))

	# 3. Sales Order is not billed.
	if so_doc.per_billed > 0:
		frappe.throw(_("The Sales Order {0} is billed.").format(so_doc.name))

	# 4. Outstanding amount is smaller than the requested down payment amount.
	if (so_doc.grand_total - so_doc.advance_paid) < doc.down_payment_amount:
		frappe.throw(
			_(
				"The requested down payment amount ({0}) is greater than the outstanding amount ({1}) of the Sales Order {2}."
			).format(
				fmt_money(doc.down_payment_amount, currency=so_doc.currency),
				fmt_money(so_doc.grand_total - so_doc.advance_paid, currency=so_doc.currency),
				so_doc.name,
			)
		)

	# 5. "Total Sales Order Amount" is not outdated.
	if flt(doc.total_sales_order_amount) != flt(so_doc.grand_total):
		frappe.throw(
			_(
				"The Total Sales Order Amount {0} is outdated. The current Sales Order's total is {1}. Please refresh the down payment invoice with the button on top of the form."
			).format(
				fmt_money(doc.total_sales_order_amount, currency=so_doc.currency),
				fmt_money(so_doc.grand_total, currency=so_doc.currency),
			)
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


@frappe.whitelist()
def make_payment_entry(source_name: str, reference_date=None):
	"""Same as **Sales Order** > **Create** > **Payment** for the linked order, with *Paid Amount* set to this invoice's *Down Payment Amount*."""
	frappe.has_permission("Payment Entry", "create", throw=True)
	dpi = frappe.get_doc("Down Payment Invoice", source_name)
	if dpi.docstatus != 1:
		frappe.throw(_("Submit the Down Payment Invoice before creating a payment."))
	if not dpi.sales_order:
		frappe.throw(_("Sales Order is required."))

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	return get_payment_entry(
		"Sales Order",
		dpi.sales_order,
		party_amount=flt(dpi.down_payment_amount),
		reference_date=reference_date,
	)
