# IMPORTANT NOTE
THIS DESCRIBES OUTDATED REQUIREMENTS AND SOLUTIONS.
THE FILE IS ONLY KEPT FOR COMPARISON AND DOCUMENTATION PURPOSES AND WILL BE DELETED SOON.

# General

This app supports down payment invoices and their matching final invoices with focus on German legislation.

# Features

## Differentiating between invoice types

The **Sales Invoice** now has one of three different invoice types:

- Invoice -> That is a general invoice, that has no special functions. It acts according to ERPNext's standard.
- Down Payment Invoice
- Final Invoice

Each **Sales Order** has one of the following invoice flows:

- Invoice -> All its invoices are normal invoices, without the down payment/final invoice flow.
- Down Payment Invoice -> All its invoices follow the down payment/final invoice flow.

According to the *Invoice Type* the **Sales Invoice** are validated, to be consistent.
The validations are done in sales_invoice.py
Most of the validations aim to keep consistency with the legal frame and with the print format (incl. e-invoice).

## Preparing data for Print Format

The **Sales Invoice** has a specific jinja method for transforming the data to be consistent with the rules of the specific invoice type.
Details:

- Invoice: Nothing special
- Down Payment Invoice: Here we manipulate the items table if the checkbox `custom_summarize_positions` is checked.
- Final Invoice: Here we replace the items of the object doc with the **Sales Order Items**. Furthermore, we show the Down Payment Invoices against the **Sales Order**.

## Accounting

Depending on the *Sales Invoice Type*, the accounting of a **Sales Invoice** differ.
General requirements:

- A Down Payment Invoice shall not cause income or taxes.
- A Payment Entry against a Down Payment Invoice shall cause taxes, but not income.
- A Final Invoice shall book income for the previous down payment invoices, that belong to the final invoice.

Example:


| Vorgang / Schritt                 | Forderungen (S) | Bank (S)       | Ertrag (H)     | Steuer (H)   | Erhaltene Anzahlungen (H) | Anzahlungsanforderungen (H) |
| --------------------------------- | --------------- | -------------- | -------------- | ------------ | ------------------------- | --------------------------- |
| **Schritt 1: Anzahlungsrechnung** | 1.190,00 €      |                | 1.000,00 €     | 190,00 €     |                           |                             |
| -> AUTOMATISMUS (A1)              |                 |                | -1.000,00 €    | -190,00 €    |                           | 1.190,00 €                  |
| **Schritt 2: Zahlung**            | -1.190,00 €     | 1.190,00 €     |                |              |                           |                             |
| -> AUTOMATISMUS (A2)              |                 |                |                | 190,00 €     | 1.000,00 €                | -1.190,00 €                 |
| **Schritt 3: Schlussrechnung**    | 2.380,00 €      |                | 2.000,00 €     | 380,00 €     |                           |                             |
| -> AUTOMATISMUS (A3)              |                 |                | 1.000,00 €     |              | -1.000,00 €               |                             |
| **Schritt 4: Zahlung**            | -2.380,00 €     | 2.380,00 €     |                |              |                           |                             |
| ---                               | ---             | ---            | ---            | ---          | ---                       | ---                         |
| **SUMME**                         | **0,00 €**      | **3.570,00 €** | **3.000,00 €** | **570,00 €** | **0,00 €**                | **0,00 €**                  |


### Automation 1

After a Down Payment Invoice is submitted, the income account(s) and tax account(s) shall be immediately neutralized with respective **Journal Entries**.
For this a new custom account field in **Company** is used as an against account. Name of the field: *Requested Payments Account*.

Edge Cases:

- A Down Payment Invoice is returned -> Then the Automation shall run as well, this time it shall be the other way round (such that all acounts are "reset").

### Automation 2

After the **Payment Entry** is made against a Down Payment Invoice, following **Journal Entry** is created:

- Requested Payment Account is reduced.
- Taxes are raised (as in the **Sales Invoice**)
- The `received_down_payment_account` from the matching **Company Down Payment Account** row (configured per income account, and also of root_type Liability) is raised.

### Automation 3

After a final Invoice is submitted, another **Journal Entry** is created (one per Down Payment Invoice):
The received payments shall be shifted to income, as per respective Down Payment Invoice.
This shall also consider down payments that are returned.

### Edge Case: Different Tax Rates

Example: **Sales Order** of net value 2.000€, of which 1.000€ net are taxed with 7%, and 1.000€ with 19% amounting to a grand total of 2.260€


| Bezeichnung                       | Forderungen (S) | Bank (S)       | Ertrag (H)     | Steuer 19% (H) | Steuer 7% (H) | Erhaltene Anzahlungen 19% (H) | Erhaltene Anzahlungen 7% (H) | Anzahlungsanforderungen (H) |
| --------------------------------- | --------------- | -------------- | -------------- | -------------- | ------------- | ----------------------------- | ---------------------------- | --------------------------- |
| **Auftrag (Gesamt)**              |                 |                | **2.000,00 €** | **190,00 €**   | **70,00 €**   |                               |                              |                             |
|                                   |                 |                |                |                |               |                               |                              |                             |
| **Schritt 1: Anzahlungsrechnung** | 1.130,00 €      |                | 1.000,00 €     | 95,00 €        | 35,00 €       |                               |                              |                             |
| -> AUTOMATISMUS (A1)              |                 |                | -1.000,00 €    | -95,00 €       | -35,00 €      |                               |                              | 1.130,00 €                  |
|                                   |                 |                |                |                |               |                               |                              |                             |
| **Schritt 2: Zahlung**            | -1.130,00 €     | 1.130,00 €     |                |                |               |                               |                              |                             |
| -> AUTOMATISMUS (A2)              |                 |                |                | 95,00 €        | 35,00 €       | 500,00 €                      | 500,00 €                     | -1.130,00 €                 |
|                                   |                 |                |                |                |               |                               |                              |                             |
| **Schritt 3: Schlussrechnung**    | 1.130,00 €      |                | 1.000,00 €     | 95,00 €        | 35,00 €       |                               |                              |                             |
| -> AUTOMATISMUS (A3)              |                 |                | 1.000,00 €     |                |               | -500,00 €                     | -500,00 €                    |                             |
|                                   |                 |                |                |                |               |                               |                              |                             |
| **Schritt 4: Zahlung**            | -1.130,00 €     | 1.130,00 €     |                |                |               |                               |                              |                             |
|                                   |                 |                |                |                |               |                               |                              |                             |
| **SUMME**                         | **0,00 €**      | **2.260,00 €** | **2.000,00 €** | **190,00 €**   | **70,00 €**   | **0,00 €**                    | **0,00 €**                   | **0,00 €**                  |


Solution: In automation A2 and A3 we need to differentiate between different tax rates.
Details:

- We need to create a data structure to recognize, which of the following accounts belong together: Income Account, Received Down Payments, Tax Account
-> This is handled with a new child DocType **Company Down Payment Account**. This is added to **Company**.
- Features:
  - Validate **Sales Invoice**: All Income Accounts book to the respective Tax Accounts linked in **Company Down Payment Account**.
  - When a **Payment Entry** is submitted against a Down Payment **Sales Invoice**, the Received Payment Account **Company Down Payment Account** gets the payment allocated, in the same share that the income account has on the net total.

### Edge Case: ...

### Further Edge Cases

- User creates a Journal Entry to make a Down Payment Invoice paid -> For now we don't rule that out, even though we should. It is very unlikely.
- **Implementation note:** Automation **A2** (tax + received prepayments on payment) runs only for submitted **Payment Entry** documents that reference a Down Payment **Sales Invoice**. Clearing a down payment via a standalone **Journal Entry** does not trigger A2; use **Payment Entry** or extend the app with explicit, low-risk detection later.
- ...

## E Invoice

We want to extend the outgoing E Invoices such that they are consistent with the Down Payment and Final Invoice logic.

### Extra Validation

To ensure maximum reliability and legal compliance, this app exclusively supports two e invoice profiles:

- EN 16931
- XRechnung

Reasons:

- Full Compliance: Covers all legal requirements for both B2B (ZUGFeRD Comfort) and B2G (XRechnung) in Germany.
- Advanced Logic: These are the only profiles providing the mandatory semantic structure for down payments and final invoices.
- System Stability: By excluding limited (BASIC) or niche (EXTENDED) profiles, we guarantee tax-compliant data exchange without unnecessary complexity.

Other Invoice Types are not allowed (via backend validation).

# To Implement

- Edge case: Several invoices are reconciled with one **Payment Entry**.
- A clearing **Journal Entry** has to be cancelled or re-done, if a **Payment Entry** or **Sales Invoice** is cancelled.

# Limitations

- Multi-currency is not yet supported. It is avoided by a validation.
- We could add another invoice type "Partial Invoice", that acts as a Teilrechnung/ Teilschlussrechnung
(which is a mixture of a normal invoice (concerning accounting) and a partial invoice (concerning deduction against final invoice amount))

