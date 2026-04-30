from collections.abc import Iterable

import frappe


def execute():
	remove_custom_fields_if_exist(
		"Sales Invoice",
		("custom_summarize_positions", "custom_down_payment_invoice_description"),
	)


def remove_custom_fields_if_exist(
	doctype: str,
	fieldnames: Iterable[str],
	*,
	sync_schema: bool = True,
) -> list[str]:
	"""Delete **Custom Field** rows for ``fieldnames`` on ``doctype`` if they exist.

	Use when fields were removed from ``get_custom_fields()`` — ``create_custom_fields`` does not
	remove existing **Custom Field** documents or DB columns.

	From a new patch module::

	                                from erpnext_anzahlungsrechnung.patches.patch_helpers import (
	                                    remove_custom_fields_if_exist,
	                                )


	                                def execute():
	                                    remove_custom_fields_if_exist(
	                                        "My DocType", ("old_field", "other_field")
	                                    )

	Or as a one-off from ``patches.txt`` (single line)::

		execute:from erpnext_anzahlungsrechnung.patches.patch_helpers import remove_custom_fields_if_exist; remove_custom_fields_if_exist("Sales Order", ["legacy_x"])

	:param doctype: Target DocType (``dt`` on **Custom Field**).
	:param fieldnames: Fieldnames to drop if a matching **Custom Field** exists.
	:param sync_schema: If True (default), run ``frappe.db.updatedb`` for ``doctype`` after any delete.
	:return: List of deleted **Custom Field** ``name`` values (empty if nothing removed).
	"""
	deleted: list[str] = []
	for fieldname in fieldnames:
		name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname}, "name")
		if name:
			frappe.delete_doc("Custom Field", name, force=True)
			deleted.append(name)
	if sync_schema and deleted:
		frappe.db.updatedb(doctype)
	return deleted
