// Copyright (c) 2026, ALYF GmbH and contributors
// For license information, please see license.txt

function base_total(frm) {
	return flt(frm.doc.total_sales_order_amount);
}

function sync_amount_from_percentage(frm) {
	const total = base_total(frm);
	const pct = flt(frm.doc.down_payment_percentage);
	if (total) {
		frm._syncing_down_payment_fields = true;
		frm.set_value(
			"down_payment_amount",
			flt((pct * total) / 100, precision("down_payment_amount", frm.doc))
		);
		frm._syncing_down_payment_fields = false;
	}
}

function sync_percentage_from_amount(frm) {
	const total = base_total(frm);
	const amt = flt(frm.doc.down_payment_amount);
	if (total) {
		frm._syncing_down_payment_fields = true;
		frm.set_value(
			"down_payment_percentage",
			flt((amt / total) * 100, precision("down_payment_percentage", frm.doc))
		);
		frm._syncing_down_payment_fields = false;
	}
}

function resync_after_total_change(frm) {
	if (frm._syncing_down_payment_fields) {
		return;
	}
	const total = base_total(frm);
	if (!total) {
		return;
	}
	if (flt(frm.doc.down_payment_percentage)) {
		sync_amount_from_percentage(frm);
	} else if (flt(frm.doc.down_payment_amount)) {
		sync_percentage_from_amount(frm);
	}
}

frappe.ui.form.on("Down Payment Invoice", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.docstatus == 0 && frm.doc.sales_order) {
			frm.add_custom_button(__("Refresh"), () => {
				frappe.call({
					doc: frm.doc,
					method: "refresh_totals_from_sales_order",
					freeze: true,
					freeze_message: __("Updating from Sales Order..."),
					callback: () => frm.reload_doc(),
				});
			});
		}

		if (frm.doc.docstatus === 1 && frappe.model.can_create("Payment Entry")) {
			frm.add_custom_button(
				__("Payment"),
				() => make_down_payment_invoice_payment_entry(frm),
				__("Create")
			);
		}
	},

	down_payment_amount(frm) {
		if (frm._syncing_down_payment_fields) {
			return;
		}
		sync_percentage_from_amount(frm);
	},

	down_payment_percentage(frm) {
		if (frm._syncing_down_payment_fields) {
			return;
		}
		sync_amount_from_percentage(frm);
	},

	sales_order(frm) {
		frappe.after_ajax(() => resync_after_total_change(frm));
	},

	total_sales_order_amount(frm) {
		resync_after_total_change(frm);
	},
});

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
