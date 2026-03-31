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
				"options": "Invoice\nDown Payment Invoice\nFinal Invoice",
				"default": "Invoice",
				"reqd": 1,
				"read_only_depends_on": "eval: !doc.__islocal;",
			},
			{
				"fieldname": "custom_summarize_positions",
				"label": "Summarize Positions",
				"fieldtype": "Check",
				"insert_after": "items",
				"depends_on": "eval: doc.custom_invoice_type == 'Down Payment Invoice'",
			},
			{
				"fieldname": "custom_down_payment_invoice_description",
				"label": "Down Payment Invoice Description",
				"fieldtype": "Text Editor",
				"insert_after": "custom_summarize_positions",
				"depends_on": "eval: doc.custom_invoice_type == 'Down Payment Invoice' && doc.custom_summarize_positions",
				"mandatory_depends_on": "eval: doc.custom_invoice_type == 'Down Payment Invoice' && doc.custom_summarize_positions",
			},
			{
				"fieldname": "custom_down_payments",
				"label": "Down Payments",
				"fieldtype": "Table",
				"insert_after": "custom_down_payment_invoice_description",
				"depends_on": "eval: doc.custom_invoice_type == 'Final Invoice'",
				"options": "Sales Invoice Down Payment",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "custom_outstanding_after_down_payments",
				"label": "Outstanding After Down Payments",
				"fieldtype": "Currency",
				"insert_after": "custom_down_payments",
				"depends_on": "eval: doc.custom_invoice_type == 'Final Invoice'",
				"read_only": 1,
				"no_copy": 1,
			},
		],
		"Sales Order": [
			{
				"fieldname": "custom_invoice_type",
				"label": "Invoice Type",
				"fieldtype": "Select",
				"insert_after": "transaction_date",
				"options": "Invoice\nDown Payment Invoice",
				"default": "Invoice",
				"reqd": 1,
				"allow_on_submit": 1,
				"read_only_depends_on": "eval: doc.per_billed > 0",
			},
		],
	}
	return custom_fields
