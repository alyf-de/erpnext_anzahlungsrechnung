import frappe
from frappe import _
from frappe.utils import flt


def before_print(doc, method, print_settings):
	prepare_invoice_data_according_to_invoice_type(doc)


def prepare_invoice_data_according_to_invoice_type(doc):
	if doc.custom_invoice_type == "Invoice":
		_add_tax_rates_to_items(doc)
	if doc.custom_invoice_type == "Final Invoice":
		_prepare_final_invoice_data(doc)
		_add_tax_rates_to_items(doc)


def _prepare_final_invoice_data(doc):
	# we can assume that all positions are linked to the same sales order (see validations in sales_invoice.py)
	sales_order = frappe.get_doc("Sales Order", doc.items[0].sales_order)
	doc.set("items", sales_order.items)
	doc.set("taxes", sales_order.taxes)
	doc.set("item_wise_tax_details", sales_order.item_wise_tax_details)
	doc.total = sales_order.total
	doc.net_total = sales_order.net_total
	doc.grand_total = sales_order.grand_total
	doc.base_total = sales_order.base_total
	doc.base_net_total = sales_order.base_net_total
	doc.base_grand_total = sales_order.base_grand_total


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
			rates = _tax_rates_from_item_tax_rate(getattr(item, "item_tax_rate", None))
		item.tax_rate = rates if rates else None


def _tax_rates_from_item_tax_rate(item_tax_rate):
	if not item_tax_rate:
		return []
	data = frappe.parse_json(item_tax_rate) if isinstance(item_tax_rate, str) else item_tax_rate
	if not data:
		return []
	return sorted({flt(v) for v in data.values()})
