import frappe
from erpnext.accounts.doctype.payment_terms_template.payment_terms_template import (
	PaymentTermsTemplate as ERPPaymentTermsTemplate,
)
from frappe import _


class PaymentTermsTemplate(ERPPaymentTermsTemplate):
	def validate_terms(self):
		"""
		This method consists of two parts. We keep the first part (see below).
		We want to avoid the second part, that throw a duplicate error for payment terms with the same credit days.
		Since the credit days are calculated based on the invoice date in our case, they are unique (at least if they are not used in the Sales Invoice).
		"""
		for term in self.terms:
			if self.allocate_payment_based_on_payment_terms and not term.payment_term:
				frappe.throw(_("Row {0}: Payment Term is mandatory").format(term.idx))
