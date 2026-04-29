# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice import (
	_validate_down_payment_amounts,
)


class _MiniDoc:
	def __init__(self, **kw):
		self.__dict__.update(kw)

	def precision(self, fieldname):
		return 2


class TestDownPaymentInvoiceAmounts(FrappeTestCase):
	def test_rejects_percentage_ge_100(self):
		d = _MiniDoc(
			down_payment_amount=100,
			down_payment_percentage=100,
			total_sales_order_amount=200,
		)
		with self.assertRaises(frappe.ValidationError):
			_validate_down_payment_amounts(d)

	def test_accepts_matching_amount_and_percentage(self):
		d = _MiniDoc(
			down_payment_amount=50,
			down_payment_percentage=5,
			total_sales_order_amount=1000,
		)
		_validate_down_payment_amounts(d)
