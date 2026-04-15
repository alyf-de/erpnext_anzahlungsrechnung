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
});

function show_down_payment_sales_invoice_dialog(frm) {
	const per_billed = flt(frm.doc.per_billed);
	// Treat near-zero % as unbilled (avoids float noise blocking the final-invoice option incorrectly).
	const partial_locked = per_billed < 0.01;

	let dialog;

	function refresh_visibility() {
		const partial = partial_locked || dialog.get_value("create_partial");
		const summarize = partial && dialog.get_value("summarize_positions");
		dialog.set_df_property("summarize_positions", "hidden", !partial);
		dialog.set_df_property("share_percent", "hidden", !(partial && summarize));
	}

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
				read_only: partial_locked ? 1 : 0,
				onchange: refresh_visibility,
			},
			{
				fieldname: "summarize_positions",
				fieldtype: "Check",
				label: __("Summarize Positions on Print"),
				default: 1,
				onchange: refresh_visibility,
			},
			{
				fieldname: "share_percent",
				fieldtype: "Float",
				label: __("Bill This Share of Total Order (%)"),
				precision: 2,
			},
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			const create_partial = partial_locked ? 1 : values.create_partial ? 1 : 0;
			const summarize_positions = create_partial ? (values.summarize_positions ? 1 : 0) : 0;
			const share_percent = flt(values.share_percent);

			if (create_partial && summarize_positions) {
				if (share_percent <= 0 || share_percent >= 100) {
					frappe.throw(__("Bill share (%) must be greater than 0 and less than 100."));
				}
			}

			frappe.model.open_mapped_doc({
				method: "erpnext_anzahlungsrechnung.scripts.sales_order.make_sales_invoice_from_sales_order",
				frm: frm,
				args: {
					create_partial,
					summarize_positions,
					share_percent: summarize_positions ? share_percent : 100,
				},
				freeze: true,
				freeze_message: __("Creating Sales Invoice ..."),
			});
			dialog.hide();
		},
	});

	dialog.show();
	refresh_visibility();
}
