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

function payment_schedule_rows_for_down_payment(frm) {
	return (frm.doc.payment_schedule || []).filter(
		(row) => flt(row.invoice_portion) > 0 && flt(row.invoice_portion) < 100
	);
}

/** Net / due validity from **Payment Schedule** row (`credit_days` or `credit_months`). */
function payment_schedule_validity_display(row) {
	if (!row) {
		return "";
	}
	const based_on = row.due_date_based_on || "";
	if (based_on === "Month(s) after the end of the invoice month") {
		const months = cint(row.credit_months);
		return months ? __("{0} months", [String(months)]) : "";
	}
	const days = cint(row.credit_days);
	return days ? __("{0} days", [String(days)]) : "";
}

function show_down_payment_sales_invoice_dialog(frm) {
	frappe.call({
		method: "frappe.desk.reportview.get_list",
		type: "POST",
		args: {
			doctype: "Down Payment Invoice",
			fields: ["name", "posting_date", "down_payment_percentage", "status"],
			filters: {
				sales_order: frm.doc.name,
				docstatus: ["!=", 2],
			},
			order_by: "posting_date asc",
			limit_page_length: 100,
		},
		callback(r) {
			if (r.exc) {
				frappe.msgprint({
					title: __("Error"),
					indicator: "red",
					message: __(
						"Could not load linked Down Payment Invoices. You need read permission on DocType Down Payment Invoice (ask an administrator), or the server rejected the request."
					),
				});
				return;
			}
			const dpi_rows = Array.isArray(r.message) ? r.message : [];
			try {
				open_down_payment_sales_invoice_dialog(frm, dpi_rows);
			} catch (e) {
				console.error(e);
				frappe.msgprint({
					title: __("Error"),
					indicator: "red",
					message: __("Could not open the billing dialog: {0}", [
						e.message || String(e),
					]),
				});
			}
		},
		error() {
			frappe.msgprint({
				title: __("Error"),
				indicator: "red",
				message: __(
					"Could not load Down Payment Invoices for this Sales Order (network or server error)."
				),
			});
		},
	});
}

function open_down_payment_sales_invoice_dialog(frm, dpi_rows) {
	const dp_info_html = format_down_payment_invoice_dialog_html(dpi_rows);
	const valid_ps_rows = payment_schedule_rows_for_down_payment(frm);
	const has_ps = valid_ps_rows.length > 0;
	const ps_select_options = valid_ps_rows.map((row) => {
		const pct = flt(row.invoice_portion, 2);
		const validity = payment_schedule_validity_display(row);
		return {
			value: row.name,
			label: validity ? `${pct} % — ${validity}` : `${pct} %`,
		};
	});

	const dialog_fields = [
		{
			fieldname: "create_partial",
			fieldtype: "Check",
			label: __("Create Down Payment Invoice"),
			default: 1,
		},
		{
			fieldname: "has_payment_schedule",
			fieldtype: "Int",
			hidden: 1,
			default: has_ps ? 1 : 0,
		},
	];

	if (has_ps) {
		dialog_fields.push({
			fieldname: "set_share_manually",
			fieldtype: "Check",
			label: __("Set Share Manually"),
			default: 0,
			depends_on: "eval: doc.create_partial",
		});
		dialog_fields.push({
			fieldname: "payment_schedule_row",
			fieldtype: "Select",
			label: __("Invoice portion (%)"),
			options: ps_select_options,
			default: valid_ps_rows[0].name,
			depends_on: "eval: doc.create_partial && !doc.set_share_manually",
			mandatory_depends_on: "eval: doc.create_partial && !doc.set_share_manually",
		});
	}

	dialog_fields.push(
		{
			fieldname: "share_percent",
			fieldtype: "Float",
			label: __("Bill This Share of Total Order (%)"),
			precision: 2,
			depends_on:
				"eval: doc.create_partial && (doc.has_payment_schedule == 0 || doc.set_share_manually)",
			mandatory_depends_on:
				"eval: doc.create_partial && (doc.has_payment_schedule == 0 || doc.set_share_manually)",
		},
		{
			fieldname: "dp_info",
			fieldtype: "HTML",
			options: dp_info_html,
		},
		{
			fieldname: "currency",
			fieldtype: "Data",
			hidden: 1,
			default: frm.doc.currency,
		},
		{
			fieldname: "advance_paid_display",
			fieldtype: "Currency",
			label: __("Advance Paid"),
			read_only: 1,
			default: flt(frm.doc.advance_paid),
			options: "currency",
		}
	);

	const dialog = new frappe.ui.Dialog({
		title: __("Create Sales Invoice"),
		fields: dialog_fields,
		primary_action_label: __("Create"),
		primary_action(values) {
			const create_partial = values.create_partial ? 1 : 0;
			const share_percent = flt(values.share_percent);
			const set_share_manually = values.has_payment_schedule
				? values.set_share_manually
					? 1
					: 0
				: 1;
			const payment_schedule_row = values.payment_schedule_row || null;

			if (create_partial) {
				if (set_share_manually) {
					if (share_percent <= 0 || share_percent >= 100) {
						frappe.throw(
							__("Bill share (%) must be greater than 0 and less than 100.")
						);
					}
				} else if (!payment_schedule_row) {
					frappe.throw(__("Select a payment plan row (invoice portion)."));
				}
			}

			frappe.model.open_mapped_doc({
				method: "erpnext_anzahlungsrechnung.scripts.sales_order.make_sales_invoice_from_sales_order",
				frm: frm,
				args: {
					create_partial,
					set_share_manually,
					share_percent,
					payment_schedule_row,
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

function format_down_payment_invoice_dialog_html(rows) {
	const divider = `<hr class="my-2">`;
	const title = `${divider}<p class="small mb-1"><strong>${__(
		"For Information Purposes Only"
	)}</strong></p>`;

	if (!rows || !rows.length) {
		return `${title}<p class="text-muted small">${__(
			"No Down Payment Invoices linked to this Sales Order."
		)}</p><br><br>`;
	}

	const head = `<tr><th>${__("Down Payment Invoice")}</th><th>${__("Date")}</th><th>${__(
		"Percentage"
	)}</th><th>${__("Status")}</th></tr>`;
	const body = rows
		.map((row) => {
			const date_str = row.posting_date
				? frappe.datetime.str_to_user(row.posting_date, false, true)
				: "";
			const pct = flt(row.down_payment_percentage, 2);
			const link = frappe.utils.get_form_link("Down Payment Invoice", row.name, true);
			const status_html = row.status
				? frappe.utils.escape_html(__(row.status, null, "Down Payment Invoice"))
				: "—";
			return `<tr><td>${link}</td><td>${frappe.utils.escape_html(
				date_str
			)}</td><td>${frappe.utils.escape_html(
				String(pct)
			)}&nbsp;%</td><td>${status_html}</td></tr>`;
		})
		.join("");

	return `${title}<table class="table table-bordered small"><thead>${head}</thead><tbody>${body}</tbody></table>`;
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
