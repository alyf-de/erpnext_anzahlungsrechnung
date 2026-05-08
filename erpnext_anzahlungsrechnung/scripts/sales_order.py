import frappe
from erpnext.controllers.accounts_controller import get_discount_date, get_due_date
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as erpnext_make_sales_invoice
from erpnext.stock.get_item_details import ItemDetailsCtx, get_item_details
from frappe import _
from frappe.utils import cint, flt, getdate, today

from erpnext_anzahlungsrechnung.scripts.utils import has_additional_discount_on_grand_total


def before_validate(doc, event):
	_sync_income_account_on_sales_order_items(doc)
	_require_income_account_for_down_payment_sales_order(doc)
	has_additional_discount_on_grand_total(doc)


def before_update_after_submit(doc, event):
	if doc.has_value_changed("custom_invoice_type"):
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


@frappe.whitelist()
def make_sales_invoice_from_sales_order(source_name: str, target_doc: dict | None = None):
	"""Map Sales Order → **Down Payment Invoice** (partial) or **Sales Invoice** final (full billing)."""
	args = frappe.flags.args or frappe._dict()
	create_partial = cint(args.get("create_partial", 1))
	set_share_manually = cint(args.get("set_share_manually", 1))
	payment_schedule_row = args.get("payment_schedule_row")

	so = frappe.get_doc("Sales Order", source_name)
	if so.docstatus != 1:
		frappe.throw(_("Sales Order must be submitted."))
	if so.custom_invoice_type == "Invoice":
		frappe.throw(_("Use the standard Sales Invoice action for this order."))

	if not create_partial:
		frappe.has_permission("Sales Invoice", "create", throw=True)
		doc = erpnext_make_sales_invoice(source_name, target_doc=target_doc, ignore_permissions=False)
		doc.set("custom_invoice_type", "Final Invoice")
		_apply_final_invoice_payment_schedule_from_sales_order(so, doc)
		return doc

	frappe.has_permission("Down Payment Invoice", "create", throw=True)

	if set_share_manually:
		share = flt(args.get("share_percent", 0))
		if not (0 < share < 100):
			frappe.throw(_("Bill share (%) must be greater than 0 and less than 100."))
	else:
		if not payment_schedule_row:
			frappe.throw(_("Select a payment plan row (invoice portion)."))
		ps_list = so.get("payment_schedule") or []
		ps_row = next((r for r in ps_list if r.name == payment_schedule_row), None)
		if not ps_row:
			frappe.throw(_("The payment plan row does not belong to this Sales Order."))
		if ps_list and ps_row.name == ps_list[-1].name:
			frappe.throw(_("The last payment plan row is reserved for the Final Invoice."))
		share = flt(ps_row.invoice_portion)
		if not (0 < share < 100):
			frappe.throw(_("Invoice portion from the payment plan must be greater than 0 and less than 100."))

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

	settings = frappe.get_cached_doc("Down Payment Settings")
	_apply_default_position_from_settings(dpi, settings)
	dpi.due_date = frappe.utils.add_to_date(frappe.utils.getdate(), days=settings.credit_days)

	# Set project if custom field exists
	if frappe.db.exists(
		"Custom Field",
		{
			"dt": "Down Payment Invoice",
			"fieldtype": "Link",
			"options": "Project",
			"fetch_from": "sales_order.project",
		},
	):
		dpi.custom_project = so.project
	return dpi


def _apply_final_invoice_payment_schedule_from_sales_order(so, si):
	"""Replace **Sales Invoice** *Payment Schedule* with a single row copied from the last **Sales Order** row at 100%."""
	ps = so.get("payment_schedule") or []
	if not ps:
		return
	last = ps[-1]
	for d in list(si.get("payment_schedule") or []):
		si.remove(d)

	posting_date = si.posting_date or today()
	bill_date = si.get("bill_date") or posting_date
	auto_terms = cint(frappe.get_single_value("Accounts Settings", "automatically_fetch_payment_terms"))

	row = {
		"payment_term": last.payment_term,
		"description": last.description,
		"mode_of_payment": last.mode_of_payment,
		"invoice_portion": 100,
		"paid_amount": 0,
	}
	if auto_terms and last.get("due_date_based_on"):
		row["due_date_based_on"] = last.due_date_based_on
		row["credit_days"] = cint(last.credit_days)
		row["credit_months"] = cint(last.credit_months)
		row["due_date"] = get_due_date(last, posting_date, bill_date) or getdate(posting_date)
	else:
		row["due_date"] = last.due_date or getdate(posting_date)

	if last.get("discount_validity_based_on"):
		row["discount_validity_based_on"] = last.discount_validity_based_on
		row["discount_validity"] = cint(last.discount_validity)
		if auto_terms:
			dd = get_discount_date(last, posting_date, bill_date)
			if dd:
				row["discount_date"] = dd
		elif last.get("discount_date"):
			row["discount_date"] = last.discount_date

	if last.get("discount_type") == "Percentage":
		row["discount_type"] = last.discount_type
		row["discount"] = flt(last.discount)

	grand_total, base_grand_total = _payment_schedule_grand_totals_for_final_invoice(si)
	pay_prec = frappe.get_meta("Payment Schedule").get_field("payment_amount").precision or 2
	row["payment_amount"] = flt(grand_total, pay_prec)
	row["base_payment_amount"] = flt(base_grand_total, pay_prec)
	row["outstanding"] = row["payment_amount"]
	row["base_outstanding"] = row["base_payment_amount"]

	si.append("payment_schedule", row)
	si.payment_terms_template = so.payment_terms_template
	si.run_method("set_due_date")


def _payment_schedule_grand_totals_for_final_invoice(si):
	"""Grand totals used for *payment_amount* / *base_payment_amount*, aligned with payment schedule validation."""
	base_grand_total = flt(si.get("base_rounded_total") or si.base_grand_total) - flt(
		si.base_write_off_amount or 0
	)
	grand_total = flt(si.get("rounded_total") or si.grand_total) - flt(si.write_off_amount or 0)
	party_account_currency = si.get("party_account_currency")
	if not party_account_currency:
		from erpnext.accounts.party import get_party_account_currency

		party_account_currency = get_party_account_currency("Customer", si.customer, si.company)
	if si.get("total_advance"):
		if party_account_currency == si.company_currency:
			base_grand_total -= flt(si.total_advance)
			if flt(si.get("conversion_rate")):
				grand_total = flt(base_grand_total / si.conversion_rate, si.precision("grand_total"))
		else:
			grand_total -= flt(si.total_advance)
			base_grand_total = flt(
				grand_total * flt(si.get("conversion_rate")), si.precision("base_grand_total")
			)
	return grand_total, base_grand_total


def _apply_default_position_from_settings(dpi, settings):
	"""Fill *Position Name* / *Position Description* from **Down Payment Settings** (Jinja, `doc` = draft DPI)."""
	ctx = {"doc": dpi}
	name_tpl = (settings.default_position_name or "").strip()
	desc_tpl = (settings.default_position_description or "").strip()

	if name_tpl:
		# nosemgrep: frappe-semgrep-rules.rules.security.frappe-ssti
		dpi.position_name = frappe.render_template(name_tpl, ctx)
	else:
		dpi.position_name = _("Down Payment")
	if desc_tpl:
		# nosemgrep: frappe-semgrep-rules.rules.security.frappe-ssti
		dpi.position_description = frappe.render_template(desc_tpl, ctx)
	else:
		dpi.position_description = _("Es werden {0} % des Gesamtauftragswerts in Rechnung gestellt.").format(
			flt(dpi.down_payment_percentage, 2)
		)
