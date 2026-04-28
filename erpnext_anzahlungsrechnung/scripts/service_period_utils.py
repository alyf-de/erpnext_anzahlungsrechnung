import frappe
from frappe import _
from frappe.utils import getdate


def get_item_service_period_bounds(items):
	from_dates = []
	to_dates = []

	for item in items or []:
		if item.custom_service_period_from:
			from_dates.append(getdate(item.custom_service_period_from))
		if item.custom_service_period_to:
			to_dates.append(getdate(item.custom_service_period_to))

	item_from_date = min(from_dates) if from_dates else None
	item_to_date = max(to_dates) if to_dates else None
	return item_from_date, item_to_date


def sync_standard_period_fields_from_custom(doc):
	item_from_date, item_to_date = get_item_service_period_bounds(doc.items)

	if doc.custom_service_period_from:
		doc.from_date = doc.custom_service_period_from
	elif item_from_date and not doc.from_date:
		doc.from_date = item_from_date

	if doc.custom_service_period_to:
		doc.to_date = doc.custom_service_period_to
	elif item_to_date and not doc.to_date:
		doc.to_date = item_to_date


def validate_service_period_ranges(doc):
	_validate_single_range(
		doc.custom_service_period_from,
		doc.custom_service_period_to,
		_("Invoice service period"),
	)

	for item in doc.items or []:
		_validate_single_range(
			item.custom_service_period_from,
			item.custom_service_period_to,
			_("Item row {0} service period").format(item.idx),
		)


def _validate_single_range(from_date, to_date, context):
	if not from_date or not to_date:
		return

	if getdate(from_date) > getdate(to_date):
		frappe.throw(
			_("{0}: 'From' date cannot be after 'To' date.").format(context)
		)
