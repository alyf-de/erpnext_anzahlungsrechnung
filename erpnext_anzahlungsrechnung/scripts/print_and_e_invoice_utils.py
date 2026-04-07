import frappe
from frappe import _


def before_print(doc, method, print_settings):
	prepare_invoice_data_according_to_invoice_type(doc)


def prepare_invoice_data_according_to_invoice_type(doc):
	if doc.custom_invoice_type == "Invoice":
		return
	elif doc.custom_invoice_type == "Down Payment Invoice":
		_prepare_down_payment_invoice_data(doc)
	elif doc.custom_invoice_type == "Final Invoice":
		_prepare_final_invoice_data(doc)


def _prepare_down_payment_invoice_data(doc):
	if not doc.custom_summarize_positions or not doc.custom_down_payment_invoice_description:
		return
	doc.set("items", [])
	doc.append(
		"items",
		{
			"item_code": "",
			"item_name": _("Down Payment"),
			"description": doc.custom_down_payment_invoice_description,
			"qty": 1,
			"rate": doc.net_total,
			"amount": doc.net_total,
			"discount_percentage": 0,
			"discount_amount": 0,
		},
	)


def _prepare_final_invoice_data(doc):
	# we can assume that all positions are linked to the same sales order (see validations in sales_invoice.py)
	sales_order = frappe.get_doc("Sales Order", doc.items[0].sales_order)
	doc.set("items", sales_order.items)
	doc.set("taxes", sales_order.taxes)
	doc.net_total = sales_order.net_total
	doc.grand_total = sales_order.grand_total
	doc.base_net_total = sales_order.base_net_total
	doc.base_grand_total = sales_order.base_grand_total
