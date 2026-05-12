"""Remove legacy custom **DocType Link** rows **Sales Order** → **Down Payment Invoice**.

Connections are provided by `override_doctype_dashboards` (`sales_order_dashboard`); custom
**DocType Link** rows duplicated in **Customize Form** because of Frappe **Meta** behaviour.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Down Payment Invoice"):
		return

	names = frappe.get_all(
		"DocType Link",
		filters={
			"parent": "Sales Order",
			"parenttype": "DocType",
			"parentfield": "links",
			"link_doctype": "Down Payment Invoice",
			"link_fieldname": "sales_order",
			"custom": 1,
		},
		pluck="name",
	)
	for name in names:
		frappe.delete_doc("DocType Link", name, ignore_permissions=True)

	if names:
		frappe.clear_cache(doctype="Sales Order")
