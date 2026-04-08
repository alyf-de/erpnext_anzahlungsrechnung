import frappe
from frappe import _
from frappe.utils import flt


def require_company_liability_accounts(company, need_received=False):
	"""Ensure Company has Requested Payments (and optionally Received Prepayments) accounts set."""
	requested, received = get_company_liability_accounts(company)
	if not requested:
		frappe.throw(
			_("Set {0} on Company {1}.").format(_("Requested Payments Account"), frappe.bold(company))
		)
	if need_received and not received:
		frappe.throw(_("Set {0} on Company {1}.").format(_("Received Pre Payments"), frappe.bold(company)))
	return requested, received


def get_company_liability_accounts(company):
	requested, received = frappe.db.get_value(
		"Company",
		company,
		["custom_requested_payments_account", "custom_received_prepayments_account"],
	)
	return requested, received


def default_cost_center(company):
	return frappe.db.get_value("Company", company, "cost_center")


def append_je_row(
	rows,
	account,
	debit,
	credit,
	cost_center,
	project,
	reference_type=None,
	reference_name=None,
	*,
	party_type=None,
	party=None,
):
	"""Append a JE account row. Only set reference_* when both are given — ERPNext validates SI/PI refs (party + receivable/payable account). Receivable/Payable accounts require party_type + party."""
	row = {"account": account}
	if reference_type and reference_name:
		row["reference_type"] = reference_type
		row["reference_name"] = reference_name
	if party_type and party:
		row["party_type"] = party_type
		row["party"] = party
	if flt(debit):
		row["debit_in_account_currency"] = flt(debit)
	elif flt(credit):
		row["credit_in_account_currency"] = flt(credit)
	if cost_center:
		row["cost_center"] = cost_center
	if project:
		row["project"] = project
	rows.append(row)


def merge_je_account_rows(rows):
	keyed = {}
	order = []
	for r in rows:
		side = "debit" if flt(r.get("debit_in_account_currency")) else "credit"
		k = (
			r["account"],
			side,
			r.get("cost_center"),
			r.get("project"),
			r.get("reference_type"),
			r.get("reference_name"),
			r.get("party_type"),
			r.get("party"),
		)
		if k not in keyed:
			keyed[k] = dict(r)
			order.append(k)
		else:
			if side == "debit":
				keyed[k]["debit_in_account_currency"] = flt(keyed[k].get("debit_in_account_currency")) + flt(
					r.get("debit_in_account_currency")
				)
			else:
				keyed[k]["credit_in_account_currency"] = flt(
					keyed[k].get("credit_in_account_currency")
				) + flt(r.get("credit_in_account_currency"))
	return [keyed[k] for k in order]


def insert_and_submit_je(
	company,
	posting_date,
	accounts,
	user_remark,
	title,
	*,
	sales_invoice=None,
	payment_entry=None,
):
	"""Build a Journal Entry with down-payment trace links, insert and submit."""
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": company,
			"posting_date": posting_date,
			"voucher_type": "Journal Entry",
			"user_remark": user_remark,
			"title": title,
			"accounts": accounts,
			"custom_dp_sales_invoice": sales_invoice,
			"custom_dp_payment_entry": payment_entry,
		}
	)
	je.insert()
	je.submit()
	return je
