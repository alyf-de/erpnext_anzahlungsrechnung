# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
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
	def test_submit_does_not_post_journal(self):
		"""Step 1 does not create an opening **Journal Entry** (product spec)."""
		with patch(
			"erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting.insert_and_submit_je",
		) as mock_insert:
			out = post_down_payment_invoice_submission_journals(_Dpi())

		self.assertIsNone(out)
		mock_insert.assert_not_called()
