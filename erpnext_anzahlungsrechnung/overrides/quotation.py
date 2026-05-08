from erpnext.selling.doctype.quotation.quotation import Quotation as ERPQuotation

from erpnext_anzahlungsrechnung.overrides.payment_schedule_dates import (
	validate_payment_schedule_dates_allow_duplicate_due_dates,
)


class Quotation(ERPQuotation):
	def validate_payment_schedule_dates(self):
		validate_payment_schedule_dates_allow_duplicate_due_dates(self)
