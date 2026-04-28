import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as erpnext_make_sales_invoice
from frappe import _
from frappe.utils import cint, flt


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


@frappe.whitelist()
def make_sales_invoice_from_sales_order(source_name: str, target_doc: dict | None = None):
	"""Map Sales Order → Sales Invoice with down payment / final options (dialog args in `frappe.flags.args`)."""
	frappe.has_permission("Sales Invoice", "create", throw=True)

	args = frappe.flags.args or frappe._dict()
	create_partial = cint(args.get("create_partial", 1))
	summarize = cint(args.get("summarize_positions", 1))
	share = flt(args.get("share_percent", 100))

	so = frappe.get_doc("Sales Order", source_name)
	if so.docstatus != 1:
		frappe.throw(_("Sales Order must be submitted."))
	if so.custom_invoice_type == "Invoice":
		frappe.throw(_("Use the standard Sales Invoice action for this order."))

	doc = erpnext_make_sales_invoice(source_name, target_doc=target_doc, ignore_permissions=False)
	_apply_service_period_fields_to_sales_invoice(doc, so)

	if not create_partial:
		doc.set("custom_invoice_type", "Final Invoice")
		return doc

	doc.set("custom_invoice_type", "Down Payment Invoice")
	doc.set("custom_summarize_positions", summarize)
	if summarize:
		if not (0 < share < 100):
			frappe.throw(_("Bill share (%) must be greater than 0 and less than 100."))
		_apply_share_of_total_order_to_items(doc, source_name, share)
		doc.set(
			"custom_down_payment_invoice_description",
			_("Es werden {0} % des Gesamtauftragswerts in Rechnung gestellt.").format(flt(share, 2)),
		)
		doc.run_method("calculate_taxes_and_totals")

	return doc


def _apply_service_period_fields_to_sales_invoice(doc, sales_order):
	if not doc.custom_service_period_from and sales_order.custom_service_period_from:
		doc.custom_service_period_from = sales_order.custom_service_period_from
	if not doc.custom_service_period_to and sales_order.custom_service_period_to:
		doc.custom_service_period_to = sales_order.custom_service_period_to

	so_items_by_name = {row.name: row for row in sales_order.items}
	for item in doc.items:
		so_item = so_items_by_name.get(item.so_detail) if item.so_detail else None
		if not so_item:
			continue
		if not item.custom_service_period_from and so_item.custom_service_period_from:
			item.custom_service_period_from = so_item.custom_service_period_from
		if not item.custom_service_period_to and so_item.custom_service_period_to:
			item.custom_service_period_to = so_item.custom_service_period_to


def _apply_share_of_total_order_to_items(doc, sales_order_name: str, share_percent: float):
	"""Each line amount = SO line total * share/100; throws if manual handling is needed."""
	so_items = {
		r["name"]: r
		for r in frappe.get_all(
			"Sales Order Item",
			filters={"parent": sales_order_name},
			fields=["name", "amount", "billed_amt"],
		)
	}
	p = frappe.get_precision("Sales Invoice Item", "amount") or 2
	tol = 10 ** (-p)
	cr = flt(doc.conversion_rate)

	for item in doc.items:
		so_row = so_items.get(item.so_detail) if item.so_detail else None
		if not so_row:
			frappe.throw(
				_(
					"This invoice has rows that are not linked to the Sales Order. Please create the Sales Invoice manually."
				)
			)

		line_amt = flt(so_row.get("amount"))
		billed = flt(so_row.get("billed_amt"))
		if line_amt <= 0:
			item.qty = item.amount = item.base_amount = 0
			if getattr(item, "stock_qty", None) is not None:
				item.stock_qty = 0
			continue

		if billed >= line_amt - tol:
			frappe.throw(
				_(
					"At least one order line is already fully billed. Please create the Sales Invoice manually."
				)
			)
		target = flt(line_amt * share_percent / 100.0, p)
		if target > line_amt - billed + tol:
			frappe.throw(
				_(
					"For at least one line, the requested share of the total order exceeds the remaining line amount. Please create the Sales Invoice manually."
				)
			)

		rate = flt(item.rate)
		if rate:
			item.qty = flt(target / rate, item.precision("qty"))
			if getattr(item, "stock_qty", None) is not None:
				item.stock_qty = flt(
					flt(item.qty) * flt(item.conversion_factor or 1.0),
					item.precision("stock_qty"),
				)
		else:
			item.qty = 0
		item.amount = target
		item.base_amount = flt(target * cr, item.precision("base_amount"))
