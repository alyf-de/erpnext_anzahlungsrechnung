# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_tax_allocation import (
	_aggregate_item_wise_by_rate,
)


class TestDownPaymentTaxAllocation(IntegrationTestCase):
	def test_multi_sales_tax_rows_do_not_double_count_zero_bucket(self):
		"""Same shape as multiple ``taxes`` rows on the DPI itself: extra item_wise rows with amount 0."""
		doc = frappe._dict(
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
		buckets = _aggregate_item_wise_by_rate(doc)
		self.assertEqual(len(buckets), 2)
		self.assertEqual(buckets[19]["tax_description"], "Umsatzsteuer 19 %")
		self.assertAlmostEqual(buckets[19]["taxable"], 10000, places=2)
		self.assertAlmostEqual(buckets[19]["tax"], 1900, places=2)
		self.assertAlmostEqual(buckets[0]["taxable"], 1000, places=2)
		self.assertAlmostEqual(buckets[0]["tax"], 0, places=2)
