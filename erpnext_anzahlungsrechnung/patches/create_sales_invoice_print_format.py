from pathlib import Path

import frappe

PRINT_FORMAT_NAME = "Rechnung (inkl. Anzahlungs- und Schlussrechnung)"
DOWN_PAYMENT_INVOICE_PRINT_FORMAT_NAME = "Anzahlungsrechnung"


def upsert_print_format(name: str, doc_type: str, html: str, css: str):
	data = {
		"doc_type": doc_type,
		"custom_format": 1,
		"print_format_type": "Jinja",
		"print_format_for": "DocType",
		"html": html,
		"css": css,
		"disabled": 0,
	}

	if frappe.db.exists("Print Format", name):
		pf = frappe.get_doc("Print Format", name)
		pf.update(data)
		pf.save(ignore_permissions=True)
	else:
		pf = frappe.get_doc({"doctype": "Print Format", "name": name, **data})
		pf.insert(ignore_permissions=True)


def execute():
	base = Path(frappe.get_app_path("erpnext_anzahlungsrechnung")) / "templates"
	css = (base / "print_style.css").read_text(encoding="utf-8")

	upsert_print_format(
		PRINT_FORMAT_NAME,
		"Sales Invoice",
		(base / "sales_invoice.jinja").read_text(encoding="utf-8"),
		css,
	)
	upsert_print_format(
		DOWN_PAYMENT_INVOICE_PRINT_FORMAT_NAME,
		"Down Payment Invoice",
		(base / "down_payment_invoice.jinja").read_text(encoding="utf-8"),
		css,
	)
