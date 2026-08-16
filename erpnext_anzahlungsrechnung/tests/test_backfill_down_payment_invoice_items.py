# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

"""Tests for patches/backfill_down_payment_invoice_items.py -- the migration patch that backfills
``items``/``taxes``/``item_wise_tax_details`` on legacy **Down Payment Invoice** documents (see
PLAN.md / PLAN-AMENDMENTS.md §6 and the patch module's own docstring).

FrappeTestCase (not IntegrationTestCase), same reasoning as
``doctype/down_payment_invoice/test_down_payment_invoice.py``: avoid erpnext's Sales Order test
record bootstrap, which collides with master data already on a real site.
"""

from unittest.mock import patch as mock_patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from erpnext_anzahlungsrechnung.patches.backfill_down_payment_invoice_items import _migrate_one, execute

COMPANY = "_Test Company"


class TestBackfillDownPaymentInvoiceItems(FrappeTestCase):
	def test_migrates_legacy_dpi_into_items_and_taxes(self):
		"""A legacy-shaped DPI (``down_payment_amount`` set via raw SQL, no items/taxes) gets real
		child rows whose totals reconcile with the frozen legacy amount, and whose docstatus
		matches the parent's."""
		tax_19 = self._get_or_create_tax_account("Test Backfill VAT 19")
		self._set_company_down_payment_accounts([("Sales - _TC", 19, tax_19, self._liability_account())])

		so = self._make_down_payment_sales_order(
			[{"income_account": "Sales - _TC", "net_amount": 1000}], tax_19, 19
		)
		# net 1000 + 19% tax 190 = grand_total 1190; a 20% down payment is 238.
		dpi_name = self._make_legacy_dpi(so, down_payment_amount=238, docstatus=1)

		migrated = _migrate_one(dpi_name, 1, so.name, COMPANY)
		self.assertTrue(migrated)

		dpi = frappe.get_doc("Down Payment Invoice", dpi_name)
		self.assertAlmostEqual(dpi.net_total + dpi.total_taxes_and_charges, dpi.grand_total, places=2)
		self.assertAlmostEqual(dpi.grand_total, 238, places=2)
		self.assertTrue(dpi.items)
		for item in dpi.items:
			self.assertEqual(item.docstatus, dpi.docstatus)
		for tax in dpi.taxes:
			self.assertEqual(tax.docstatus, dpi.docstatus)

	def test_missing_income_account_is_not_silently_corrupted(self):
		"""A Sales Order Item with no ``income_account`` (a custom field this app adds with no
		backfill -- legacy orders genuinely lack it) must make ``_migrate_one`` raise instead of
		leaving the DPI's totals at DEFAULT 0, which downstream code would trust as real."""
		so = self._make_down_payment_sales_order(
			[{"income_account": "Sales - _TC", "net_amount": 1000}],
			self._get_or_create_tax_account("Test Backfill VAT 19 (missing acct)"),
			19,
		)
		# Simulate legacy data: blank the income account without going through validate().
		frappe.db.set_value("Sales Order Item", {"parent": so.name}, "income_account", None)
		dpi_name = self._make_legacy_dpi(so, down_payment_amount=238, docstatus=1)

		with self.assertRaises(Exception):
			_migrate_one(dpi_name, 1, so.name, COMPANY)
		self.assertFalse(frappe.db.exists("Down Payment Invoice Item", {"parent": dpi_name}))
		self.assertEqual(frappe.db.get_value("Down Payment Invoice", dpi_name, "grand_total"), 0)

	def test_execute_keeps_good_documents_and_fails_loudly_on_a_bad_one(self):
		"""``execute()`` must not lose a good document's migrated rows when a later document in the
		same batch fails (per-document savepoint, not a batch-wide rollback -- finding 1), and it
		must not exit quietly when a document was skipped (finding 2)."""
		tax_19 = self._get_or_create_tax_account("Test Backfill VAT 19 (batch)")
		self._set_company_down_payment_accounts([("Service - _TC", 19, tax_19, self._liability_account())])

		good_so = self._make_down_payment_sales_order(
			[{"income_account": "Service - _TC", "net_amount": 1000}], tax_19, 19
		)
		good_dpi = self._make_legacy_dpi(good_so, down_payment_amount=238, docstatus=1)

		bad_so = self._make_down_payment_sales_order(
			[{"income_account": "Service - _TC", "net_amount": 1000}], tax_19, 19
		)
		frappe.db.set_value("Sales Order Item", {"parent": bad_so.name}, "income_account", None)
		bad_dpi = self._make_legacy_dpi(bad_so, down_payment_amount=238, docstatus=1)

		real_get_all = frappe.get_all

		def fake_get_all(doctype, *args, **kwargs):
			# Scope execute()'s document scan to just the two DPIs this test built, instead of
			# scanning every Down Payment Invoice on this shared site. Every other frappe.get_all
			# call (e.g. the Company Down Payment Account lookup inside _migrate_one) passes
			# through untouched.
			if doctype == "Down Payment Invoice":
				return [
					frappe._dict(name=good_dpi, docstatus=1, sales_order=good_so.name, company=COMPANY),
					frappe._dict(name=bad_dpi, docstatus=1, sales_order=bad_so.name, company=COMPANY),
				]
			return real_get_all(doctype, *args, **kwargs)

		with mock_patch(
			"erpnext_anzahlungsrechnung.patches.backfill_down_payment_invoice_items.frappe.get_all",
			side_effect=fake_get_all,
		):
			with self.assertRaises(Exception):
				execute()

		# The good document's rows survived the bad document's per-document rollback.
		self.assertTrue(frappe.db.exists("Down Payment Invoice Item", {"parent": good_dpi}))
		self.assertFalse(frappe.db.exists("Down Payment Invoice Item", {"parent": bad_dpi}))

	# -- fixtures -------------------------------------------------------------------------------

	def _liability_account(self):
		return frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Accrued Expenses"})

	def _get_or_create_tax_account(self, account_name):
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

	def _set_company_down_payment_accounts(self, rows):
		# Avoid Company.save()/validate() -- unrelated core validations on this shared test company
		# enqueue a background job, which needs a Redis queue this test environment does not run.
		liability = self._liability_account()
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

	def _get_or_create_test_item(self):
		item_code = "_Test DPI Backfill Item"
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

	def _make_down_payment_sales_order(self, item_rows, tax_account, tax_rate):
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
		so.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": tax_account,
				"description": f"VAT {tax_rate}%",
				"rate": tax_rate,
			},
		)
		so.insert()
		so.submit()
		return so

	def _make_legacy_dpi(self, so, down_payment_amount, docstatus):
		"""A pre-refactor-shaped Down Payment Invoice: only the legacy scalar columns are set, no
		items/taxes -- exactly what a production document looked like before this app added real
		item/tax tables."""
		dpi = frappe.new_doc("Down Payment Invoice")
		dpi.sales_order = so.name
		dpi.customer = so.customer
		dpi.company = so.company
		dpi.posting_date = today()
		dpi.due_date = today()
		dpi.letter_head = "Company Letterhead"
		dpi.flags.ignore_validate = True
		dpi.insert(ignore_permissions=True, ignore_mandatory=True)
		if docstatus:
			frappe.db.set_value(
				"Down Payment Invoice", dpi.name, "docstatus", docstatus, update_modified=False
			)
		# Raw column write, matching how the patch itself reads this legacy field -- it is no
		# longer on the current DocType JSON ("chore(Down Payment Invoice): drop legacy amount
		# fields"), but the DB column still exists (frappe never drops columns on migrate).
		frappe.db.sql(
			"update `tabDown Payment Invoice` set down_payment_amount=%s where name=%s",
			(down_payment_amount, dpi.name),
		)
		return dpi.name
