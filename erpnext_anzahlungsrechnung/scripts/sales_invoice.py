from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import cint, flt
from frappe.utils.formatters import format_value

from erpnext_anzahlungsrechnung.scripts.service_period_utils import (
	get_item_service_period_bounds,
	sync_standard_period_fields_from_custom,
	validate_service_period_ranges,
)
from erpnext_anzahlungsrechnung.scripts.utils import (
	aggregate_income_by_account,
	append_je_row,
	default_cost_center,
	get_company_down_payment_map,
	get_requested_payments_account,
	insert_and_submit_je,
	merge_je_account_rows,
	require_down_payment_accounts_for_income,
	require_requested_payments_account,
)


def before_validate(doc, event):
	_sync_service_period_fields(doc)
	validate_service_period_ranges(doc)
	validate_consistent_currency(doc)
	validate_sales_order_consistency(doc)
	append_down_payment_invoice_to_final_invoice(doc)


def _sync_service_period_fields(doc):
	_copy_service_period_fields_from_sales_order(doc)
	_derive_service_period_from_invoice_item_rows(doc)
	_sync_standard_invoice_period_fields(doc)


def _copy_service_period_fields_from_sales_order(doc):
	if not doc.items:
		return

	linked_sales_orders = {item.sales_order for item in doc.items if item.sales_order}
	if len(linked_sales_orders) == 1:
		sales_order_name = next(iter(linked_sales_orders))
		so_period = frappe.db.get_value(
			"Sales Order",
			sales_order_name,
			["custom_service_period_from", "custom_service_period_to"],
			as_dict=True,
		)
		if so_period:
			if not doc.custom_service_period_from and so_period.get("custom_service_period_from"):
				doc.custom_service_period_from = so_period.get("custom_service_period_from")
			if not doc.custom_service_period_to and so_period.get("custom_service_period_to"):
				doc.custom_service_period_to = so_period.get("custom_service_period_to")

	so_detail_names = list({item.so_detail for item in doc.items if item.so_detail})
	if not so_detail_names:
		return

	so_items = frappe.get_all(
		"Sales Order Item",
		filters={"name": ("in", so_detail_names)},
		fields=["name", "custom_service_period_from", "custom_service_period_to"],
	)
	so_items_by_name = {row.name: row for row in so_items}

	for item in doc.items:
		if not item.so_detail:
			continue
		so_item = so_items_by_name.get(item.so_detail)
		if not so_item:
			continue
		if not item.custom_service_period_from and so_item.get("custom_service_period_from"):
			item.custom_service_period_from = so_item.get("custom_service_period_from")
		if not item.custom_service_period_to and so_item.get("custom_service_period_to"):
			item.custom_service_period_to = so_item.get("custom_service_period_to")


def _derive_service_period_from_invoice_item_rows(doc):
	item_from_date, item_to_date = get_item_service_period_bounds(doc.items)
	if item_from_date and not doc.custom_service_period_from:
		doc.custom_service_period_from = item_from_date
	if item_to_date and not doc.custom_service_period_to:
		doc.custom_service_period_to = item_to_date


def _sync_standard_invoice_period_fields(doc):
	sync_standard_period_fields_from_custom(doc)


def validate(doc, event):
	"""After ERPNext validate (taxes calculated): Company Down Payment Account mapping vs income/tax lines."""
	if doc.is_consolidated or doc.is_internal_transfer():
		return
	if cint(doc.get("is_pos")):
		return
	if doc.custom_invoice_type not in ("Down Payment Invoice", "Final Invoice"):
		return
	_validate_company_down_payment_accounts(doc)


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


def on_submit(doc, event):
	"""Post journal entries for down payment / final invoice flows (neutralization, prepayment recognition, return reversals)."""
	if doc.is_consolidated or doc.is_internal_transfer():
		return
	if cint(doc.get("is_pos")):
		return

	if doc.is_return and doc.return_against:
		orig_type = frappe.db.get_value("Sales Invoice", doc.return_against, "custom_invoice_type")
		if orig_type == "Down Payment Invoice":
			pes = get_submitted_payment_entries_against_si(doc.return_against)
			if pes:
				frappe.throw(
					_("Cancel Payment Entries against {0} before this return: {1}").format(
						doc.return_against,
						", ".join(pes),
					)
				)
			post_neutralization_reversal_journal_for_down_payment_credit_note(doc)
		elif orig_type == "Final Invoice":
			post_prepayment_reversal_journal_for_final_invoice_credit_note(doc)
		return

	if doc.custom_invoice_type == "Down Payment Invoice":
		post_neutralization_journal_for_down_payment_invoice(doc)
	elif doc.custom_invoice_type == "Final Invoice":
		post_prepayment_recognition_journal_for_final_invoice(doc)


def validate_consistent_currency(doc):
	"""
	Ensure the currency of the Sales Invoice is consistent with the currencies of:
	Sales Order, Taxes and Income Accounts, Sales Invoice Currency, Debit To Currency.
	This validation only runs for Down Payment Invoices and Final Invoices.
	"""
	if doc.custom_invoice_type not in ["Down Payment Invoice", "Final Invoice"]:
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

	sales_invoice = DocType("Sales Invoice")
	sales_invoice_item = DocType("Sales Invoice Item")

	down_payment_invoices = (
		frappe.qb.from_(sales_invoice)
		.inner_join(sales_invoice_item)
		.on(sales_invoice_item.parent == sales_invoice.name)
		.select(
			sales_invoice.name,
			sales_invoice.posting_date,
			sales_invoice.net_total,
			sales_invoice.total_taxes_and_charges,
			sales_invoice.grand_total,
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

	from frappe.query_builder.functions import Min

	ple = DocType("Payment Ledger Entry")
	invoice_names = [row.name for row in down_payment_invoices]
	first_payment_date_by_si = {}
	if invoice_names:
		# PLE for receivable: invoice submission increases outstanding (amount > 0); payments and
		# similar credits reduce it (amount < 0). Earliest posting_date among the latter is the first payment.
		for row in (
			frappe.qb.from_(ple)
			.select(ple.against_voucher_no, Min(ple.posting_date).as_("payment_date"))
			.where(
				(ple.against_voucher_type == "Sales Invoice")
				& (ple.against_voucher_no.isin(invoice_names))
				& (ple.delinked == 0)
				& (ple.account_type == "Receivable")
				& (ple.amount < 0)
			)
			.groupby(ple.against_voucher_no)
		).run(as_dict=True):
			first_payment_date_by_si[row.against_voucher_no] = row.payment_date

	doc.set("custom_down_payments", [])
	for down_payment_invoice in down_payment_invoices:
		doc.append(
			"custom_down_payments",
			{
				"invoice_no": down_payment_invoice.name,
				"date": down_payment_invoice.posting_date,
				"payment_date": first_payment_date_by_si.get(down_payment_invoice.name),
				"net_total": down_payment_invoice.net_total,
				"tax_amount": flt(down_payment_invoice.total_taxes_and_charges),
				"grand_total": down_payment_invoice.grand_total,
			},
		)


def post_neutralization_journal_for_down_payment_invoice(si) -> str:
	"""Debit income and tax accounts (undoing the SI posting), credit Requested Payments liability."""
	require_requested_payments_account(si.company)
	requested_acc = get_requested_payments_account(si.company)

	income_totals, income_cc, income_proj = aggregate_income_by_account(si)
	tax_totals, tax_cc = _aggregate_tax_by_account(si)

	rows = []
	for acc, amt in sorted(income_totals.items()):
		append_je_row(
			rows,
			acc,
			amt,
			0,
			income_cc.get(acc) or default_cost_center(si.company),
			income_proj.get(acc),
		)
	for acc, amt in sorted(tax_totals.items()):
		append_je_row(rows, acc, amt, 0, tax_cc.get(acc) or default_cost_center(si.company), None)

	total_debit = sum(flt(r.get("debit_in_account_currency") or 0) for r in rows)
	if not total_debit:
		frappe.throw(_("No income or tax lines to neutralize for {0}.").format(si.name))

	append_je_row(
		rows,
		requested_acc,
		0,
		total_debit,
		None,
		None,
	)

	je = insert_and_submit_je(
		si.company,
		si.posting_date,
		rows,
		_("Down payment invoice neutralization for {0}").format(si.name),
		_("Down Payment Neutralization"),
		sales_invoice=si.name,
		payment_entry=None,
	)
	return je.name


def post_neutralization_reversal_journal_for_down_payment_credit_note(return_si) -> str:
	"""Mirror of neutralization for a credit note: credit income/tax, debit Requested Payments (pairs with return SI GL)."""
	require_requested_payments_account(return_si.company)
	requested_acc = get_requested_payments_account(return_si.company)

	income_totals, income_cc, income_proj = aggregate_income_by_account(return_si)
	tax_totals, tax_cc = _aggregate_tax_by_account(return_si)

	rows = []
	for acc, amt in sorted(income_totals.items()):
		amt = abs(flt(amt))
		if not amt:
			continue
		append_je_row(
			rows,
			acc,
			0,
			amt,
			income_cc.get(acc) or default_cost_center(return_si.company),
			income_proj.get(acc),
		)
	for acc, amt in sorted(tax_totals.items()):
		amt = abs(flt(amt))
		if not amt:
			continue
		append_je_row(
			rows,
			acc,
			0,
			amt,
			tax_cc.get(acc) or default_cost_center(return_si.company),
			None,
		)

	total_credit = sum(flt(r.get("credit_in_account_currency") or 0) for r in rows)
	if not total_credit:
		frappe.throw(_("No income or tax lines for reversal on {0}.").format(return_si.name))

	append_je_row(
		rows,
		requested_acc,
		total_credit,
		0,
		None,
		None,
	)

	je = insert_and_submit_je(
		return_si.company,
		return_si.posting_date,
		rows,
		_("Down payment credit note — neutralization reversal for {0}").format(return_si.name),
		_("Down Payment Neutralization Rev."),
		sales_invoice=return_si.name,
		payment_entry=None,
	)
	return je.name


def post_prepayment_recognition_journal_for_final_invoice(si) -> str | None:
	"""Debit Received Down Payment liability and credit income — one balanced Journal Entry per income account."""
	dp_map = get_company_down_payment_map(si.company)

	last_je_name = None

	for dp in si.get("custom_down_payments") or []:
		dpsi = frappe.get_doc("Sales Invoice", dp.invoice_no)
		if dpsi.docstatus != 1 or dpsi.custom_invoice_type != "Down Payment Invoice":
			frappe.throw(
				_("Down payment invoice {0} must be a submitted Down Payment Invoice.").format(dp.invoice_no)
			)
		if not _get_latest_submitted_je_for_si(dp.invoice_no):
			frappe.throw(
				_("Down payment invoice {0} has no neutralization journal yet.").format(dp.invoice_no)
			)

		income_totals, income_cc, income_proj = aggregate_income_by_account(dpsi)
		require_down_payment_accounts_for_income(si.company, income_totals.keys())
		dp_net = flt(dp.net_total)
		si_net = flt(dpsi.base_net_total)
		if not si_net or not dp_net:
			continue
		if not income_totals:
			frappe.throw(
				_("Down payment invoice {0} has no income lines for prepayment recognition.").format(
					dp.invoice_no
				)
			)

		buckets = []
		for acc, amt in income_totals.items():
			share = flt(dp_net * amt / si_net, 2)
			if share:
				received_acc = dp_map[acc]["received_down_payment_account"]
				buckets.append((acc, share, received_acc, income_cc.get(acc), income_proj.get(acc)))

		sum_shares = sum(b[1] for b in buckets)
		if buckets and abs(sum_shares - dp_net) > 0.02:
			first = buckets[0]
			buckets[0] = (
				first[0],
				flt(first[1] + (dp_net - sum_shares)),
				first[2],
				first[3],
				first[4],
			)

		for acc, share, received_acc, icc, ip in buckets:
			rows = []
			append_je_row(
				rows,
				received_acc,
				share,
				0,
				None,
				None,
				party_type="Customer",
				party=si.customer,
			)
			append_je_row(
				rows,
				acc,
				0,
				share,
				icc or default_cost_center(si.company),
				ip,
			)
			je = insert_and_submit_je(
				si.company,
				si.posting_date,
				rows,
				_("Final invoice prepayment recognition for {0} ({1})").format(si.name, acc),
				_("Final Invoice Prepayment Recognition"),
				sales_invoice=si.name,
				payment_entry=None,
			)
			last_je_name = je.name

	return last_je_name


def post_prepayment_reversal_journal_for_final_invoice_credit_note(return_si) -> str:
	"""Reverse prepayment recognition in proportion to this credit note against a Final Invoice."""
	if not return_si.return_against:
		frappe.throw(_("Return invoice must reference the original invoice."))

	original = frappe.get_doc("Sales Invoice", return_si.return_against)
	if original.custom_invoice_type != "Final Invoice":
		frappe.throw(_("This reversal only applies when the original invoice is a Final Invoice."))

	prepayment_je_names = _get_submitted_prepayment_recognition_jes_for_final_invoice(original.name)
	if not prepayment_je_names:
		frappe.throw(
			_("Original final invoice {0} has no prepayment recognition journal.").format(original.name)
		)

	orig_net = abs(flt(original.base_net_total))
	ret_net = abs(flt(return_si.base_net_total))
	ratio = ret_net / orig_net if orig_net else 1.0

	last_je_name = None
	for prepayment_je_name in prepayment_je_names:
		prepayment_je = frappe.get_doc("Journal Entry", prepayment_je_name)
		rows = []
		for line in prepayment_je.accounts:
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
			frappe.throw(_("Final invoice prepayment credit note journal does not balance."))

		je = insert_and_submit_je(
			return_si.company,
			return_si.posting_date,
			rows,
			_("Final invoice prepayment reversal for {0}").format(return_si.name),
			_("Final Invoice Prepayment Reversal"),
			sales_invoice=return_si.name,
			payment_entry=None,
		)
		last_je_name = je.name

	return last_je_name


def get_submitted_payment_entries_against_si(sales_invoice_name: str) -> list[str]:
	"""Names of submitted Payment Entries that allocate to this Sales Invoice."""
	pe_ref = DocType("Payment Entry Reference")
	pe = DocType("Payment Entry")
	q = (
		frappe.qb.from_(pe_ref)
		.inner_join(pe)
		.on(pe_ref.parent == pe.name)
		.select(pe.name)
		.distinct()
		.where(
			(pe_ref.reference_doctype == "Sales Invoice")
			& (pe_ref.reference_name == sales_invoice_name)
			& (pe.docstatus == 1)
		)
	)
	return q.run(pluck=True)


def _get_latest_submitted_je_for_si(sales_invoice_name: str) -> str | None:
	"""Newest submitted automation Journal Entry linked to this Sales Invoice (`custom_dp_sales_invoice`)."""
	names = frappe.get_all(
		"Journal Entry",
		filters={"custom_dp_sales_invoice": sales_invoice_name, "docstatus": 1},
		pluck="name",
		order_by="creation desc",
		limit_page_length=1,
	)
	return names[0] if names else None


def _get_submitted_prepayment_recognition_jes_for_final_invoice(final_invoice_name: str) -> list[str]:
	"""All submitted prepayment recognition JEs for a Final Invoice (one per income account)."""
	return frappe.get_all(
		"Journal Entry",
		filters={"custom_dp_sales_invoice": final_invoice_name, "docstatus": 1},
		pluck="name",
		order_by="creation asc",
	)


def _aggregate_tax_by_account(doc):
	enable_discount_accounting = cint(
		frappe.get_single_value("Selling Settings", "enable_discount_accounting")
	)
	totals = defaultdict(float)
	cc = {}
	for tax in doc.get("taxes") or []:
		if not tax.account_head:
			continue
		if not flt(tax.base_tax_amount_after_discount_amount):
			continue
		_tax_amt, base_amount = doc.get_tax_amounts(tax, enable_discount_accounting)
		totals[tax.account_head] += flt(base_amount)
		cc.setdefault(tax.account_head, tax.cost_center)
	return totals, cc
