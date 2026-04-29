// Copyright (c) 2026, ALYF GmbH and contributors
// License: MIT. See license.txt
frappe.ui.form.on("Sales Order", {
	setup(frm) {
		const cscript = frm.cscript;
		const original_make_sales_invoice = cscript.make_sales_invoice;
		if (!original_make_sales_invoice) {
			return;
		}
		cscript.make_sales_invoice = function () {
			if (frm.doc.custom_invoice_type === "Invoice") {
				return original_make_sales_invoice.apply(this, arguments);
			}
			show_down_payment_sales_invoice_dialog(frm);
		};
	},

	customer(frm) {
		set_debit_to_from_party_account(frm);
	},

	company(frm) {
		set_debit_to_from_party_account(frm);
	},
});

function show_down_payment_sales_invoice_dialog(frm) {
	const per_billed = flt(frm.doc.per_billed);

	let dialog;

	dialog = new frappe.ui.Dialog({
		title: __("Create Sales Invoice"),
		fields: [
			{
				fieldname: "per_billed_info",
				fieldtype: "Data",
				label: __("Already Invoiced (%)"),
				read_only: 1,
				default: String(per_billed),
			},
			{
				fieldname: "create_partial",
				fieldtype: "Check",
				label: __("Create Down Payment Invoice"),
				default: 1,
			},
			{
				fieldname: "share_percent",
				fieldtype: "Float",
				label: __("Bill This Share of Total Order (%)"),
				precision: 2,
				depends_on: "eval: doc.create_partial",
				mandatory_depends_on: "eval: doc.create_partial",
			},
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			const create_partial = values.create_partial ? 1 : 0;
			const share_percent = flt(values.share_percent);

			if (create_partial) {
				if (share_percent <= 0 || share_percent >= 100) {
					frappe.throw(__("Bill share (%) must be greater than 0 and less than 100."));
				}
			}

			frappe.model.open_mapped_doc({
				method: "erpnext_anzahlungsrechnung.scripts.sales_order.make_sales_invoice_from_sales_order",
				frm: frm,
				args: {
					create_partial,
					share_percent,
				},
				freeze: true,
				freeze_message: create_partial
					? __("Creating Down Payment Invoice ...")
					: __("Creating Sales Invoice ..."),
			});
			dialog.hide();
		},
	});

	dialog.show();
}

function set_debit_to_from_party_account(frm) {
	if (!frappe.meta.has_field(frm.doctype, "debit_to")) {
		return;
	}
	if (frm.updating_party_details) {
		return;
	}
	if (frm.doc.__onload && frm.doc.__onload.load_after_mapping) {
		return;
	}
	if (!frm.doc.customer || !frm.doc.company) {
		frm.set_value("debit_to", "");
		return;
	}
	frappe.call({
		method: "erpnext.accounts.party.get_party_account",
		args: {
			party_type: "Customer",
			party: frm.doc.customer,
			company: frm.doc.company,
		},
		callback(r) {
			if (!r.exc && r.message) {
				frm.set_value("debit_to", r.message);
			}
		},
	});
}
