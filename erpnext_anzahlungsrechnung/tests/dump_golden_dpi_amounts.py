# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

"""Pre-refactor golden dump for the items/taxes-table refactor (see PLAN.md / PLAN-AMENDMENTS.md).

Run this **before** any refactor commit lands (the derivation it calls — ``get_income_tax_totals_
for_down_payment_invoice`` / ``get_down_payment_invoice_print_tax_rows`` — is deleted by the
refactor), on a site holding the legacy-shaped ``Down Payment Invoice`` data the migration patch
must handle:

	bench --site SITE execute erpnext_anzahlungsrechnung.tests.dump_golden_dpi_amounts.run \\
		--kwargs '{"output_path": "/path/to/golden_dpi_amounts.json"}'

Writes ``{dpi_name: {down_payment_amount, income_totals, tax_totals, print_tax_rows}}`` for every
Down Payment Invoice (any docstatus) to ``output_path`` as JSON. The post-refactor verification
step re-derives the same shape from the migrated document and diffs it against this file.
"""

import json

import frappe
from frappe.utils import flt

from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice_accounting import (
	get_income_tax_totals_for_down_payment_invoice,
)
from erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_tax_allocation import (
	get_down_payment_invoice_print_tax_rows,
)


def run(output_path: str = "golden_dpi_amounts.json"):
	golden = {}
	rows = frappe.get_all(
		"Down Payment Invoice",
		fields=["name", "docstatus", "sales_order", "company", "down_payment_amount"],
		order_by="creation",
	)
	for row in rows:
		dpi = frappe.get_doc("Down Payment Invoice", row.name)
		income_totals, tax_totals, _income_cc, _income_proj, _tax_cc = (
			get_income_tax_totals_for_down_payment_invoice(dpi)
		)
		print_rows = get_down_payment_invoice_print_tax_rows(dpi.sales_order, dpi.down_payment_amount)
		golden[row.name] = {
			"down_payment_amount": flt(row.down_payment_amount),
			"income_totals": {k: flt(v) for k, v in income_totals.items()},
			"tax_totals": {k: flt(v) for k, v in tax_totals.items()},
			"print_tax_rows": print_rows,
		}

	with open(output_path, "w") as f:
		json.dump(golden, f, indent=2, sort_keys=True, default=str)

	print(f"Wrote golden amounts for {len(golden)} Down Payment Invoice(s) to {output_path}")
	return golden
