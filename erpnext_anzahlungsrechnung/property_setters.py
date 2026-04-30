import frappe


def get_property_setters():
	"""
	Property Setters for ERPNext Anzahlungsrechnung (updates are triggered by patches.txt.)
	DocTypes are ordered alphabetically.
	"""
	_add_doctype_links()
	return {
		"Company": [
			("book_advance_payments_in_separate_party_account", "default", "0"),
			("book_advance_payments_in_separate_party_account", "hidden", "1"),
		],
	}


def _add_doctype_links():
	"""Custom **DocType Link** so **Sales Order** shows **Down Payment Invoice** under Payment."""
	if not frappe.db.exists("DocType", "Down Payment Invoice"):
		return

	filters = {
		"parent": "Sales Order",
		"parenttype": "DocType",
		"parentfield": "links",
		"link_doctype": "Down Payment Invoice",
		"link_fieldname": "sales_order",
		"custom": 1,
	}
	if frappe.db.exists("DocType Link", filters):
		return

	doc = frappe.new_doc("DocType Link")
	doc.update(
		{
			**filters,
			"group": "Payment",
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.clear_cache(doctype="Sales Order")
