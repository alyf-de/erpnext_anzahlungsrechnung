# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

import frappe
from erpnext.controllers.accounts_controller import AccountsController
from frappe import _
from frappe.utils import flt, fmt_money

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	cancel_down_payment_invoice_journals,
	get_income_tax_totals_for_down_payment_invoice,
	get_submitted_payment_entries_against_down_payment_invoice,
	get_submitted_payment_entries_linked_via_clearing_journal,
	post_down_payment_invoice_submission_journals,
)
from erpnext_anzahlungsrechnung.scripts.utils import (
	get_company_down_payment_map,
	require_down_payment_accounts_for_income,
)


class DownPaymentInvoice(AccountsController):
	def validate(self):
		if not self.get("items"):
			frappe.throw(_("Add at least one item."))
		so_doc = _validate_sales_order_down_payment_flow(self)
		self.set_taxes()
		self.calculate_taxes_and_totals()
		self.set_down_payment_percentage(so_doc)
		_validate_company_down_payment_mapping(self)

	def calculate_taxes_and_totals(self):
		from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals

		# taxes_and_totals.calculate_totals() (v16 taxes_and_totals.py:751) has a doctype
		# allowlist ["Quotation", "Sales Order", "Delivery Note", "Sales Invoice", "POS Invoice"];
		# the else-branch reads `tax.category`, which Sales Taxes and Charges does not have and
		# BaseDocument has no __getattr__ -> AttributeError. Masquerade as Sales Order for the
		# duration; the helper only reads self.doctype. Call the module function directly rather
		# than super().calculate_taxes_and_totals() -- AccountsController's own version branches
		# on self.doctype too and would call calculate_commission()/calculate_contribution(),
		# which only exist on SellingController.
		self.meta  # v16: Document.meta is a cached_property (base_document.py:248). Prime it
		# with the real doctype before flipping, or Sales Order's meta sticks to this instance.
		original = self.doctype
		self.doctype = "Sales Order"
		try:
			calculate_taxes_and_totals(self)
		finally:
			self.doctype = original

	def set_down_payment_percentage(self, so_doc):
		"""Keep *Down Payment Percentage* truthful for manually created/amended documents."""
		so_grand_total = flt(so_doc.grand_total)
		self.down_payment_percentage = (
			flt(flt(self.grand_total) / so_grand_total * 100, self.precision("down_payment_percentage"))
			if so_grand_total
			else 0
		)

	def before_cancel(self):
		pes = set(get_submitted_payment_entries_against_down_payment_invoice(self.name))
		pes.update(get_submitted_payment_entries_linked_via_clearing_journal(self.name))
		if pes:
			frappe.throw(
				_("Cancel Payment Entries linked to this down payment before cancelling: {0}").format(
					", ".join(sorted(pes))
				)
			)

	def on_cancel(self):
		cancel_down_payment_invoice_journals(self)

	def on_submit(self):
		post_down_payment_invoice_submission_journals(self)


def _validate_sales_order_down_payment_flow(doc):
	"""
	Validate:
	- Sales Order's invoice type is "Down Payment Invoice".
	- Sales Order is submitted.
	- Sales Order is not billed.
	- Requested down payment does not exceed the Sales Order outstanding (grand total - advance paid).

	Returns the loaded Sales Order document for reuse by the caller.
	"""
	so_doc = frappe.get_doc("Sales Order", doc.sales_order)
	# 1. Sales Order's invoice type is "Down Payment Invoice".
	if so_doc.custom_invoice_type != "Down Payment Invoice":
		frappe.throw(
			_(
				"The Invoice Type ({0}) of the Sales Order {1} does not match the down payment flow (expected Down Payment Invoice)."
			).format(_(so_doc.custom_invoice_type or ""), so_doc.name)
		)

	# 2. Sales Order is submitted.
	if so_doc.docstatus != 1:
		frappe.throw(_("The Sales Order {0} is not submitted.").format(so_doc.name))

	# 3. Sales Order is not billed.
	if so_doc.per_billed > 0:
		frappe.throw(_("The Sales Order {0} is billed.").format(so_doc.name))

	# 4. Outstanding amount is smaller than the requested down payment amount.
	if (so_doc.grand_total - so_doc.advance_paid) < flt(doc.grand_total):
		frappe.throw(
			_(
				"The requested down payment amount ({0}) is greater than the outstanding amount ({1}) of the Sales Order {2}."
			).format(
				fmt_money(doc.grand_total, currency=so_doc.currency),
				fmt_money(so_doc.grand_total - so_doc.advance_paid, currency=so_doc.currency),
				so_doc.name,
			)
		)

	return so_doc


def _validate_company_down_payment_mapping(doc):
	if not doc.sales_order or not doc.company:
		return
	income_totals, _, _, _, _ = get_income_tax_totals_for_down_payment_invoice(doc)
	if income_totals:
		require_down_payment_accounts_for_income(doc.company, income_totals.keys())
	_validate_tax_accounts_match_company_mapping(doc, income_totals.keys())


def _validate_tax_accounts_match_company_mapping(doc, income_accounts):
	"""Each ``taxes`` row's ``account_head`` must match the tax account this document's income
	accounts are mapped to for that row's rate. ``down_payment_invoice_accounting.py`` now reads
	the tax account straight off ``tax.account_head`` instead of looking it up via
	``Company Down Payment Account``, so nothing else catches a mis-mapped company here -- and
	the final invoice's own rule (``scripts/sales_invoice.py::_validate_company_down_payment_accounts``)
	will later demand the account this validation expects. Mirrors that same rate -> tax_account
	comparison so both sides agree before the money moves anywhere."""
	if not income_accounts:
		return
	dp_map = get_company_down_payment_map(doc.company)
	rate_tol = 0.01
	for tax in doc.get("taxes") or []:
		if not flt(tax.base_tax_amount_after_discount_amount):
			continue
		for income_account in income_accounts:
			cfg = dp_map.get(income_account)
			if not cfg:
				continue
			expected_rate = flt(cfg.get("tax_rate"))
			if expected_rate <= 0 or abs(flt(tax.rate) - expected_rate) > rate_tol:
				continue
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


@frappe.whitelist()
def make_payment_entry(source_name: str, reference_date: str | None = None):
	"""Same as **Sales Order** > **Create** > **Payment** for the linked order, with *Paid Amount* set to this invoice's *Grand Total*."""
	frappe.has_permission("Payment Entry", "create", throw=True)
	dpi = frappe.get_doc("Down Payment Invoice", source_name)
	dpi.check_permission("read")
	if dpi.docstatus != 1:
		frappe.throw(_("Submit the Down Payment Invoice before creating a payment."))
	if not dpi.sales_order:
		frappe.throw(_("Sales Order is required."))

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	return get_payment_entry(
		"Sales Order",
		dpi.sales_order,
		party_amount=flt(dpi.grand_total),
		reference_date=reference_date,
	)
