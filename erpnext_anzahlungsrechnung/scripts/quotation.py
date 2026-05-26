import frappe
from erpnext.selling.doctype.quotation.quotation import make_sales_order as erpnext_make_sales_order


@frappe.whitelist()
def make_sales_order(source_name: str, target_doc=None, args=None):
	doc = erpnext_make_sales_order(source_name, target_doc=target_doc, args=args)
	if doc.meta.get_field("custom_invoice_type") and doc.custom_invoice_type == "Invoice":
		if frappe.db.count("Payment Schedule", {"parent": source_name, "parenttype": "Quotation"}) > 1:
			doc.custom_invoice_type = "Down Payment Invoice"
	return doc
