# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

from erpnext_anzahlungsrechnung.scripts.utils import has_additional_discount_on_grand_total


class TestDownPaymentDiscountRules(FrappeTestCase):
	def test_grand_total_percent_triggers(self):
		self.assertTrue(
			has_additional_discount_on_grand_total(
				{
					"apply_discount_on": "Grand Total",
					"additional_discount_percentage": 5,
					"discount_amount": 0,
				}
			)
		)

	def test_grand_total_amount_triggers(self):
		self.assertTrue(
			has_additional_discount_on_grand_total(
				{
					"apply_discount_on": "Grand Total",
					"additional_discount_percentage": 0,
					"discount_amount": 10,
				}
			)
		)

	def test_net_total_not_triggered_with_discount(self):
		self.assertFalse(
			has_additional_discount_on_grand_total(
				{"apply_discount_on": "Net Total", "additional_discount_percentage": 5, "discount_amount": 0}
			)
		)

	def test_default_apply_on_is_grand_total(self):
		self.assertTrue(
			has_additional_discount_on_grand_total(
				{"additional_discount_percentage": 1, "discount_amount": 0}
			)
		)
