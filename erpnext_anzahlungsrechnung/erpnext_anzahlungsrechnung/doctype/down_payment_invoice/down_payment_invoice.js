// Copyright (c) 2026, ALYF GmbH and contributors
// For license information, please see license.txt

// erpnext.sales_common (utils/sales_common.js) ships in erpnext.bundle.js, loaded on every
// Desk page -- no explicit include needed here.
erpnext.sales_common.setup_selling_controller();

const DownPaymentInvoiceController = class DownPaymentInvoiceController extends erpnext.selling
	.SellingController {
	refresh() {
		super.refresh && super.refresh();

		if (this.frm.doc.docstatus === 1 && frappe.model.can_create("Payment Entry")) {
			this.frm.add_custom_button(
				__("Payment"),
				() => make_down_payment_invoice_payment_entry(this.frm),
				__("Create")
			);
		}
	}
};

// nosemgrep: frappe-semgrep-rules.rules.frappe-cur-frm-usage
extend_cscript(cur_frm.cscript, new DownPaymentInvoiceController({ frm: cur_frm }));

function make_down_payment_invoice_payment_entry(frm) {
	frappe.call({
		method: "erpnext_anzahlungsrechnung.erpnext_anzahlungsrechnung.doctype.down_payment_invoice.down_payment_invoice.make_payment_entry",
		args: { source_name: frm.doc.name },
		callback(r) {
			if (!r.message) {
				return;
			}
			const doclist = frappe.model.sync(r.message);
			frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
		},
	});
}
