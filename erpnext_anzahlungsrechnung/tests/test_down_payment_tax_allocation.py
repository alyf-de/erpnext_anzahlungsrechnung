# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_tax_allocation import (
	_round_gross_total,
	build_tax_rows_for_down_payment,
)


class TestDownPaymentTaxAllocation(IntegrationTestCase):
	def test_scales_by_grand_total(self):
		so = frappe._dict(
			grand_total=12900,
			item_wise_tax_details=[
				frappe._dict(rate=19, taxable_amount=10000, amount=1900),
				frappe._dict(rate=0, taxable_amount=1000, amount=0),
			],
		)
		rows = build_tax_rows_for_down_payment(so, 2580)
		self.assertEqual(len(rows), 2)
		total = sum(r["net_amount"] + r["tax_amount"] for r in rows)
		self.assertAlmostEqual(total, 2580, places=2)
		self.assertAlmostEqual(rows[0]["net_amount"], 2000, places=2)
		self.assertAlmostEqual(rows[0]["tax_amount"], 380, places=2)
		self.assertAlmostEqual(rows[1]["net_amount"], 200, places=2)
		self.assertAlmostEqual(rows[1]["tax_amount"], 0, places=2)

	def test_merges_same_rate(self):
		so = frappe._dict(
			grand_total=11900,
			item_wise_tax_details=[
				frappe._dict(rate=19, taxable_amount=5000, amount=950),
				frappe._dict(rate=19, taxable_amount=5000, amount=950),
			],
		)
		rows = build_tax_rows_for_down_payment(so, 2380)
		self.assertEqual(len(rows), 1)
		self.assertAlmostEqual(rows[0]["net_amount"], 2000, places=2)
		self.assertAlmostEqual(rows[0]["tax_amount"], 380, places=2)

	def test_multi_sales_tax_rows_do_not_double_count_zero_bucket(self):
		"""Same SO shape as multiple ``taxes`` rows: extra item_wise rows with amount 0."""
		so = frappe._dict(
			grand_total=12900,
			items=[
				frappe._dict(name="i1", net_amount=10000, item_tax_template="19 % - MG"),
				frappe._dict(name="i2", net_amount=1000, item_tax_template="0 % - MG"),
			],
			taxes=[
				frappe._dict(name="t19", description="Umsatzsteuer 19 %"),
				frappe._dict(name="t7", description="Umsatzsteuer 7 %"),
			],
			item_wise_tax_details=[
				frappe._dict(item_row="i1", tax_row="t19", rate=19, taxable_amount=10000, amount=1900),
				frappe._dict(item_row="i1", rate=0, taxable_amount=10000, amount=0),
				frappe._dict(item_row="i2", rate=0, taxable_amount=1000, amount=0),
				frappe._dict(item_row="i2", rate=0, taxable_amount=1000, amount=0),
			],
		)
		rows = build_tax_rows_for_down_payment(so, 2580)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["tax_description"], "Umsatzsteuer 19 %")
		self.assertAlmostEqual(rows[0]["net_amount"], 2000, places=2)
		self.assertAlmostEqual(rows[0]["tax_amount"], 380, places=2)
		self.assertAlmostEqual(rows[1]["net_amount"], 200, places=2)
		self.assertAlmostEqual(rows[1]["tax_amount"], 0, places=2)
		net_sum = sum(r["net_amount"] for r in rows)
		self.assertAlmostEqual(net_sum, 2200, places=2)
		self.assertGreaterEqual(rows[1]["tax_amount"], 0)

	def test_rounding_diff_applies_to_highest_tax_row_not_last(self):
		"""If gross sum misses target, adjust the row with the most tax (not always the last / 0% row)."""
		rows = [
			{"net_amount": 2000.0, "tax_amount": 380.0, "gross_amount": 2380.0},
			{"net_amount": 2400.0, "tax_amount": 0.0, "gross_amount": 2400.0},
		]
		_round_gross_total(rows, 2580.0, 2)
		self.assertAlmostEqual(rows[0]["tax_amount"], 380.0 - 2200.0, places=2)
		self.assertAlmostEqual(rows[1]["tax_amount"], 0.0, places=2)
		self.assertAlmostEqual(rows[0]["gross_amount"], 2000.0 + rows[0]["tax_amount"], places=2)
		self.assertAlmostEqual(sum(r["net_amount"] + r["tax_amount"] for r in rows), 2580.0, places=2)
