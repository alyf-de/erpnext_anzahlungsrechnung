import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Min
from frappe.utils import cint, flt
from frappe.utils.formatters import format_value

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	get_down_payment_net_total,
	post_final_invoice_down_payment_neutralization_journals,
)
from erpnext_anzahlungsrechnung.scripts.utils import (
	aggregate_income_by_account,
	append_je_row,
	get_company_down_payment_map,
	has_additional_discount_on_grand_total,
	insert_and_submit_je,
	merge_je_account_rows,
	require_down_payment_accounts_for_income,
)


def before_validate(doc, event):
	validate_sales_order_consistency(doc)
	append_down_payment_invoice_to_final_invoice(doc)


def validate(doc, event):
	"""After ERPNext validate (taxes calculated): Company Down Payment Account mapping vs income/tax lines."""
	if doc.is_consolidated or doc.is_internal_transfer():
		return
	if cint(doc.get("is_pos")):
		return
	if doc.custom_invoice_type != "Final Invoice":
		return
	_validate_company_down_payment_accounts(doc)


def on_submit(doc, event):
	"""Post journal entries for final invoice flows (down payment neutralization, return reversals)."""
	if doc.is_consolidated or doc.is_internal_transfer():
		return
	if cint(doc.get("is_pos")):
		return

	if doc.is_return and doc.return_against:
		orig_type = frappe.db.get_value("Sales Invoice", doc.return_against, "custom_invoice_type")
		if orig_type == "Final Invoice":
			post_final_invoice_down_payment_neutralization_reversal_for_credit_note(doc)
		return

	if doc.custom_invoice_type == "Final Invoice":
		post_final_invoice_down_payment_neutralization_journals(doc)


def before_cancel(doc, event):
	frappe.throw(
		_(
			"Due to regulatory requirements, you cannot cancel an invoice. Alternatively you can create a return invoice."
		)
	)


def _validate_company_down_payment_accounts(doc):
	from erpnext.controllers.taxes_and_totals import ignore_item_wise_tax_details

	dp_map = get_company_down_payment_map(doc.company)
	income_totals, _cc, _proj = aggregate_income_by_account(doc)
	if income_totals:
		require_down_payment_accounts_for_income(doc.company, income_totals.keys())

	if ignore_item_wise_tax_details(doc):
		return

	rate_tol = 0.01
	for row in doc.get("_item_wise_tax_details") or []:
		tax = row.get("tax")
		item = row.get("item")
		if not tax or not item:
			continue
		if getattr(tax, "category", None) == "Valuation":
			continue
		income_account = (
			item.income_account
			if (not item.enable_deferred_revenue or doc.is_return)
			else item.deferred_revenue_account
		)
		if not income_account:
			continue
		cfg = dp_map.get(income_account)
		if not cfg:
			frappe.throw(
				_("Missing Company Down Payment Account row for income account {0}.").format(
					frappe.bold(income_account)
				)
			)

		expected_rate = flt(cfg.get("tax_rate"))
		row_rate = flt(row.get("rate"))
		amt = flt(row.get("amount"))

		# Company tax_rate picks the VAT bucket for this income account; other tax rows on the same item are ignored.
		if abs(row_rate - expected_rate) > rate_tol:
			continue

		if not amt:
			continue

		if expected_rate > 0:
			tax_acc = cfg.get("tax_account")
			if not tax_acc:
				frappe.throw(
					_("Set Tax Account on Company Down Payment Account for income account {0}.").format(
						frappe.bold(income_account)
					)
				)
			if tax.account_head != tax_acc:
				frappe.throw(
					_(
						"Tax account {0} does not match Company Down Payment Account mapping for income account {1} (expected {2})."
					).format(
						frappe.bold(tax.account_head),
						frappe.bold(income_account),
						frappe.bold(tax_acc),
					)
				)


def _validate_consistent_currency(doc):
	"""
	Ensure the currency of the Sales Invoice is consistent with the currencies of:
	Sales Order, Taxes and Income Accounts, Sales Invoice Currency, Debit To Currency.
	This validation only runs for Final Invoices.
	"""
	if doc.custom_invoice_type != "Final Invoice":
		return

	so_currency = frappe.db.get_value("Sales Order", doc.items[0].sales_order, "currency")
	si_currency = doc.currency

	# Check 1: Income Accounts
	seen_accounts = set()
	for item in doc.items:
		if item.income_account in seen_accounts:
			continue
		seen_accounts.add(item.income_account)
		if frappe.db.get_value("Account", item.income_account, "account_currency") != si_currency:
			frappe.throw(
				_(
					"The currency of the Income Account {0} does not match the currency of the Sales Invoice."
				).format(item.income_account)
			)

	# Check 2: Tax Accounts
	for tax in doc.taxes:
		if tax.account_currency != si_currency:
			frappe.throw(
				_("The currency of the Tax Row {0} does not match the currency of the Sales Invoice.").format(
					tax.idx
				)
			)

	# Check 3: Debit To Currency and Sales Order Currency
	if so_currency == si_currency == doc.party_account_currency:
		# All currencies are consistent
		return
	else:
		msg = _(
			"The currency of the Sales Invoice must be the same as the currency of the Sales Order and the Debit To Currency."
		)
		msg += "<br><br>"
		msg += _("Sales Order Currency: {0}").format(so_currency)
		msg += "<br>"
		msg += _("Sales Invoice Currency: {0}").format(si_currency)
		msg += "<br>"
		msg += _("Debit To Currency: {0}").format(doc.party_account_currency)
		frappe.throw(msg)


def validate_sales_order_consistency(doc):
	"""Orchestrate which validations run based on the invoice type and return against."""
	# Run for all invoice types
	_avoid_invoice_type_inconsistencies(doc.custom_invoice_type, doc.items)

	if doc.is_return and doc.return_against:
		# Run for all returns
		_ensure_invoice_type_consistency_for_returns(doc.return_against, doc.custom_invoice_type)

	if doc.custom_invoice_type == "Invoice":
		return
	elif doc.custom_invoice_type == "Final Invoice":
		_ensure_sales_order_is_linked(doc.items)
		_ensure_only_one_linked_sales_order(doc.items)
		_validate_consistent_currency(doc)
		_validate_final_invoice_income_accounts_match_sales_order(doc)
		has_additional_discount_on_grand_total(doc)
		if doc.is_return and doc.return_against:
			_inform_about_update_of_sales_order_billed_amount(doc.update_billed_amount_in_sales_order)
			# Actually we want that no extra positions are added. But this is already avoided by the _ensure_sales_order_is_linked validation.
		else:
			_ensure_final_invoice_completes_sales_order_positions(doc)
	else:
		frappe.throw(_("Invalid invoice type: {0}").format(doc.custom_invoice_type))


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


def _inform_about_update_of_sales_order_billed_amount(update_billed_amount):
	"""Require billed amount updates for final invoice returns."""
	if not update_billed_amount:
		frappe.msgprint(
			_(
				"Note: The Sales Order Billed Amount will not be updated for this return invoice, because the checkbox is not activated."
			)
		)
	else:
		frappe.msgprint(
			_(
				"Note: The Sales Order Billed Amount will be updated for this return invoice, because the checkbox is activated."
			)
		)


def _ensure_sales_order_is_linked(items):
	"""Require each invoice row to link to a Sales Order row."""
	if not all(item.sales_order for item in items):
		frappe.throw(_("All positions must be linked to a Sales Order."))


def _ensure_only_one_linked_sales_order(items):
	"""Ensure final invoices reference exactly one Sales Order."""
	if len({item.sales_order for item in items}) > 1:
		frappe.throw(_("Final Invoices can only process a single Sales Order."))


def _validate_final_invoice_income_accounts_match_sales_order(doc):
	"""Final **Sales Invoice** income account must match **Sales Order Item** ``income_account`` for each linked row."""
	rows_by_name = {
		r["name"]: r
		for r in frappe.get_all(
			"Sales Order Item",
			filters={"parent": doc.items[0].sales_order, "parenttype": "Sales Order"},
			fields=["name", "idx", "item_code", "income_account"],
		)
	}
	for item in doc.items:
		if not item.so_detail:
			continue
		so_row = rows_by_name.get(item.so_detail)
		if not so_row:
			frappe.throw(_("Sales Order Item {0} not found.").format(item.so_detail))
		if not so_row.get("income_account"):
			frappe.throw(
				_(
					"Sales Order row {0} has no Income Account. Save the Sales Order after upgrading the app, or set Item Default for company {1}."
				).format(so_row.idx, doc.company)
			)
		expected = so_row["income_account"]
		si_account = (
			item.income_account
			if (not item.enable_deferred_revenue or doc.is_return)
			else item.deferred_revenue_account
		)
		if si_account != expected:
			frappe.throw(
				_(
					"Income account on invoice row {0} ({1}) does not match Sales Order row {2} (expected {3}, got {4})."
				).format(
					item.idx,
					item.item_code or "",
					so_row.get("idx"),
					frappe.bold(expected),
					frappe.bold(si_account or ""),
				)
			)


def _ensure_final_invoice_completes_sales_order_positions(doc):
	"""Require this final invoice to bill every **Sales Order** line to 100% (remaining + this invoice = line amount)."""
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


def append_down_payment_invoice_to_final_invoice(doc):
	if doc.custom_invoice_type != "Final Invoice":
		return

	dpi = DocType("Down Payment Invoice")

	down_payment_invoices = (
		frappe.qb.from_(dpi)
		.select(
			dpi.name,
			dpi.posting_date,
			dpi.down_payment_amount,
		)
		.where((dpi.sales_order == doc.items[0].sales_order) & (dpi.docstatus == 1))
		.orderby(dpi.posting_date)
		.orderby(dpi.creation)
	).run(as_dict=True)

	ple = DocType("Payment Ledger Entry")
	invoice_names = [row.name for row in down_payment_invoices]
	first_payment_date_by_dpi = {}
	if invoice_names:
		for row in (
			frappe.qb.from_(ple)
			.select(ple.against_voucher_no, Min(ple.posting_date).as_("payment_date"))
			.where(
				(ple.against_voucher_type == "Down Payment Invoice")
				& (ple.against_voucher_no.isin(invoice_names))
				& (ple.delinked == 0)
				& (ple.account_type == "Receivable")
				& (ple.amount < 0)
			)
			.groupby(ple.against_voucher_no)
		).run(as_dict=True):
			first_payment_date_by_dpi[row.against_voucher_no] = row.payment_date

	so_name = doc.items[0].sales_order
	first_so_payment_date = None
	so_pay_rows = (
		frappe.qb.from_(ple)
		.select(Min(ple.posting_date).as_("payment_date"))
		.where(
			(ple.against_voucher_type == "Sales Order")
			& (ple.against_voucher_no == so_name)
			& (ple.delinked == 0)
			& (ple.account_type == "Receivable")
			& (ple.amount < 0)
		)
	).run(as_dict=True)
	if so_pay_rows and so_pay_rows[0].get("payment_date"):
		first_so_payment_date = so_pay_rows[0]["payment_date"]

	doc.set("custom_down_payments", [])
	for row in down_payment_invoices:
		dpi_doc = frappe.get_doc("Down Payment Invoice", row.name)
		net_total = get_down_payment_net_total(dpi_doc)
		tax_amount = flt(flt(row.down_payment_amount) - net_total, dpi_doc.precision("down_payment_amount"))
		doc.append(
			"custom_down_payments",
			{
				"invoice_no": row.name,
				"date": row.posting_date,
				"payment_date": first_payment_date_by_dpi.get(row.name) or first_so_payment_date,
				"net_total": net_total,
				"tax_amount": tax_amount,
				"grand_total": row.down_payment_amount,
			},
		)


def post_final_invoice_down_payment_neutralization_reversal_for_credit_note(return_si) -> str:
	"""Reverse final-invoice down payment neutralization **Journal Entries** in proportion to this credit note."""
	if not return_si.return_against:
		frappe.throw(_("Return invoice must reference the original invoice."))

	original = frappe.get_doc("Sales Invoice", return_si.return_against)
	if original.custom_invoice_type != "Final Invoice":
		frappe.throw(_("This reversal only applies when the original invoice is a Final Invoice."))

	neutralization_je_names = _get_submitted_final_invoice_dpi_neutralization_jes(original.name)
	if not neutralization_je_names:
		frappe.throw(
			_("Original final invoice {0} has no down payment neutralization journal.").format(original.name)
		)

	orig_net = abs(flt(original.base_net_total))
	ret_net = abs(flt(return_si.base_net_total))
	ratio = ret_net / orig_net if orig_net else 1.0

	last_je_name = None
	for je_name in neutralization_je_names:
		source_je = frappe.get_doc("Journal Entry", je_name)
		rows = []
		for line in source_je.accounts:
			debit = abs(flt(line.debit_in_account_currency)) * ratio
			credit = abs(flt(line.credit_in_account_currency)) * ratio
			if not debit and not credit:
				continue
			if debit:
				append_je_row(
					rows,
					line.account,
					0,
					debit,
					line.cost_center,
					line.project,
					party_type=line.party_type or None,
					party=line.party or None,
				)
			if credit:
				append_je_row(
					rows,
					line.account,
					credit,
					0,
					line.cost_center,
					line.project,
					party_type=line.party_type or None,
					party=line.party or None,
				)

		rows = merge_je_account_rows(rows)
		total_debit = sum(flt(r.get("debit_in_account_currency") or 0) for r in rows)
		total_credit = sum(flt(r.get("credit_in_account_currency") or 0) for r in rows)
		if abs(total_debit - total_credit) > 0.02:
			frappe.throw(_("Final invoice down payment credit note journal does not balance."))

		je = insert_and_submit_je(
			return_si.company,
			return_si.posting_date,
			rows,
			_("Final invoice down payment reversal for {0}").format(return_si.name),
			_("Final Invoice Down Payment Reversal"),
			sales_invoice=return_si.name,
			payment_entry=None,
		)
		last_je_name = je.name

	return last_je_name


def _get_submitted_final_invoice_dpi_neutralization_jes(final_invoice_name: str) -> list[str]:
	return frappe.get_all(
		"Journal Entry",
		filters=[
			["custom_dp_sales_invoice", "=", final_invoice_name],
			["docstatus", "=", 1],
			["custom_dp_down_payment_invoice", "is", "set"],
		],
		pluck="name",
		order_by="creation asc",
	)
