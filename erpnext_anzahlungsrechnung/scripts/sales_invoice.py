import frappe
from frappe import _
from frappe.utils.formatters import format_value


def before_validate(doc, event):
	validate_sales_order_consistency(doc)
	append_down_payment_invoice_to_final_invoice(doc)
	set_amount_after_down_payments(doc)


def validate_sales_order_consistency(doc):
	"""Orchestrate which validations run based on the invoice type and return against."""
	if not doc.return_against:
		_avoid_invoice_type_inconsistencies(doc.custom_invoice_type, doc.items)

	if doc.return_against:
		_ensure_invoice_type_consistency_for_returns(doc.return_against, doc.custom_invoice_type)
		_avoid_returns_against_finished_down_payment_invoices(doc)
		_validate_updation_of_sales_order_billed_amount(
			doc.custom_invoice_type, doc.update_billed_amount_in_sales_order
		)

	if doc.custom_invoice_type in ["Down Payment Invoice", "Final Invoice"]:
		_ensure_sales_order_is_linked(doc.items)
		_ensure_only_one_linked_sales_order(doc.items)
		_prevent_position_discounts(doc)

	if doc.custom_invoice_type == "Down Payment Invoice":
		_validate_down_payment_invoice_billing_limits(doc)
		_prevent_additional_discounts(doc)

	if doc.custom_invoice_type == "Final Invoice":
		_ensure_final_invoice_completes_sales_order_positions(doc)
		_validate_sum_of_invoices_against_sales_order(doc)


def _avoid_invoice_type_inconsistencies(invoice_type, items):
	"""Ensure linked Sales Orders use the expected invoice type."""
	billing_mode = "Down Payment Invoice" if invoice_type == "Final Invoice" else invoice_type
	sales_orders = {item.sales_order for item in items if item.sales_order}
	for sales_order in sales_orders:
		sales_order_invoice_type = frappe.db.get_value("Sales Order", sales_order, "custom_invoice_type")
		if sales_order_invoice_type != billing_mode:
			frappe.throw(
				_(
					"The Invoice Type ({0}) of the Sales Order {1} does not match the Invoice Type of the Invoice."
				).format(_(sales_order_invoice_type), sales_order)
			)


def _ensure_invoice_type_consistency_for_returns(return_against, invoice_type):
	"""Ensure return invoice type matches the original invoice."""
	if frappe.db.get_value("Sales Invoice", return_against, "custom_invoice_type") != invoice_type:
		frappe.throw(_("The Invoice Type of the Return must match the Invoice Type of the original Invoice."))


def _avoid_returns_against_finished_down_payment_invoices(doc):
	"""Block returns once a down-payment-invoice Sales Order is fully billed."""
	if doc.custom_invoice_type != "Down Payment Invoice":
		return

	sales_order_per_billed = frappe.db.get_value("Sales Order", doc.items[0].sales_order, "per_billed")
	if sales_order_per_billed >= 100:
		frappe.throw(
			_(
				"This Down Payment Invoice has already been fully invoiced. No returns are allowed to avoid inconsistencies with Final Invoice."
			)
		)


def _validate_updation_of_sales_order_billed_amount(invoice_type, update_billed_amount):
	"""Require billed amount updates for down-payment/final invoice flows."""
	if invoice_type in ["Down Payment Invoice", "Final Invoice"] and not update_billed_amount:
		frappe.throw(
			_(
				"The Sales Order Billed Amount must be updated if it's a Down Payment Invoice or Final Invoice. Please activate the checkbox."
			)
		)


def _ensure_sales_order_is_linked(items):
	"""Require each invoice row to link to a Sales Order row."""
	if not all(item.sales_order for item in items):
		frappe.throw(_("All positions must be linked to a Sales Order."))


def _ensure_only_one_linked_sales_order(items):
	"""Ensure down-payment/final invoices reference exactly one Sales Order."""
	if len({item.sales_order for item in items}) > 1:
		frappe.throw(_("Down Payment Invoices or Final Invoices can only process a single Sales Order."))


def _validate_down_payment_invoice_billing_limits(doc):
	"""Prevent down payment invoices from overbilling or fully closing all order rows."""
	so_positions = {
		item["name"]: item
		for item in frappe.get_all(
			"Sales Order Item",
			filters={"parent": doc.items[0].sales_order},
			fields=["name", "idx", "item_name", "item_code", "amount", "billed_amt"],
		)
	}

	for invoice_item in doc.items:
		so_positions[invoice_item.so_detail]["billed_amt"] += invoice_item.amount

	if all(round(so_pos["billed_amt"], 2) >= round(so_pos["amount"], 2) for so_pos in so_positions.values()):
		frappe.throw(
			_(
				"This Down Payment Invoice tries to complete all positions of the Sales Order. At least one position must remain open."
			)
		)

	for so_pos in so_positions.values():
		if round(so_pos["billed_amt"], 2) > round(so_pos["amount"], 2):
			frappe.throw(
				_(
					"This Down Payment Invoice tries to overbill following Sales Order Position:<br><br>#{0} | {1}: {2} | Order Amount: {3}"
				).format(
					so_pos["idx"],
					so_pos["item_code"],
					so_pos["item_name"],
					format_value(so_pos["amount"], "Currency", doc.currency),
				)
			)


def _prevent_position_discounts(doc):
	"""Disallow position discounts on down payment or final invoices."""
	if any(item.discount_percentage for item in doc.items):
		frappe.throw(_("Position Discounts are not allowed for Down Payment Invoices or Final Invoices."))


def _prevent_additional_discounts(doc):
	"""Disallow additional discount amount on down payment invoices."""
	if doc.discount_amount:
		frappe.throw(
			_(
				"Additional discounts are not allowed for Down Payment Invoices. You can add them later to the Final Invoice."
			)
		)


def _ensure_final_invoice_completes_sales_order_positions(doc):
	"""Require final invoice to fully settle every Sales Order position."""
	so_positions = {
		position["name"]: position
		for position in frappe.get_all(
			"Sales Order Item",
			filters={"parent": doc.items[0].sales_order},
			fields=["name", "idx", "item_name", "amount", "billed_amt"],
			order_by="idx asc",
		)
	}

	invoice_amounts = {}
	for invoice_item in doc.items:
		if invoice_item.so_detail and invoice_item.so_detail in so_positions:
			if invoice_item.so_detail not in invoice_amounts:
				invoice_amounts[invoice_item.so_detail] = 0
			invoice_amounts[invoice_item.so_detail] += invoice_item.amount
			so_positions[invoice_item.so_detail]["billed_amt"] += invoice_item.amount

	not_fully_billed_positions = []
	for so_pos_key, so_pos in so_positions.items():
		if abs(round(so_pos.billed_amt, 2) - round(so_pos.amount, 2)) != 0:
			original_billed_amt = so_pos.billed_amt - invoice_amounts.get(so_pos_key, 0)
			remaining_before_invoice = so_pos.amount - original_billed_amt
			invoice_amount = invoice_amounts.get(so_pos_key, 0)
			not_fully_billed_positions.append(
				{
					"idx": so_pos.idx,
					"item_name": so_pos.item_name,
					"remaining": remaining_before_invoice,
					"invoice_amount": invoice_amount,
				}
			)

	if not_fully_billed_positions:
		error_message = _(
			"The following Sales Order positions are not fully billed and must be included in the Final Invoice:"
		)
		for position in not_fully_billed_positions:
			error_message += "<br>"
			error_message += _("- Position {0} ({1}): Remaining amount {2} | This invoice bills {3}").format(
				position["idx"],
				position["item_name"],
				format_value(position["remaining"], "Currency", doc.currency),
				format_value(position["invoice_amount"], "Currency", doc.currency),
			)
		frappe.throw(error_message)


def _validate_sum_of_invoices_against_sales_order(doc):
	"""Validate submitted invoice totals equal the linked Sales Order total."""
	from frappe.query_builder import DocType
	from frappe.query_builder.functions import Sum

	sales_order = doc.items[0].sales_order
	sales_invoice = DocType("Sales Invoice")
	sales_invoice_item = DocType("Sales Invoice Item")

	invoice_names = (
		frappe.qb.from_(sales_invoice_item)
		.select(sales_invoice_item.parent)
		.where(sales_invoice_item.sales_order == sales_order)
		.distinct()
	).run(pluck=True)

	if not invoice_names:
		frappe.throw(
			_(
				"No Invoices found for this Sales Order. You can't create a Final Invoice. Consider using 'normal' Invoices or creating Down Payment Invoices before creating a Final Invoice."
			)
		)

	invoiced_amount = (
		frappe.qb.from_(sales_invoice)
		.select(Sum(sales_invoice.net_total))
		.where(
			(sales_invoice.docstatus == 1)
			& (sales_invoice.name != doc.name)
			& (sales_invoice.name.isin(invoice_names))
		)
	).run()[0][0] or 0
	invoiced_amount += doc.net_total

	sales_order_amount = frappe.db.get_value("Sales Order", sales_order, "net_total")
	if abs(invoiced_amount - sales_order_amount) > 0.01:
		frappe.throw(
			_(
				"The sum of the Invoices ({0}) for this Sales Order is not equal to the Sales Order amount ({1})."
			).format(
				format_value(invoiced_amount, "Currency", doc.currency),
				format_value(sales_order_amount, "Currency", doc.currency),
			)
		)


def append_down_payment_invoice_to_final_invoice(doc):
	if doc.custom_invoice_type != "Final Invoice":
		return

	from frappe.query_builder import DocType

	sales_invoice = DocType("Sales Invoice")
	sales_invoice_item = DocType("Sales Invoice Item")

	down_payment_invoices = (
		frappe.qb.from_(sales_invoice)
		.inner_join(sales_invoice_item)
		.on(sales_invoice_item.parent == sales_invoice.name)
		.select(
			sales_invoice.name, sales_invoice.posting_date, sales_invoice.total, sales_invoice.grand_total
		)
		.where(
			(sales_invoice_item.sales_order == doc.items[0].sales_order)
			& (sales_invoice.docstatus == 1)
			& (sales_invoice.custom_invoice_type == "Down Payment Invoice")
		)
		.distinct()
		.orderby(sales_invoice.posting_date)
		.orderby(sales_invoice.creation)
	).run(as_dict=True)
	doc.set("custom_down_payments", [])
	for down_payment_invoice in down_payment_invoices:
		doc.append(
			"custom_down_payments",
			{
				"invoice_no": down_payment_invoice.name,
				"date": down_payment_invoice.posting_date,
				"net_total": down_payment_invoice.total,
				"grand_total": down_payment_invoice.grand_total,
			},
		)


def set_amount_after_down_payments(doc):
	if doc.custom_invoice_type != "Final Invoice":
		return

	doc.custom_outstanding_after_down_payments = doc.grand_total
