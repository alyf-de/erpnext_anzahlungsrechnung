# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_anzahlungsrechnung.scripts.utils import has_additional_discount_on_grand_total


def _doc(**kwargs):
	"""A stand-in for the document the hook receives.

	``frappe._dict`` (not a plain dict) because the guard reads ``doc.custom_invoice_type`` /
	``doc.apply_discount_on`` as attributes and ``doc.get(...)`` for the discount fields.
	"""
	return frappe._dict(
		{
			"doctype": "Sales Order",
			"custom_invoice_type": "Down Payment Invoice",
			"apply_discount_on": "Grand Total",
			"additional_discount_percentage": 0,
			"discount_amount": 0,
			**kwargs,
		}
	)


class TestDownPaymentDiscountRules(FrappeTestCase):
	"""``has_additional_discount_on_grand_total`` is a guard, not a predicate: it either throws
	or returns ``None``. Assert on that, not on a truthy return value."""

	def test_grand_total_percent_throws(self):
		with self.assertRaises(frappe.ValidationError):
			has_additional_discount_on_grand_total(_doc(additional_discount_percentage=5))

	def test_grand_total_amount_throws(self):
		with self.assertRaises(frappe.ValidationError):
			has_additional_discount_on_grand_total(_doc(discount_amount=10))

	def test_final_invoice_on_sales_invoice_throws(self):
		with self.assertRaises(frappe.ValidationError):
			has_additional_discount_on_grand_total(
				_doc(doctype="Sales Invoice", custom_invoice_type="Final Invoice", discount_amount=10)
			)

	def test_net_total_is_allowed(self):
		self.assertIsNone(
			has_additional_discount_on_grand_total(
				_doc(apply_discount_on="Net Total", additional_discount_percentage=5)
			)
		)

	def test_plain_invoice_type_is_not_restricted(self):
		"""The rule only applies to the down payment invoice types; an ordinary order is free to
		discount on Grand Total."""
		self.assertIsNone(
			has_additional_discount_on_grand_total(
				_doc(custom_invoice_type="Invoice", additional_discount_percentage=5)
			)
		)

	def test_no_discount_is_allowed(self):
		self.assertIsNone(has_additional_discount_on_grand_total(_doc()))
