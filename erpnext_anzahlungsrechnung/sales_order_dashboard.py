"""Extend **Sales Order** connection dashboard (replaces a custom **DocType Link** row)."""

import frappe
from frappe import _


def extend_sales_order_dashboard(data):
	"""Add **Down Payment Invoice** to the Payment group (same placement as the old DocType Link)."""
	if not frappe.db.exists("DocType", "Down Payment Invoice"):
		return data

	for group in data.get("transactions") or []:
		if _(group.get("label")) == _("Payment"):
			items = group.setdefault("items", [])
			if "Down Payment Invoice" not in items:
				items.append("Down Payment Invoice")
			break

	return data
