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
