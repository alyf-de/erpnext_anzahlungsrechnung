import frappe
from frappe import _
from frappe.utils import flt


def before_print(doc, method, print_settings):
	prepare_invoice_data_according_to_invoice_type(doc)


def prepare_invoice_data_according_to_invoice_type(doc):
	if doc.custom_invoice_type == "Invoice":
		_add_tax_rates_to_items(doc)
	if doc.custom_invoice_type == "Final Invoice":
		_prepare_final_invoice_data(doc)
		_add_tax_rates_to_items(doc)
		if doc.get("custom_down_payments"):
			doc.prior_down_payment_print_rows = build_prior_down_payment_print_rows(doc)
		else:
			doc.prior_down_payment_print_rows = []


def _prepare_final_invoice_data(doc):
	# we can assume that all positions are linked to the same sales order (see validations in sales_invoice.py)
	sales_order = frappe.get_doc("Sales Order", doc.items[0].sales_order)
	doc.set("items", sales_order.items)
	doc.set("taxes", sales_order.taxes)
	doc.set("item_wise_tax_details", sales_order.item_wise_tax_details)
	doc.total = sales_order.total
	doc.net_total = sales_order.net_total
	doc.grand_total = sales_order.grand_total
	doc.base_total = sales_order.base_total
	doc.base_net_total = sales_order.base_net_total
	doc.base_grand_total = sales_order.base_grand_total


def _add_tax_rates_to_items(doc):
	by_item = {}
	for row in doc.get("item_wise_tax_details") or []:
		if flt(_row_value(row, "taxable_amount")) == 0:
			continue
		by_item.setdefault(_row_value(row, "item_row"), []).append(flt(_row_value(row, "rate")))

	for key in by_item:
		rates = sorted(set(by_item[key]))
		non_zero_rates = [rate for rate in rates if abs(rate) > 1e-9]
		by_item[key] = non_zero_rates or rates

	document_has_effective_tax = _document_has_effective_tax(doc)

	for item in doc.items:
		rates = list(by_item.get(item.name) or [])
		if not rates and document_has_effective_tax:
			rates = _tax_rates_from_item_tax_rate(getattr(item, "item_tax_rate", None))
		if not rates and not document_has_effective_tax:
			rates = [0.0]
		item.tax_rate = rates if rates else None


def _tax_rates_from_item_tax_rate(item_tax_rate):
	if not item_tax_rate:
		return []
	data = frappe.parse_json(item_tax_rate) if isinstance(item_tax_rate, str) else item_tax_rate
	if not data:
		return []
	rates = sorted({flt(v) for v in data.values()})
	non_zero_rates = [rate for rate in rates if abs(rate) > 1e-9]
	return non_zero_rates or rates


def _document_has_effective_tax(doc):
	for row in doc.get("item_wise_tax_details") or []:
		if abs(flt(_row_value(row, "amount"))) > 1e-9:
			return True

	for row in doc.get("taxes") or []:
		if abs(flt(_row_value(row, "tax_amount"))) > 1e-9:
			return True
		if abs(flt(_row_value(row, "base_tax_amount"))) > 1e-9:
			return True

	return False


def _row_value(row, key):
	if isinstance(row, dict):
		return row.get(key)
	return getattr(row, key, None)


def build_prior_down_payment_print_rows(doc):
	"""FIFO-allocate Payment Entry advances (by posting date) to down payment rows for print."""
	dp_rows = doc.get("custom_down_payments") or []
	if not dp_rows:
		return []

	precision = doc.precision("grand_total")
	tol = 10 ** (-precision) if precision else 0.01

	totals = [
		{
			"invoice_no": d.invoice_no,
			"net_total": flt(d.net_total, precision),
			"tax_amount": flt(d.tax_amount, precision),
			"grand_total": flt(d.grand_total, precision),
		}
		for d in dp_rows
	]
	n = len(dp_rows)
	remaining = [flt(t["grand_total"], precision) for t in totals]
	emitted = [0] * n
	out = []

	payments = []
	for adv in doc.get("advances") or []:
		if adv.reference_type != "Payment Entry" or not adv.reference_name:
			continue
		amt = flt(adv.allocated_amount, precision)
		if amt <= 0:
			continue
		payments.append({"pe": adv.reference_name, "amount": amt})

	pe_names = list({p["pe"] for p in payments})
	posting_by_pe = {}
	if pe_names:
		for row in frappe.get_all(
			"Payment Entry", filters={"name": ["in", pe_names]}, fields=["name", "posting_date"]
		):
			posting_by_pe[row.name] = row.posting_date

	payments.sort(key=lambda p: (posting_by_pe.get(p["pe"]) or "", p["pe"]))

	def append_payment_row(dpi_idx: int, paid_on, paid_amount: float):
		first = emitted[dpi_idx] == 0
		t = totals[dpi_idx]
		out.append(
			{
				"invoice_no": t["invoice_no"],
				"show_invoice_amounts": first,
				"net_total": t["net_total"] if first else None,
				"tax_amount": t["tax_amount"] if first else None,
				"grand_total": t["grand_total"] if first else None,
				"paid_on": paid_on,
				"paid_amount": flt(paid_amount, precision),
			}
		)
		emitted[dpi_idx] += 1

	for pay in payments:
		amt_left = pay["amount"]
		paid_on = posting_by_pe.get(pay["pe"])
		while amt_left > tol and sum(remaining) > tol:
			idx = next((i for i in range(n) if remaining[i] > tol), None)
			if idx is None:
				break
			chunk = min(amt_left, remaining[idx])
			append_payment_row(idx, paid_on, chunk)
			remaining[idx] = flt(remaining[idx] - chunk, precision)
			amt_left = flt(amt_left - chunk, precision)

	for i in range(n):
		if emitted[i] == 0:
			t = totals[i]
			out.append(
				{
					"invoice_no": t["invoice_no"],
					"show_invoice_amounts": True,
					"net_total": t["net_total"],
					"tax_amount": t["tax_amount"],
					"grand_total": t["grand_total"],
					"paid_on": None,
					"paid_amount": None,
				}
			)

	return out
