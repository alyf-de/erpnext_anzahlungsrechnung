def get_custom_fields():
	"""
	Custom Fields for ERPNext Anzahlungsrechnung (updates are triggered by patches.txt.)
	DocTypes are ordered alphabetically.
	"""
	custom_fields = {
		"Sales Invoice": [
			{
				"fieldname": "custom_invoice_type",
				"label": "Invoice Type",
				"fieldtype": "Select",
				"insert_after": "posting_date",
				"options": "Invoice\nPartial Invoice\nFinal Invoice",
				"default": "Invoice",
				"reqd": 1,
				"read_only_depends_on": "eval: !doc.__islocal;",
			},
		],
		"Sales Order": [
			{
				"fieldname": "custom_invoice_type",
				"label": "Invoice Type",
				"fieldtype": "Select",
				"insert_after": "transaction_date",
				"options": "Invoice\nPartial Invoice",
				"default": "Invoice",
				"reqd": 1,
				"allow_on_submit": 1,
				"read_only_depends_on": "eval: doc.per_billed > 0",
			},
		],
	}
	return custom_fields
