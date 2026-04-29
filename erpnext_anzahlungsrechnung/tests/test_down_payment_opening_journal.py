# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	JE_TITLE_DOWN_PAYMENT_OPENING,
	post_down_payment_invoice_submission_journals,
)


class _Dpi:
	company = "Test Company"
	posting_date = "2026-04-29"
	customer = "Test Customer"
	sales_order = "SO-001"
	name = "DPI-001"
	down_payment_amount = 4380.0


class TestDownPaymentOpeningJournal(FrappeTestCase):
	def test_submit_posts_one_journal_with_receivable_and_requested_only(self):
		"""Regression: Step 1 must be one **Journal Entry** (Dr receivable, Cr requested) — not income/tax mirror."""
		mock_je = MagicMock()
		mock_je.name = "ACC-JV-TEST-00001"

		with (
			patch(
				"erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting.require_requested_payments_account"
			),
			patch(
				"erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting.get_requested_payments_account",
				return_value="3289 - Requested",
			),
			patch(
				"erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting.get_receivable_account_for_down_payment_invoice",
				return_value="1200 - Receivable",
			),
			patch(
				"erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting.insert_and_submit_je",
				return_value=mock_je,
			) as mock_insert,
		):
			out = post_down_payment_invoice_submission_journals(_Dpi())

		self.assertEqual(out, "ACC-JV-TEST-00001")
		self.assertEqual(mock_insert.call_count, 1)
		call_args = mock_insert.call_args[0]
		company, posting_date, accounts, _user_remark, title = call_args[:5]
		self.assertEqual(company, "Test Company")
		self.assertEqual(posting_date, "2026-04-29")
		self.assertEqual(title, JE_TITLE_DOWN_PAYMENT_OPENING)
		self.assertEqual(len(accounts), 2)
		debit_accounts = [a for a in accounts if a.get("debit_in_account_currency")]
		credit_accounts = [a for a in accounts if a.get("credit_in_account_currency")]
		self.assertEqual(len(debit_accounts), 1)
		self.assertEqual(len(credit_accounts), 1)
		self.assertEqual(debit_accounts[0]["account"], "1200 - Receivable")
		self.assertEqual(debit_accounts[0]["debit_in_account_currency"], 4380.0)
		self.assertEqual(credit_accounts[0]["account"], "3289 - Requested")
		self.assertEqual(credit_accounts[0]["credit_in_account_currency"], 4380.0)
