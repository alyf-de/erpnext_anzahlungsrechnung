# Intro

We use following formation:

- DocType names: bold
- Fieldnames: auto
- Fieldlabels: italic
- Values: with quotation marks

Example: In the **Sales Invoice** we set the default for *Incoterm* (`incoterm`) to "EXW".

# Overview

This apps provides a new
It consists of following main features:

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
| -> Automization for Step 1                        | 1130            |          |                |               |                |               |                               |                              | 1130                        |
| Step 2: **Payment Entry** against **Sales Order** | -565            | 565      |                |               |                |               |                               |                              |                             |
| -> Automation for Step 2                          |                 |          |                |               | 17.5           | 47.5          | 250                           | 250                          | -565                        |
| Step 3: Final Invoice                             | 2260            |          | 1000           | 1000          | 190            | 70            |                               |                              |                             |
| -> Automization for Step 3                        |                 |          |                |               | -17.5          | -47.5         | -250                          | -250                         | 565                         |
| -> Automization for Step 4                        | -1130           |          |                |               |                |               |                               |                              | -1130                       |


### Automations

#### Step 1

Create a **Journal Entry** that is linked to the **Down Payment Entry**.
It should create debit to the customer via **Sales Order** `debit_to` (same field name as **Sales Invoice**) when _Invoice Type_ is Down Payment Invoice.
This amount is transfered to the account *Requested Payments Account* (which is set in **Company**).
Summary: After Step 1 only one simple **Journal Entry** was creted, since the **Down Payment Invoice** does not touch more that we need to "cleanse".

#### Step 2

Allocate paid amount proportionally and as net sum to *Received Down Payment Account* (see child table in **Company**)
Therefere, we need an *Income Account* in **Sales Order Item**. These shouldn't differ from the ones in Final **Sales Invoice**.
We also allocate the taxes respectively and reduce *Requested Payments Account* (see **Company**).

#### Step 3

We want to create **Journal Entries**, that are basically negative from the **Journal Entries** that were done in Step 1 and 2.
This way we neutralized our interim bookings.

Note: We don't need a validation of matching `debit_to` account between **Sales Order** and **Sales Invoice**,
because the debit_to accounts of the previous automations will be used anyway – since we just return these previous **Stock Entries**.

## Print Formats

...

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

