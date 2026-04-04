import frappe
from pathlib import Path

PRINT_FORMAT_NAME = "Rechnung (inkl. Anzahlungs- und Schlussrechnung)"


def execute():
	base = Path(frappe.get_app_path("erpnext_anzahlungsrechnung")) / "templates"
	html = (base / "sales_invoice.jinja").read_text(encoding="utf-8")
	css = (base / "print_style.css").read_text(encoding="utf-8")

	data = {
		"doc_type": "Sales Invoice",
		"custom_format": 1,
		"print_format_type": "Jinja",
		"print_format_for": "DocType",
		"html": html,
		"css": css,
		"disabled": 0,
	}

	if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
		pf = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
		pf.update(data)
		pf.save(ignore_permissions=True)
	else:
		pf = frappe.get_doc({"doctype": "Print Format", "name": PRINT_FORMAT_NAME, **data})
		pf.insert(ignore_permissions=True)
