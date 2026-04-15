import frappe
from frappe import _


def validate(doc, event):
	seen = set()
	for row in doc.get("custom_down_payment_accounts") or []:
		acc = row.income_account
		if not acc:
			continue
		if acc in seen:
			frappe.throw(_("Duplicate income account {0} in Down Payment Accounts.").format(frappe.bold(acc)))
		seen.add(acc)
