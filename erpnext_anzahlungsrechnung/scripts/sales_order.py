import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as erpnext_make_sales_invoice
from frappe import _
from frappe.utils import cint, flt, today


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
	"""Map Sales Order → **Down Payment Invoice** (partial) or **Sales Invoice** final (full billing)."""
	args = frappe.flags.args or frappe._dict()
	create_partial = cint(args.get("create_partial", 1))
	summarize = cint(args.get("summarize_positions", 1))
	share = flt(args.get("share_percent", 100))

	so = frappe.get_doc("Sales Order", source_name)
	if so.docstatus != 1:
		frappe.throw(_("Sales Order must be submitted."))
	if so.custom_invoice_type == "Invoice":
		frappe.throw(_("Use the standard Sales Invoice action for this order."))

	if not create_partial:
		frappe.has_permission("Sales Invoice", "create", throw=True)
		doc = erpnext_make_sales_invoice(source_name, target_doc=target_doc, ignore_permissions=False)
		doc.set("custom_invoice_type", "Final Invoice")
		return doc

	frappe.has_permission("Down Payment Invoice", "create", throw=True)
	if not summarize:
		frappe.throw(_("Summarize Positions must be enabled to create a Down Payment Invoice."))
	if not (0 < share < 100):
		frappe.throw(_("Bill share (%) must be greater than 0 and less than 100."))

	dpi = frappe.new_doc("Down Payment Invoice")
	dpi.sales_order = source_name
	dpi.customer = so.customer
	dpi.company = so.company
	dpi.posting_date = today()
	precision = frappe.get_precision("Down Payment Invoice", "down_payment_amount") or 2
	dpi.down_payment_percentage = share
	dpi.down_payment_amount = flt(flt(so.grand_total) * share / 100.0, precision)

	dpi.letter_head = frappe.db.get_value("Company", so.company, "default_letter_head")
	if not dpi.letter_head:
		frappe.throw(_("Set Default Letter Head on Company {0}.").format(so.company))

	dpi.position_name = _("Down Payment")
	dpi.position_description = _("Es werden {0} % des Gesamtauftragswerts in Rechnung gestellt.").format(
		flt(share, 2)
	)

	return dpi
