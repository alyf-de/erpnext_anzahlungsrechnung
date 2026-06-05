# Returns

This app handles a separate workflow for **Sales Invoices** of *Invoice Type* "Final Invoice" and for **Down Payment Invoices**.

## Down Payment Invoices

Will be implemented later.


## (Final-) Sales Invoices

We differ between 4 different cases, that are determined by two dimensions:
- Update Sales Order: Handled by checkbox `update_billed_amount_in_sales_order`
- Full Return: Determined by the amount that is returned (100% or less?).

### Case 1: Update Sales Order, partial return

Business context: None

This leads to an error.
Reason: In that case we would have to create another invoice which does not cover the full amount. This is not the idea of a "Final Invoice".

### Case 2: Update Sales Order, full return

Business context (example):
Reason could be an error in the invoice address.

This means the old **Sales Invoice** is fully returned and another full final invoice is expected.

Important automations for that (on submit of returning **Sales Invoice**):
- **Payment Entries** need to be unreconciled from the previous final invoice and need to be reconciled back to the **Sales Order** again.
- Create a new **Journal Entry** that basically returns what the automatic **Journal Entry** of the previous **Sales Invoice** did.

### Case 3: Don't update Sales Order, partial return

Business context (example):
This can be interpreted as some sort of discount due to bad service, delay or bad quality goods.

Here we don't expect any further **Sales Invoices**.
Good thing is, that we don't need to automate anything for that.

### Case 4: Don't update Sales Order, full return

Business context (example):
In this case the service/goods probably was not supplied, since we fully return the invoice and don't expect new invoices.

There is no automation needed here. We just want to add msgprint saying: You need to pay back possible advance payments.
