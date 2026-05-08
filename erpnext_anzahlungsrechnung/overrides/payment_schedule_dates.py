import frappe
from frappe import _
from frappe.utils import getdate


def validate_payment_schedule_dates_allow_duplicate_due_dates(self):
	"""Same as ERPNext AccountsController.validate_payment_schedule_dates without duplicate due_date rows."""
	for d in self.get("payment_schedule"):
		d.validate_from_to_dates("discount_date", "due_date")
		if self.doctype in ["Sales Order", "Quotation"] and getdate(d.due_date) < getdate(
			self.transaction_date
		):
			frappe.throw(
				_("Row {0}: Due Date in the Payment Terms table cannot be before Posting Date").format(d.idx)
			)
