from collections import OrderedDict

import frappe
from erpnext.controllers.accounts_controller import get_discount_date, get_due_date
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as erpnext_make_sales_invoice
from erpnext.stock.get_item_details import ItemDetailsCtx, get_item_details
from frappe import _
from frappe.utils import cint, flt, getdate, today

from erpnext_anzahlungsrechnung.scripts.utils import has_additional_discount_on_grand_total

# Copied verbatim from the Sales Order's own tax rows onto the Down Payment Invoice.
# Not tax_amount/total/base_* -- those are recomputed by calculate_taxes_and_totals().
TAX_COPY_FIELDS = (
	"charge_type",
	"row_id",
	"account_head",
	"cost_center",
	"description",
	"rate",
	"included_in_print_rate",
	"account_currency",
)


def before_validate(doc, event):
	validate_income_account_for_down_payment_sales_order(doc)
	has_additional_discount_on_grand_total(doc)


def before_update_after_submit(doc, event):
	if doc.has_value_changed("custom_invoice_type"):
		has_additional_discount_on_grand_total(doc)
		validate_income_account_for_down_payment_sales_order(doc)


def validate_income_account_for_down_payment_sales_order(doc):
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


# Do not annotate target_doc -- see quotation.make_sales_order (f7fdcd5).
# nosemgrep: frappe-semgrep-rules.rules.security.missing-argument-type-hint
@frappe.whitelist()
def make_sales_invoice_from_sales_order(source_name: str, target_doc=None):
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
	dpi.down_payment_percentage = share  # informational only; validate() derives the real value

	for row in _build_dpi_items_for_percentage(so, share):
		dpi.append("items", row)
	_copy_so_taxes_to_dpi(dpi, so)
	dpi.taxes_and_charges = so.taxes_and_charges

	dpi.letter_head = frappe.db.get_value("Company", so.company, "default_letter_head")
	if not dpi.letter_head:
		frappe.throw(_("Set Default Letter Head on Company {0}.").format(so.company))

	settings = frappe.get_cached_doc("Down Payment Settings")
	# Calculate totals before rendering the settings templates -- default_position_description
	# can reference {{ doc.grand_total }}, which would otherwise still be 0.0 at render time.
	dpi.run_method("calculate_taxes_and_totals")
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


def _copy_so_taxes_to_dpi(dpi, so):
	"""Copy the Sales Order's tax rows onto the DPI, skipping ``charge_type == "Actual"`` rows.

	TAX_COPY_FIELDS deliberately excludes ``tax_amount``/``total``/``base_*`` so ordinary rate-based
	rows scale themselves down to the DPI's smaller net via ``calculate_taxes_and_totals()``. But for
	an ``"Actual"`` row ``tax_amount`` *is* the charge (a flat fee, not a rate), and nothing
	recomputes it -- copying the row without its amount would leave a visible 0,00 charge on the
	DPI, and a flat SO-wide fee should not be pro-rated into a down payment anyway. Skip it.

	Skipping a row can break the ``row_id`` reference other rows use for "On Previous Row *" charge
	types (a 1-based index into the same table), so also skip any row chained off a skipped row and
	renumber the surviving rows' ``row_id`` to match their new position.
	"""
	dropped_idx = {t.idx for t in so.taxes if t.charge_type == "Actual"}
	changed = True
	while changed:
		changed = False
		for t in so.taxes:
			if t.idx not in dropped_idx and t.row_id and cint(t.row_id) in dropped_idx:
				dropped_idx.add(t.idx)
				changed = True

	kept = [t for t in so.taxes if t.idx not in dropped_idx]
	old_to_new_idx = {t.idx: new_idx for new_idx, t in enumerate(kept, start=1)}

	for tax in kept:
		row = {f: tax.get(f) for f in TAX_COPY_FIELDS}
		if row.get("row_id"):
			new_row_id = old_to_new_idx.get(cint(row["row_id"]))
			row["row_id"] = str(new_row_id) if new_row_id else None
		dpi.append("taxes", row)


def _build_dpi_items_for_percentage(so, pct):
	"""One synthetic row per (income account, item tax template) group, at ``pct`` of that
	group's net. Grouping by income account keeps the Journal Entry automation exact; grouping
	by item tax template keeps the VAT breakup exact. ``income_account`` is guaranteed non-empty
	by ``validate_income_account_for_down_payment_sales_order`` (Sales Order ``before_validate``).
	"""
	groups = OrderedDict()
	for row in so.items:
		key = (row.income_account, row.item_tax_template or "")
		g = groups.setdefault(
			key, {"net": 0.0, "cost_center": row.cost_center, "project": row.project or so.project}
		)
		g["net"] += flt(row.net_amount) or flt(row.amount)

	precision = frappe.get_precision("Down Payment Invoice Item", "rate")
	items = []
	for (income_account, template), g in groups.items():
		amount = flt(g["net"] * flt(pct) / 100, precision)
		if not amount:
			continue
		items.append(
			{
				"qty": 1,
				"rate": amount,
				"amount": amount,
				"income_account": income_account,
				"item_tax_template": template or None,
				"cost_center": g["cost_center"],
				"project": g["project"],
			}
		)
	return items


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
	"""Fill each item's *Item Name* (and the first item's *Description*) from **Down Payment
	Settings** (Jinja, `doc` = draft DPI). Must run after `dpi.down_payment_percentage` is set --
	the settings template and the German fallback below both render that field."""
	if not dpi.get("items"):
		return

	ctx = {"doc": dpi}
	name_tpl = (settings.default_position_name or "").strip()
	desc_tpl = (settings.default_position_description or "").strip()

	if name_tpl:
		# nosemgrep: frappe-semgrep-rules.rules.security.frappe-ssti
		item_name = frappe.render_template(name_tpl, ctx)
	else:
		item_name = _("Down Payment")
	for row in dpi.items:
		row.item_name = item_name

	if desc_tpl:
		# nosemgrep: frappe-semgrep-rules.rules.security.frappe-ssti
		description = frappe.render_template(desc_tpl, ctx)
	else:
		description = _("Es werden {0} % des Gesamtauftragswerts in Rechnung gestellt.").format(
			flt(dpi.down_payment_percentage, 2)
		)
	dpi.items[0].description = description
