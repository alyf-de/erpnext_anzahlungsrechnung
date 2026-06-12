import frappe
from frappe import _
from frappe.utils import flt, getdate


def before_print(doc, method, print_settings):
	prepare_invoice_data_according_to_invoice_type(doc)


def prepare_invoice_data_according_to_invoice_type(doc):
	_add_tax_rates_to_items(doc)
	if doc.custom_invoice_type == "Final Invoice":
		if doc.get("custom_down_payments"):
			doc.prior_down_payment_print_rows = build_prior_down_payment_print_rows(doc)
		else:
			doc.prior_down_payment_print_rows = []


def _add_tax_rates_to_items(doc):
	by_item = {}
	for row in doc.get("item_wise_tax_details") or []:
		if flt(row.amount) == 0 or flt(row.taxable_amount) == 0:
			continue
		by_item.setdefault(row.item_row, []).append(flt(row.rate))

	for key in by_item:
		by_item[key] = sorted(set(by_item[key]))

	for item in doc.items:
		rates = list(by_item.get(item.name) or [])
		if not rates:
			rates = [0.0]
		item.tax_rate = rates


def build_prior_down_payment_print_rows(doc):
	"""Allocate Payment Entry advances to down payment rows for print."""
	dp_rows = doc.get("custom_down_payments") or []
	if not dp_rows:
		return []

	precision = doc.precision("grand_total")
	totals = [
		{
			"invoice_no": d.invoice_no,
			"net_total": flt(d.net_total, precision),
			"tax_amount": flt(d.tax_amount, precision),
			"grand_total": flt(d.grand_total, precision),
		}
		for d in dp_rows
	]
	dpi_dates = [getdate(d.date) for d in dp_rows]
	payments = _collect_advance_payments(doc, precision)
	pe_meta = _load_payment_entry_dates([p["pe"] for p in payments])
	for payment in payments:
		meta = pe_meta.get(payment["pe"], {})
		payment["payment_date"] = meta.get("payment_date")
		payment["posting_date"] = meta.get("posting_date")

	payments.sort(key=lambda p: (p["payment_date"] or "", p["posting_date"] or "", p["pe"]))
	return _build_allocated_print_rows(totals, dpi_dates, payments, precision)


def _collect_advance_payments(doc, precision):
	payments = []
	for adv in doc.get("advances") or []:
		if adv.reference_type != "Payment Entry" or not adv.reference_name:
			continue
		amt = flt(adv.allocated_amount, precision)
		if amt <= 0:
			continue
		payments.append({"pe": adv.reference_name, "amount": amt})
	return payments


def _load_payment_entry_dates(pe_names):
	pe_names = list({name for name in pe_names if name})
	meta = {}
	if not pe_names:
		return meta
	for row in frappe.get_all(
		"Payment Entry",
		filters={"name": ["in", pe_names]},
		fields=["name", "reference_date", "posting_date"],
	):
		payment_date = getdate(row.reference_date) if row.reference_date else getdate(row.posting_date)
		meta[row.name] = {
			"reference_date": getdate(row.reference_date) if row.reference_date else None,
			"posting_date": getdate(row.posting_date) if row.posting_date else None,
			"payment_date": payment_date,
		}
	return meta


def _build_allocated_print_rows(totals, dpi_dates, payments, precision):
	tol = 10 ** (-precision) if precision else 0.01
	n = len(totals)
	remaining = [flt(t["grand_total"], precision) for t in totals]
	emitted = [0] * n
	out = []

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
		paid_on = pay["payment_date"]
		while amt_left > tol:
			idx = _target_dpi_index(dpi_dates, paid_on, remaining, tol, n)
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


def _target_dpi_index(dpi_dates, payment_date, remaining, tol, n):
	"""Latest down payment invoice (by date, then table order) due on or before the payment."""
	if not payment_date:
		return None
	payment_date = getdate(payment_date)
	eligible = [i for i in range(n) if dpi_dates[i] <= payment_date and remaining[i] > tol]
	if not eligible:
		return None
	max_date = max(dpi_dates[i] for i in eligible)
	at_max_date = [i for i in eligible if dpi_dates[i] == max_date]
	return max(at_max_date)
