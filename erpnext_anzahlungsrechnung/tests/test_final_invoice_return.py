# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_anzahlungsrechnung.scripts.sales_invoice import (
	_get_cumulative_returned_base_grand_total,
	_get_final_invoice_return_case,
	_is_full_final_invoice_return,
	_validate_final_invoice_return_workflow,
	on_submit,
)


def _return_doc(**kwargs):
	defaults = {
		"doctype": "Sales Invoice",
		"name": "SINV-RET-1",
		"is_return": 1,
		"return_against": "SINV-ORIG-1",
		"custom_invoice_type": "Final Invoice",
		"update_billed_amount_in_sales_order": 0,
		"base_grand_total": -1000,
		"items": [frappe._dict({"sales_order": "SO-001"})],
	}
	defaults.update(kwargs)
	return frappe._dict(defaults)


def _original_doc(**kwargs):
	defaults = {
		"name": "SINV-ORIG-1",
		"custom_invoice_type": "Final Invoice",
		"base_grand_total": 1000,
	}
	defaults.update(kwargs)
	return frappe._dict(defaults)


class TestFinalInvoiceReturnClassification(FrappeTestCase):
	def test_full_return_single_credit_note(self):
		return_si = _return_doc(base_grand_total=-1000)
		original = _original_doc(base_grand_total=1000)
		with patch(
			"erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.get_all",
			return_value=[],
		):
			self.assertTrue(_is_full_final_invoice_return(return_si, original))

	def test_partial_return(self):
		return_si = _return_doc(base_grand_total=-400)
		original = _original_doc(base_grand_total=1000)
		with patch(
			"erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.get_all",
			return_value=[],
		):
			self.assertFalse(_is_full_final_invoice_return(return_si, original))

	def test_cumulative_partials_reaching_full(self):
		return_si = _return_doc(name="SINV-RET-2", base_grand_total=-600)
		original = _original_doc(base_grand_total=1000)
		with patch(
			"erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.get_all",
			return_value=[-400],
		):
			self.assertTrue(_is_full_final_invoice_return(return_si, original))

	def test_cumulative_total_includes_current_and_prior(self):
		return_si = _return_doc(name="SINV-RET-2", base_grand_total=-600)
		with patch(
			"erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.get_all",
			return_value=[-400],
		):
			self.assertEqual(
				_get_cumulative_returned_base_grand_total("SINV-ORIG-1", return_si),
				1000,
			)

	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.get_doc")
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.db.get_value")
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.get_all", return_value=[])
	def test_return_case_matrix(self, mock_get_all, mock_get_value, mock_get_doc):
		mock_get_value.return_value = "Final Invoice"
		mock_get_doc.return_value = _original_doc()

		self.assertEqual(
			_get_final_invoice_return_case(
				_return_doc(update_billed_amount_in_sales_order=1, base_grand_total=-500)
			),
			1,
		)
		self.assertEqual(
			_get_final_invoice_return_case(
				_return_doc(update_billed_amount_in_sales_order=1, base_grand_total=-1000)
			),
			2,
		)
		self.assertEqual(
			_get_final_invoice_return_case(
				_return_doc(update_billed_amount_in_sales_order=0, base_grand_total=-500)
			),
			3,
		)
		self.assertEqual(
			_get_final_invoice_return_case(
				_return_doc(update_billed_amount_in_sales_order=0, base_grand_total=-1000)
			),
			4,
		)

	def test_non_final_invoice_return_returns_none(self):
		doc = _return_doc(custom_invoice_type="Invoice")
		self.assertIsNone(_get_final_invoice_return_case(doc))


class TestFinalInvoiceReturnWorkflow(FrappeTestCase):
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice._get_final_invoice_return_case", return_value=1)
	def test_case_1_throws(self, _mock_case):
		with self.assertRaises(frappe.ValidationError):
			_validate_final_invoice_return_workflow(_return_doc())

	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.msgprint")
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice._get_final_invoice_return_case", return_value=4)
	def test_case_4_msgprint(self, _mock_case, mock_msgprint):
		_validate_final_invoice_return_workflow(_return_doc())
		mock_msgprint.assert_called_once()

	@patch(
		"erpnext_anzahlungsrechnung.scripts.sales_invoice.post_final_invoice_down_payment_neutralization_reversal_for_credit_note"
	)
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.restore_final_invoice_payments_to_sales_order")
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.frappe.get_doc")
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice._get_final_invoice_return_case", return_value=2)
	def test_on_submit_case_2_runs_automations(self, _mock_case, mock_get_doc, mock_restore, mock_reverse):
		doc = _return_doc()
		mock_get_doc.return_value = _original_doc()
		on_submit(doc, None)
		mock_restore.assert_called_once()
		mock_reverse.assert_called_once()

	@patch(
		"erpnext_anzahlungsrechnung.scripts.sales_invoice.post_final_invoice_down_payment_neutralization_reversal_for_credit_note"
	)
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.restore_final_invoice_payments_to_sales_order")
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice._get_final_invoice_return_case", return_value=3)
	def test_on_submit_case_3_skips_automations(self, _mock_case, mock_restore, mock_reverse):
		on_submit(_return_doc(), None)
		mock_restore.assert_not_called()
		mock_reverse.assert_not_called()

	@patch(
		"erpnext_anzahlungsrechnung.scripts.sales_invoice.post_final_invoice_down_payment_neutralization_reversal_for_credit_note"
	)
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice.restore_final_invoice_payments_to_sales_order")
	@patch("erpnext_anzahlungsrechnung.scripts.sales_invoice._get_final_invoice_return_case", return_value=4)
	def test_on_submit_case_4_skips_automations(self, _mock_case, mock_restore, mock_reverse):
		on_submit(_return_doc(), None)
		mock_restore.assert_not_called()
		mock_reverse.assert_not_called()
