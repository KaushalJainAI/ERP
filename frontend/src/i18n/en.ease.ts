/**
 * English strings for the phone-first features: offline banner, bill sent from WhatsApp
 * (share target), bank details on a bill, and the party page. Top-level `ease` namespace,
 * merged into the en bundle in i18n.ts.
 */
export const enEase = {
  ease: {
    offline: "No connection. Your typing is kept.",
    bank: {
      holder: "Account holder",
    },
    share: {
      title: "Bill from WhatsApp",
      reading: "Reading the bill...",
      none: "Nothing was shared. Share a photo or PDF of the bill to this app.",
      saved_note: "The bill could not be read, so it is saved as a note. Open Quick notes to finish it.",
      note_failed: "The file could not be saved. Share it again.",
      signed_out: "Sign in to read this bill. The file is kept until you do.",
      sign_in: "Sign in",
      review: "Review and save",
      home: "Go to home",
      more_notes: "{{count}} more file(s) saved as notes.",
      note_text: "Shared bill",
    },
    party: {
      customer: "Customer",
      supplier: "Supplier",
      phone: "Phone",
      call: "Call",
      no_phone: "No phone number saved.",
      balance: "Balance outstanding",
      open_bills: "Open bills",
      no_open_bills: "No open bills.",
      recent_payments: "Recent payments",
      no_payments: "No payments yet.",
      last_orders: "Last five orders",
      no_orders: "No orders yet.",
      record_money: "Record money",
      not_found: "This party was not found.",
      load_failed: "Could not load this party. Try again.",
      date: "Date",
      amount: "Amount",
      bill: "Bill",
      due: "Due",
      order: "Order",
      total: "Total",
      draft: "Draft",
      submitted: "Submitted",
      cancelled: "Cancelled",
    },
  },
};
