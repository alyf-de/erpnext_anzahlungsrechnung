from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

DEFAULT_DOWN_PAYMENT_SUMMARY_ITEM_LABEL = "Down Payment"
DEFAULT_DOWN_PAYMENT_SUMMARY_DESCRIPTION_TEMPLATE = (
	"Es werden {share_percent} % des Gesamtauftragswerts in Rechnung gestellt."
)


def require_requested_payments_account(company):
	"""Ensure Company has Requested Payments account set."""
	if not get_requested_payments_account(company):
		frappe.throw(
			_("Set {0} on Company {1}.").format(_("Requested Payments Account"), frappe.bold(company))
		)


def get_requested_payments_account(company):
	return frappe.db.get_value("Company", company, "custom_requested_payments_account")


def get_down_payment_summary_item_label(company):
	settings = _get_down_payment_summary_text_settings(company)
	return settings["item_label"]


def build_down_payment_summary_description(company, share_percent):
	settings = _get_down_payment_summary_text_settings(company)
	template = settings["description_template"]
	return _render_share_template(template, share_percent)


def _get_down_payment_summary_text_settings(company):
	company_row = frappe.db.get_value(
		"Company",
		company,
		[
			"custom_down_payment_summary_item_label",
			"custom_down_payment_summary_description_template",
		],
		as_dict=True,
	)
	company_row = company_row or {}

	item_label = (company_row.get("custom_down_payment_summary_item_label") or "").strip()
	description_template = (
		(company_row.get("custom_down_payment_summary_description_template") or "").strip()
	)

	return {
		"item_label": item_label or _(DEFAULT_DOWN_PAYMENT_SUMMARY_ITEM_LABEL),
		"description_template": description_template
		or _(DEFAULT_DOWN_PAYMENT_SUMMARY_DESCRIPTION_TEMPLATE),
	}


def _render_share_template(template, share_percent):
	share_value = flt(share_percent, 2)
	share_text = str(share_value)

	if "{share_percent}" in template:
		return template.replace("{share_percent}", share_text)

	if "{0}" in template or "{}" in template:
		try:
			return template.format(share_text)
		except (IndexError, KeyError, ValueError):
			return template

	try:
		return template.format(share_percent=share_text)
	except (IndexError, KeyError, ValueError):
		return template


def get_company_down_payment_map(company):
	"""Map income_account -> row (income_account, tax_rate, tax_account, received_down_payment_account)."""
	rows = frappe.get_all(
		"Company Down Payment Account",
		filters={
			"parent": company,
			"parenttype": "Company",
			"parentfield": "custom_down_payment_accounts",
		},
		fields=["income_account", "tax_rate", "tax_account", "received_down_payment_account"],
	)
	return {r["income_account"]: r for r in rows}


def require_down_payment_accounts_for_income(company, income_accounts):
	"""Ensure every income account in the iterable is configured on Company Down Payment Account."""
	missing = [a for a in income_accounts if a and a not in get_company_down_payment_map(company)]
	if missing:
		frappe.throw(
			_("Configure Down Payment Accounts on Company {0} for the following income accounts: {1}").format(
				frappe.bold(company), ", ".join(frappe.bold(a) for a in sorted(set(missing)))
			)
		)


def aggregate_income_by_account(doc):
	"""Net base amount per income account (same rules as Sales Invoice GL income lines)."""
	enable_discount_accounting = cint(
		frappe.get_single_value("Selling Settings", "enable_discount_accounting")
	)
	totals = defaultdict(float)
	cc = {}
	proj = {}
	for item in doc.get("items") or []:
		if doc.is_internal_transfer():
			continue
		if item.get("is_fixed_asset") and item.get("asset"):
			continue
		income_account = (
			item.income_account
			if (not item.enable_deferred_revenue or doc.is_return)
			else item.deferred_revenue_account
		)
		if not income_account:
			continue
		_net_amt, base_amount = doc.get_amount_and_base_amount(item, enable_discount_accounting)
		if not flt(base_amount, item.precision("base_net_amount")):
			continue
		totals[income_account] += flt(base_amount)
		cc.setdefault(income_account, item.cost_center)
		proj.setdefault(income_account, item.project or doc.get("project"))
	return totals, cc, proj


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
