# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from datetime import date

from frappe.tests import UnitTestCase

from erpnext_anzahlungsrechnung.scripts.print_and_e_invoice_utils import _build_allocated_print_rows


class TestPriorDownPaymentPrintRows(UnitTestCase):
	def _totals(self, rows):
		return [
			{
				"invoice_no": invoice_no,
				"invoice_date": None,
				"grand_total": amount,
			}
			for invoice_no, amount in rows
		]

	def _payments(self, rows):
		return [
			{
				"pe": pe,
				"amount": amount,
				"payment_date": payment_date,
				"posting_date": posting_date,
			}
			for pe, amount, payment_date, posting_date in rows
		]

	def test_spec_example_assigns_by_payment_date(self):
		totals = self._totals([("DPI-1", 1000), ("DPI-2", 1000)])
		dpi_dates = [date(2026, 4, 1), date(2026, 4, 16)]
		payments = self._payments(
			[
				("PE-1", 400, date(2026, 4, 2), date(2026, 4, 2)),
				("PE-2", 400, date(2026, 4, 15), date(2026, 4, 15)),
				("PE-3", 1000, date(2026, 4, 16), date(2026, 4, 16)),
			]
		)

		rows = _build_allocated_print_rows(totals, dpi_dates, payments, 2)

		self.assertEqual(
			[(r["invoice_no"], r["payment_date"], r["paid_amount"]) for r in rows],
			[
				("DPI-1", date(2026, 4, 2), 400),
				("DPI-1", date(2026, 4, 15), 400),
				("DPI-2", date(2026, 4, 16), 1000),
			],
		)

	def test_same_dpi_date_prefers_later_invoice_before_earlier(self):
		totals = self._totals([("AZ-RE10069", 11144.64), ("AZ-RE10070", 1082.81)])
		dpi_dates = [date(2026, 3, 31), date(2026, 3, 31)]
		payments = self._payments(
			[
				("PE-1", 1082.81, date(2026, 4, 8), date(2026, 3, 31)),
				("PE-2", 11144.64, date(2026, 4, 8), date(2026, 6, 9)),
			]
		)

		rows = _build_allocated_print_rows(totals, dpi_dates, payments, 2)

		self.assertEqual(
			[(r["invoice_no"], r["payment_date"], r["paid_amount"]) for r in rows],
			[
				("AZ-RE10070", date(2026, 4, 8), 1082.81),
				("AZ-RE10069", date(2026, 4, 8), 11144.64),
			],
		)

	def test_overflow_spills_to_earlier_invoice_when_later_is_paid(self):
		totals = self._totals([("DPI-1", 1000), ("DPI-2", 1000)])
		dpi_dates = [date(2026, 4, 1), date(2026, 4, 16)]
		payments = self._payments(
			[
				("PE-1", 1200, date(2026, 4, 16), date(2026, 4, 16)),
			]
		)

		rows = _build_allocated_print_rows(totals, dpi_dates, payments, 2)

		self.assertEqual(
			[(r["invoice_no"], r["paid_amount"]) for r in rows],
			[
				("DPI-2", 1000),
				("DPI-1", 200),
			],
		)

	def test_unpaid_invoice_gets_placeholder_row(self):
		totals = self._totals([("DPI-1", 1000), ("DPI-2", 1000)])
		dpi_dates = [date(2026, 4, 1), date(2026, 4, 16)]
		payments = self._payments(
			[
				("PE-1", 400, date(2026, 4, 2), date(2026, 4, 2)),
			]
		)

		rows = _build_allocated_print_rows(totals, dpi_dates, payments, 2)

		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["invoice_no"], "DPI-1")
		self.assertEqual(rows[0]["paid_amount"], 400)
		self.assertEqual(rows[1]["invoice_no"], "DPI-2")
		self.assertIsNone(rows[1]["payment_date"])
		self.assertIsNone(rows[1]["paid_amount"])

	def test_taxes_shown_only_on_first_row_when_pe_splits(self):
		totals = self._totals([("DPI-1", 1000), ("DPI-2", 1000)])
		dpi_dates = [date(2026, 4, 1), date(2026, 4, 16)]
		payments = self._payments(
			[
				("PE-1", 1200, date(2026, 4, 16), date(2026, 4, 16)),
			]
		)
		pe_taxes = {
			"PE-1": [
				{"description": "Umsatzsteuer 19%", "amount": 95},
				{"description": "Umsatzsteuer 7%", "amount": 35},
			],
		}

		rows = _build_allocated_print_rows(totals, dpi_dates, payments, 2, pe_taxes)

		self.assertEqual(rows[0]["taxes"], pe_taxes["PE-1"])
		self.assertIsNone(rows[1]["taxes"])
