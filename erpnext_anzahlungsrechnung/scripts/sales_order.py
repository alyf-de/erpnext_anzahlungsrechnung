import frappe
from frappe import _


def before_validate(doc, event):
	_avoid_position_discounts_on_down_payment_invoices(doc)


def before_update_after_submit(doc, event):
	if doc.has_value_changed("custom_invoice_type"):
		_avoid_position_discounts_on_down_payment_invoices(doc)


def _avoid_position_discounts_on_down_payment_invoices(doc):
	if doc.custom_invoice_type != "Down Payment Invoice":
		return
	if not any(item.discount_percentage and item.discount_percentage > 0 for item in doc.items):
		return

	frappe.throw(
		_(
			"Position Discounts are not allowed for Orders that will be invoiced as Down Payment Invoices. You can use bulk discounts instead."
		)
	)
