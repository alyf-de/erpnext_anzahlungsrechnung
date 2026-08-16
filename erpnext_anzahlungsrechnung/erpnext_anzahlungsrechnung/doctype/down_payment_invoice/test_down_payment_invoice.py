# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	get_income_tax_totals_for_down_payment_invoice,
)

# FrappeTestCase (not IntegrationTestCase): the latter recursively auto-loads test records for
# every link-field dependency, which drags in erpnext's own Sales Order/Item test bootstrap and
# collides with any master data (Price Lists etc.) the site already has. Everything this test
# needs it creates itself, get-or-create style.

COMPANY = "_Test Company"


class IntegrationTestDownPaymentInvoice(FrappeTestCase):
	"""
	Integration tests for DownPaymentInvoice.
	Use this class for testing interactions between multiple components.
	"""

	def test_totals_and_income_tax_split_from_items_and_taxes(self):
		"""One income account taxed at 19%, one at 7%; a 30% down payment split across both.
		Amounts must be read straight off the DPI's own items/taxes, not derived from the SO."""
		tax_19 = self._get_or_create_tax_account("Test VAT 19", 19)
		tax_7 = self._get_or_create_tax_account("Test VAT 7", 7)
		itt_19 = self._get_or_create_item_tax_template("Test 19%", {tax_19: 19, tax_7: 0})
		itt_7 = self._get_or_create_item_tax_template("Test 7%", {tax_19: 0, tax_7: 7})
		liability = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Accrued Expenses"})
		self._set_company_down_payment_accounts(
			[
				("Sales - _TC", 19, tax_19, liability),
				("Service - _TC", 7, tax_7, liability),
			]
		)

		so = self._make_down_payment_sales_order(
			[
				{"income_account": "Sales - _TC", "net_amount": 1000},
				{"income_account": "Service - _TC", "net_amount": 500},
			]
		)

		dpi = frappe.new_doc("Down Payment Invoice")
		dpi.sales_order = so.name
		dpi.customer = so.customer
		dpi.company = so.company
		dpi.posting_date = today()
		dpi.due_date = today()
		dpi.letter_head = "Company Letterhead"
		dpi.append(
			"items",
			{
				"item_name": "Down payment (19%)",
				"qty": 1,
				"rate": 300,
				"income_account": "Sales - _TC",
				"item_tax_template": itt_19,
			},
		)
		dpi.append(
			"items",
			{
				"item_name": "Down payment (7%)",
				"qty": 1,
				"rate": 150,
				"income_account": "Service - _TC",
				"item_tax_template": itt_7,
			},
		)
		dpi.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": tax_19,
				"description": "VAT 19%",
				"rate": 19,
			},
		)
		dpi.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": tax_7,
				"description": "VAT 7%",
				"rate": 7,
			},
		)
		dpi.insert()

		# (a) one item row per income account, as constructed.
		self.assertEqual(len(dpi.items), 2)

		# (b) grand_total == net_total + total_taxes_and_charges.
		self.assertAlmostEqual(dpi.grand_total, dpi.net_total + dpi.total_taxes_and_charges, places=2)
		self.assertAlmostEqual(dpi.net_total, 450, places=2)

		# (c) get_income_tax_totals_for_down_payment_invoice returns the expected per-account
		# nets and per-account taxes.
		income_totals, tax_totals, _income_cc, _income_proj, _tax_cc = (
			get_income_tax_totals_for_down_payment_invoice(dpi)
		)
		self.assertAlmostEqual(income_totals["Sales - _TC"], 300, places=2)
		self.assertAlmostEqual(income_totals["Service - _TC"], 150, places=2)
		self.assertAlmostEqual(tax_totals[tax_19], 57, places=2)
		self.assertAlmostEqual(tax_totals[tax_7], 10.5, places=2)

		# (d) sum(income_totals) == net_total.
		self.assertAlmostEqual(flt(sum(income_totals.values())), dpi.net_total, places=2)

	def _get_or_create_tax_account(self, account_name, rate):
		name = f"{account_name} - _TC"
		if frappe.db.exists("Account", name):
			return name
		parent = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Tax", "is_group": 1})
		account = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": account_name,
				"company": COMPANY,
				"parent_account": parent,
				"account_type": "Tax",
				"is_group": 0,
			}
		).insert()
		return account.name

	def _get_or_create_item_tax_template(self, title, rate_by_account):
		name = f"{title} - _TC"
		if frappe.db.exists("Item Tax Template", name):
			return name
		template = frappe.get_doc(
			{
				"doctype": "Item Tax Template",
				"title": title,
				"company": COMPANY,
				"taxes": [
					{"tax_type": account, "tax_rate": rate} for account, rate in rate_by_account.items()
				],
			}
		).insert()
		return template.name

	def _set_company_down_payment_accounts(self, rows):
		# Avoid Company.save()/validate() -- unrelated core validations on this shared test
		# company (e.g. validate_provisional_account_for_non_stock_items) enqueue a background
		# job, which needs a Redis queue this test environment does not run. Write directly.
		liability = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Accrued Expenses"})
		frappe.db.set_value("Company", COMPANY, "custom_requested_payments_account", liability)
		frappe.db.delete(
			"Company Down Payment Account",
			{"parent": COMPANY, "parenttype": "Company", "parentfield": "custom_down_payment_accounts"},
		)
		for idx, (income_account, tax_rate, tax_account, received_account) in enumerate(rows, start=1):
			row = frappe.new_doc("Company Down Payment Account")
			row.update(
				{
					"income_account": income_account,
					"tax_rate": tax_rate,
					"tax_account": tax_account,
					"received_down_payment_account": received_account,
				}
			)
			row.parent, row.parenttype, row.parentfield = COMPANY, "Company", "custom_down_payment_accounts"
			row.idx = idx
			row.insert(ignore_permissions=True)

	def _make_down_payment_sales_order(self, item_rows):
		item = self._get_or_create_test_item()
		so = frappe.new_doc("Sales Order")
		so.customer = "_Test Customer"
		so.company = COMPANY
		so.transaction_date = today()
		so.delivery_date = today()
		so.custom_invoice_type = "Down Payment Invoice"
		for row in item_rows:
			so.append(
				"items",
				{
					"item_code": item,
					"qty": 1,
					"rate": row["net_amount"],
					"income_account": row["income_account"],
					"delivery_date": today(),
				},
			)
		so.insert()
		so.submit()
		return so

	def _get_or_create_test_item(self):
		item_code = "_Test DPI Service Item"
		if frappe.db.exists("Item", item_code):
			return item_code
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"stock_uom": "Nos",
			}
		).insert()
		return item.name
