# Intro

We use the following conventions:

- DocType names: bold
- Fieldnames: auto
- Fieldlabels: italic
- Values: with quotation marks

Example: In the **Sales Invoice** we set the default for *Incoterm* (`incoterm`) to "EXW".

# Overview

This app adds a **Down Payment Invoice** flow on top of ERPNext. It consists of the following main features:

- New DocType: **Down Payment Invoice**
- Adjusted **Print Format** for **Down Payment Invoice** and **Sales Invoice**
- Automatically created **Journal Entry** records that match German regulation for down payments and their matching final invoices
- Adjusted E-Invoices for both **Down Payment Invoice** and **Sales Invoice**.

## Accounting Automation

For the accounting part we need to create additional **Journal Entries** automatically.
To demonstrate what we need, we can look at following example.
This involves:

- One **Down Payment Invoice** (50% of the **Sales Order** amount)
- A partial payment on the **Down Payment Invoice** (only half of the half of the **Sales Order** was paid)
- A Final **Sales Invoice**


|                                                   | Forderungen (S) | Bank (S) | Ertrag 19% (H) | Ertrag 7% (H) | Steuer 19% (H) | Steuer 7% (H) | Erhaltene Anzahlungen 19% (H) | Erhaltene Anzahlungen 7% (H) | Anzahlungsanforderungen (H) |
| ------------------------------------------------- | --------------- | -------- | -------------- | ------------- | -------------- | ------------- | ----------------------------- | ---------------------------- | --------------------------- |
| Step 1: **Down Payment Invoice** is submitted     |                 |          |                |               |                |               |                               |                              |                             |
| Step 2: **Payment Entry** against **Sales Order** | -565            | 565      |                |               |                |               |                               |                              |                             |
| -> Automation for Step 2                          |                 |          |                |               | 47.5           | 17.5          | 250                           | 250                          | -565                        |
| Step 3: Final Invoice                             | 2260            |          | 1000           | 1000          | 190            | 70            |                               |                              |                             |
| -> Automation for Step 3                          |                 |          |                |               | -47.5          | -17.5         | -250                          | -250                         | 565                         |
| Step 4: Full Return                               | -2260           |          | -1000          | -1000         | -190           | -70           |                               |                              |                             |
| -> Automation for Step 4                          |                 |          |                |               | 47.5           | 17.5          | 250                           | 250                          | -565                        |
| Step 5: Final Invoice                             | 2260            |          | 1000           | 1000          | 190            | 70            |                               |                              |                             |
| -> Automation for Step 5                          |                 |          |                |               | -47.5          | -17.5         | -250                          | -250                         | 565                         |


### Automations

#### Step 1

No automation needed.

#### Step 2

Allocate paid amount proportionally and as net sum to *Received Down Payment Account* (see child table in **Company**)
Therefore, we need an *Income Account* in **Sales Order Item**. These shouldn't differ from the ones in Final **Sales Invoice**.
We also allocate the taxes respectively and reduce *Requested Payments Account* (see **Company**).

#### Step 3

We want to create **Journal Entries**, that are basically negative from the **Journal Entries** that were done in Step 2.
This way we neutralized our interim bookings. Note: Step 2 can actually occur several times, which lead to several **Journal Entries**.
All need to be undone by neutralizing **Journal Entries**.

Note: We don't need a validation of matching `debit_to` account between **Sales Order** and **Sales Invoice**,
because the debit_to accounts of the previous automations will be used anyway – since we just reverse these previous **Journal Entries**.

#### Step 4: Returns

This only counts for full returns agains the Sales Order.
Here we create a new **Journal Entry** that undos what was done in previous step 3.

We expect another full final invoice in the next step.

#### Step 5

Same as step 3 in this example.

## Print Formats

### Sales Invoice (Type: Final Invoice)

In the section "prior invoices" we need following columns:

- "Invoice No"
- "Net Amount"
- "Tax Amount"
- "Grand Total"
- "Paid On"
- "Paid Amount"

In the section "prior invoices" we need a table with following structure (see example):

|Invoice No|Invoice Date|Grand Total|Payment Date|Taxes|Paid Amount|
|---|---|---|---|---|---|
|DPI-123|10.05.2026|2260|12.05.2026|Umsatzsteuer 19%: 95<br>Umsatzsteuer 7%: 35|1130|
||||14.05.2026|Umsatzsteuer 19%: 95<br>Umsatzsteuer 7%: 35|1130|
|DPI-127|10.06.2026|2260|15.06.2026|Umsatzsteuer 19%: 190<br>Umsatzsteuer 7%: 70|2260|
|DPI-138|12.07.2026|2260|-|-|-|

How the table can be read:
- Down Payment invoice "DPI-123" was paid with with two sub payments.
- "DPI-127" was fully paid.
- "DPI-138" wasn't paid at all.

Where the data comes from:
- Consider each **Payment Entry** from **Sales Invoice**'s child table in `advances`.
- Assign the **Payment Entries** by date to a **Down Payment Invoice** from **Sales Invoice Down Payment**.
- Get the Taxes from the **Jouarnal Entry** that is linked to the respective **Payment Entry**.

How we solve it technically:
- We provide the data as raw as possible by a jira method (that way our sample table from above can be individually adjusted)
- We provide the example (as above) in our jinja template


## Items and taxes on the Down Payment Invoice

**Down Payment Invoice** maintains its own `items` (`Down Payment Invoice Item`) and `taxes`
(`Sales Taxes and Charges`) child tables, calculated the same way a **Sales Invoice** calculates
its own totals (`net_total`, `total_taxes_and_charges`, `grand_total`). Nothing is derived from the
**Sales Order** at read time any more: creating a Down Payment Invoice from a Sales Order groups the
order's items by `(income_account, item_tax_template)` and writes one item row per group at the
requested percentage of that group's net; the tax rows are copied verbatim from the Sales Order's
own `taxes` table and scale themselves to the smaller net automatically. Tax buckets for print and
for the Journal Entry automation are read straight off the Down Payment Invoice's own
`item_wise_tax_details` (populated for free once that field exists on the document, the same
mechanism the Sales Order itself uses).

Print format (per down payment):

1. Each item row shows that item's own *Description*, *Tax Rate*, *Net Amount*, and *Tax* — a
   natural one-row-per-item table body.

### Example

- Sales Order: 10.000 net at "19%", 1.000 net at "0%".
- A 20% down payment groups into two item rows: 2.000 net at "19%" (tax 380), 200 net at "0%"
  (tax 0).
- Result: The printout shows one row per item — "19%" with net 2.000, then "0%" with net 200.

The item table uses *Description*, *Tax Rate*, *Net Amount*, and *Tax* (no serial column). The
footer shows *Net Total*, one line per tax rate with the tax row's own *Description* and its
*Tax* amount, then *Grand Total*.

## Supporting Features

### Create Payments from Down Payment Invoice

In the form view of **Down Payment Invoice** is a button ("Create" > "Payment") that opens a **Payment Entry**.
It works as the button in **Sales Order**. Only difference is, that the paid amount equals the **Down Payment Invoice**'s amount.

## Enforced Limitations on ERPNext

To keep the features of the consistent, we limit some functionalities in ERPNext.
These are:

- In **Company** `book_advance_payments_in_separate_party_account` is deactivated, because we have a more profound booking of advance payments.

## Payment schedule and **Payment Terms Template**

Purpose:
*Payment Schedule* on **Quotation** / **Sales Order** can describe several installments (earlier rows for down payments, last row for the final invoice). Billing can use a row’s *Invoice portion* instead of typing a %, the create-invoice dialog shows schedule context and linked **Down Payment Invoice** *Status*, and the final **Sales Invoice** can take payment terms from the last schedule row. **Quotation** → **Sales Order** can default to down-payment mode when the quotation has more than one schedule line. **Quotation** / **Sales Order** also allow duplicate *Due Date* values on schedule rows where ERPNext would block them, so equal due dates on different installments are possible.

Payment Terms Template override:
ERPNext warns when two template lines share the same payment term + credit metadata; we skip that check so repeated credit windows on separate lines are allowed.
Caveat: the override is site-wide—every **Payment Terms Template** loses that duplicate warning while the app is installed.