# General
This app supports down payment invoices and their matching final invoices with focus on German legislation.

# Features
## Differentiating between invoice types
The **Sales Invoice** now has one of three different invoice types:
- Invoice -> That is a general invoice, that has no special functions. It acts accoding to ERPNext's standard.
- Down Payment Invoice
- Final Invoice

Each **Sales Order** has one of the following invoice flows:
- Invoice -> All its invoices are normal invoices, without the down payment/final invoice flow.
- Down Payment Invoice -> All its invoices follow the down payment/final invoice flow.

According to the *Invoice Type* the **Sales Invoice** are validated, to be consistent.
The validations are done in sales_invoice.py
Most of the validations aim to keep consistency with the legal frame and with the print format (incl. e-invoice).
