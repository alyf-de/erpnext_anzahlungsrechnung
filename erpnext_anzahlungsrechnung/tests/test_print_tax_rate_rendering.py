# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_anzahlungsrechnung.scripts.print_and_e_invoice_utils import _add_tax_rates_to_items


class TestPrintTaxRateRendering(FrappeTestCase):
	def test_no_effective_tax_defaults_to_zero_rate(self):
		doc = frappe._dict(
			items=[frappe._dict(name="ITEM-ROW-1", item_tax_rate={"USt 19": 19, "USt 0": 0})],
			item_wise_tax_details=[],
			taxes=[],
		)

		_add_tax_rates_to_items(doc)

		self.assertEqual(doc.items[0].tax_rate, [0.0])

	def test_zero_and_non_zero_item_wise_rates_keep_only_effective_non_zero_rate(self):
		doc = frappe._dict(
			items=[frappe._dict(name="ITEM-ROW-1", item_tax_rate=None)],
			item_wise_tax_details=[
				frappe._dict(item_row="ITEM-ROW-1", rate=19, taxable_amount=100, amount=19),
				frappe._dict(item_row="ITEM-ROW-1", rate=0, taxable_amount=100, amount=0),
			],
			taxes=[frappe._dict(tax_amount=19)],
		)

		_add_tax_rates_to_items(doc)

		self.assertEqual(doc.items[0].tax_rate, [19.0])

	def test_fallback_from_item_tax_rate_drops_zero_if_non_zero_present(self):
		doc = frappe._dict(
			items=[frappe._dict(name="ITEM-ROW-1", item_tax_rate={"USt 19": 19, "USt 0": 0})],
			item_wise_tax_details=[],
			taxes=[frappe._dict(tax_amount=19)],
		)

		_add_tax_rates_to_items(doc)

		self.assertEqual(doc.items[0].tax_rate, [19.0])
