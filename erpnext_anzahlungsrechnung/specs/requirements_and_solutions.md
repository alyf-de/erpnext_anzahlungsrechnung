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
| -> Automation for Step 2                          |                 |          |                |               | 17.5           | 47.5          | 250                           | 250                          | -565                        |
| Step 3: Final Invoice                             | 2260            |          | 1000           | 1000          | 190            | 70            |                               |                              |                             |


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

## Print Formats

### Sales Invoice (Type: Final Invoice)
In the section "prior invoices" we need following columns:
- "Invoice No"
- "Net Amount"
- "Tax Amount"
- "Grand Total"
- "Paid On"
- "Paid Amount"

The main challenge with these columns is to assign **Payment Entries** to **Down Payment Invoices**.
Here is a solution for that:

- Consider each **Payment Entry** from **Sales Invoice**'s child table in `advances`.
- Assign the **Payment Entries** by date to a **Down Payment Entry**

#### Edge Cases
NOTE: There can be following three edge cases:
- Paid amount is less than invoiced amount -> No problem!
- Nothing was paid for a certain Down Payment Invoice -> No problem!
- There are two payments against one **Down Payment Invoice** -> Show them in two rows.
- No payment -> Show "-" in "Paid On" and "Paid Amount"

#### Example
- **Down Payment Invoices**
  1) 1000€ on 01.04.
  2) 1000€ on 16.04.
- **Payment Entries**
  1) **Payment Entry** on 02.04. -> Assigned to first invoice
  2) **Payment Entry** on 15.04. -> Assigned to first invoice
  3) **Payment Entry** on 16.04. -> Assigned to second invoice

## Proportional tax on down payments

**Down Payment Invoice** does not maintain its own `taxes` child table. Tax buckets come from the **Sales Order** document’s `item_wise_tax_details` (same logic as item-wise tax on the order or invoice).
The *Down Payment Amount* (`down_payment_amount`) is split across those buckets in proportion to each bucket’s taxable (net) base on the source document. From each share we derive net, rate label, and tax for that rate.

Print format (per down payment):

1. The first item row puts *Position Name* (`position_name`), a line break, then *Position Description* (`position_description`) in the *Description* cell, with the first tax bucket’s *Tax Rate*, *Net Amount*, and *Tax* on the same table row. Further buckets (if any) follow on their own rows with an empty *Description* cell.

### Example

- Source totals 12.900
  - 10.000 net at "19%"
  - 1.000 net at "0%"
- A 20% down payment is 2.580 gross. Allocation:
  - 2.000 net at "19%" (tax 380)
  - 200 net at "0%" (tax 0).
- Result: The printout shows one row: *Position Name* / *Position Description* (stacked in one cell) with first bucket *Tax Rate* "19%" and net 2.000; next row: "0%" and net 200.

The item table uses *Description*, *Tax Rate*, *Net Amount*, and *Tax* (no serial column). The footer shows *Net Total*, one line per bucket with the **Sales Order** `taxes` row *Description* (`description`) when resolved from `item_wise_tax_details` / rate matching (otherwise a short fallback) and the scaled *Tax* amount, then *Grand Total* (*Down Payment Amount*).

```

```

## Supporting Features
### Create Payments from Down Payment Invoice
In the form view of **Down Payment Invoice** is a button ("Create" > "Payment") that opens a **Payment Entry**.
It works as the button in **Sales Order**. Only difference is, that the paid amount equals the **Down Payment Invoice**'s amount.

## Enforced Limitations on ERPNext
To keep the features of the consistent, we limit some functionalities in ERPNext.
These are:
- In **Company** `book_advance_payments_in_separate_party_account` is deactivated, because we have a more profound booking of advance payments.
