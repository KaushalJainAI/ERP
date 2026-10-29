/**
 * Purchase bill: supplier invoice no / date and the extra charges (freight, hamali /
 * loading) that are added to the stock cost. Merged at the top level under `chg`.
 */
export const enCharges = {
  chg: {
    bill_no: "Supplier Invoice No",
    bill_no_help: "The number printed on the supplier's bill. Required to submit; the accountant needs it in Tally for GST input credit.",
    bill_date: "Supplier Invoice Date",
    table: "Extra charges (freight, hamali, loading)",
    from_template: "Charge type",
    description: "Charge",
    amount: "Amount",
    distribute_by: "Spread by",
    by_amount: "Line amount",
    by_qty: "Quantity (kg)",
    in_bill: "On supplier bill",
    account: "Expense account",
    charges_on_bill: "Charges on the bill",
    charges_off_bill: "Charges paid separately",
    preview_title: "Landed cost per line (bill rate plus its share of the charges)",
    bill_rate: "Bill rate",
    share: "Charges share",
    landed_rate: "Landed rate",
    need_stock: "Charges are spread over stock items: turn on Update Stock and add at least one stock item.",
  },
};
