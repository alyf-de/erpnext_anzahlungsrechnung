def get_custom_fields():
	"""
	Custom Fields for ERPNext Anzahlungsrechnung (updates are triggered by patches.txt.)
	DocTypes are ordered alphabetically.
	"""
	liability_account_filters = (
		'[["Account", "company", "=", "eval:doc.name"], '
		'["Account", "root_type", "=", "Liability"], '
		'["Account", "is_group", "=", "0"]]'
	)
	custom_fields = {
		"Company": [
			{
				"fieldname": "custom_down_payment_section",
				"label": "Down Payment",
				"fieldtype": "Section Break",
				"insert_after": "default_finance_book",
			},
			{
				"fieldname": "custom_requested_payments_account",
				"label": "Requested Payments Account",
				"fieldtype": "Link",
				"insert_after": "custom_down_payment_section",
				"ignore_user_permissions": 1,
				"options": "Account",
				"link_filters": liability_account_filters,
				"description": "Liability: neutralization and payment clearing for down payment invoices.",
			},
			{
				"fieldname": "custom_down_payment_accounts",
				"label": "Down Payment Accounts",
				"fieldtype": "Table",
				"insert_after": "custom_requested_payments_account",
				"options": "Company Down Payment Account",
				"description": "Down Payment Accounts for different tax rates.",
			},
		],
		"Journal Entry": [
			{
				"fieldname": "custom_dp_sales_invoice",
				"label": "Down Payment Sales Invoice",
				"fieldtype": "Link",
				"insert_after": "company",
				"options": "Sales Invoice",
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "custom_dp_payment_entry",
				"label": "Down Payment Payment Entry",
				"fieldtype": "Link",
				"insert_after": "custom_dp_sales_invoice",
				"options": "Payment Entry",
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "custom_dp_down_payment_invoice",
				"label": "Down Payment Invoice",
				"fieldtype": "Link",
				"insert_after": "custom_dp_payment_entry",
				"options": "Down Payment Invoice",
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
			},
		],
		"Sales Invoice": [
			{
				"fieldname": "custom_invoice_type",
				"label": "Invoice Type",
				"fieldtype": "Select",
				"insert_after": "posting_date",
				"options": "Invoice\nFinal Invoice",
				"default": "Invoice",
				"reqd": 1,
				"read_only_depends_on": "eval: !doc.__islocal;",
			},
			{
				"fieldname": "custom_down_payments",
				"label": "Down Payments",
				"fieldtype": "Table",
				"insert_after": "items",
				"depends_on": "eval: doc.custom_invoice_type == 'Final Invoice'",
				"options": "Sales Invoice Down Payment",
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
			{
				"fieldname": "debit_to",
				"label": "Debit To",
				"fieldtype": "Link",
				"insert_after": "custom_invoice_type",
				"options": "Account",
				"depends_on": "eval: doc.custom_invoice_type == 'Down Payment Invoice'",
				"mandatory_depends_on": "eval: doc.custom_invoice_type == 'Down Payment Invoice'",
				"description": "Receivable account debited on down payment opening journal entries.",
				"ignore_user_permissions": 1,
				"link_filters": (
					'[["Account", "company", "=", "eval:doc.company"], '
					'["Account", "account_type", "=", "Receivable"], '
					'["Account", "is_group", "=", "0"]]'
				),
			},
		],
		"Sales Order Item": [
			{
				"fieldname": "income_account",
				"label": "Income Account",
				"fieldtype": "Link",
				"insert_after": "project",
				"options": "Account",
				"description": "Defaults from Item Default (company); used for down payment allocation and final invoice checks.",
			},
		],
	}
	return custom_fields
