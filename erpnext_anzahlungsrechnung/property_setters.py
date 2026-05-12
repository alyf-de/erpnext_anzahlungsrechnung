_PAYMENT_SCHEDULE_FIELD_DESCRIPTION = "Last line: final invoice. Earlier lines: down-payment portions."


def get_property_setters():
	"""
	Property Setters for ERPNext Anzahlungsrechnung (updates are triggered by patches.txt.)
	DocTypes are ordered alphabetically.
	"""
	return {
		"Company": [
			("book_advance_payments_in_separate_party_account", "default", "0"),
			("book_advance_payments_in_separate_party_account", "hidden", "1"),
		],
		"Quotation": [
			("payment_schedule", "description", _PAYMENT_SCHEDULE_FIELD_DESCRIPTION),
		],
		"Sales Order": [
			("payment_schedule", "description", _PAYMENT_SCHEDULE_FIELD_DESCRIPTION),
		],
	}
