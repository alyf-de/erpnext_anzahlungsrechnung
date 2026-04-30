"""One-off and reusable **Company** / global preference fixes (extend `execute` as needed)."""

import frappe


def execute():
	# Set book_advance_payments_in_separate_party_account to 0 for all companies
	companies = frappe.get_all("Company", pluck="name")
	for c in companies:
		company = frappe.get_doc("Company", c)
		company.book_advance_payments_in_separate_party_account = 0
		company.save()
