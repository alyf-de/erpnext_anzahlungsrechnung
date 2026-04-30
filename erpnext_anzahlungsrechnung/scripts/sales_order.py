import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as erpnext_make_sales_invoice
from erpnext.stock.get_item_details import ItemDetailsCtx, get_item_details
from frappe import _
from frappe.utils import cint, flt, today

from erpnext_anzahlungsrechnung.scripts.utils import has_additional_discount_on_grand_total


def before_validate(doc, event):
	_sync_income_account_on_sales_order_items(doc)
	_require_income_account_for_down_payment_sales_order(doc)
	_avoid_position_discounts_on_down_payment_invoices(doc)
	has_additional_discount_on_grand_total(doc)


def before_update_after_submit(doc, event):
	if doc.has_value_changed("custom_invoice_type"):
		_avoid_position_discounts_on_down_payment_invoices(doc)
		has_additional_discount_on_grand_total(doc)


def _sync_income_account_on_sales_order_items(doc):
	"""Set ``income_account`` like selling transactions: ``get_item_details`` (item / group / brand / company chain)."""
	if not doc.company or not doc.get("items"):
		return
	if not frappe.get_meta("Sales Order Item").get_field("income_account"):
		return

	company_changed = doc.has_value_changed("company")
	parent_dict = {fieldname: doc.get(fieldname) for fieldname in doc.meta.get_valid_columns()}
	parent_dict["document_type"] = "Sales Order Item"

	for item in doc.get("items") or []:
		if not item.get("item_code"):
			continue
		if not (not item.get("income_account") or company_changed or item.has_value_changed("item_code")):
			continue

		ctx: ItemDetailsCtx = ItemDetailsCtx(parent_dict.copy())
		ctx.update(item.as_dict())
		ctx.update(
			{
				"doctype": doc.doctype,
				"name": doc.name,
				"child_doctype": item.doctype,
				"child_docname": item.name,
				"ignore_pricing_rule": doc.get("ignore_pricing_rule") or 0,
			}
		)
		if not ctx.transaction_date:
			ctx.transaction_date = ctx.get("posting_date")

		ret = get_item_details(ctx, doc, for_validate=True, overwrite_warehouse=False)
		if ret.get("income_account"):
			item.income_account = ret["income_account"]


def _require_income_account_for_down_payment_sales_order(doc):
	"""Down-payment **Sales Order** rows that bill stock need an income account for allocation."""
	if doc.custom_invoice_type != "Down Payment Invoice":
		return
	for item in doc.get("items") or []:
		if not item.get("income_account"):
			frappe.throw(
				_(
					"Row {0}: Income Account could not be resolved for item {1} (company {2}). "
					"Check item defaults, item group / brand defaults, or company default income account."
				).format(item.idx, item.item_code, doc.company)
			)


def _avoid_position_discounts_on_down_payment_invoices(doc):
	if doc.custom_invoice_type != "Down Payment Invoice":
		return
	if not any(item.discount_percentage and item.discount_percentage > 0 for item in doc.items):
		return

	frappe.throw(
		_(
			"Position discounts are not allowed for Sales Orders with invoice type Down Payment Invoice. Use Apply Additional Discount On Net Total if you need an order-level discount."
		)
	)


@frappe.whitelist()
def make_sales_invoice_from_sales_order(source_name: str, target_doc: dict | None = None):
	"""Map Sales Order → **Down Payment Invoice** (partial) or **Sales Invoice** final (full billing)."""
	args = frappe.flags.args or frappe._dict()
	create_partial = cint(args.get("create_partial", 1))
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
	if not (0 < share < 100):
		frappe.throw(_("Bill share (%) must be greater than 0 and less than 100."))

	dpi = frappe.new_doc("Down Payment Invoice")
	dpi.sales_order = source_name
	dpi.customer = so.customer
	dpi.company = so.company
	dpi.posting_date = today()
	dpi.total_sales_order_amount = flt(so.grand_total)
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
