/** English strings for Stock Reconciliation (opening stock / physical count). */
export const enReco = {
  reco: {
    res: {
      stock_reconciliations: "Stock Reconciliations",
      stock_reconciliation: "Stock Reconciliation",
    },
    purpose: {
      reconciliation: "Stock Reconciliation",
      opening: "Opening Stock",
    },
    fields: {
      purpose: "Purpose",
      expense_account: "Difference Account",
      difference_amount: "Difference Amount",
      current_qty: "Current Qty",
      current_rate: "Current Rate",
      qty: "Counted Qty",
      rate: "Counted Rate",
      qty_difference: "Qty Difference",
      amount_difference: "Value Difference",
    },
    help: {
      expense_account:
        "Where the stock value difference is booked. Leave blank for the Stock Adjustment account; Opening Stock needs a balance-sheet account such as Temporary Opening.",
    },
    fetch: {
      button: "Fetch items",
      warehouse: "Warehouse to count",
      pick: "Choose a warehouse first.",
      company_first: "Select the company first.",
      none: "No new items with stock in this warehouse.",
      done: "{{count}} items added. Enter the counted quantities.",
      failed: "Could not fetch the items.",
    },
  },
};
