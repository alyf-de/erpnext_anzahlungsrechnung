from erpnext.selling.doctype.sales_order.sales_order import SalesOrder as ERPSalesOrder

from erpnext_anzahlungsrechnung.overrides.payment_schedule_dates import (
	validate_payment_schedule_dates_allow_duplicate_due_dates,
)


class SalesOrder(ERPSalesOrder):
	def validate_payment_schedule_dates(self):
		validate_payment_schedule_dates_allow_duplicate_due_dates(self)
