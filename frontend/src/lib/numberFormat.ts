/**
 * Indian Number Formatting Utility (Lakh / Crore)
 * Example: 1234567.89 -> "₹12,34,567.89"
 */
export function formatIndianCurrency(amount: number | string | null | undefined): string {
  if (amount === null || amount === undefined || isNaN(Number(amount))) {
    return "₹0.00";
  }
  const num = Number(amount);
  const formatter = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
  return formatter.format(num);
}

export function formatIndianNumber(val: number | string | null | undefined): string {
  if (val === null || val === undefined || isNaN(Number(val))) {
    return "0";
  }
  const num = Number(val);
  return new Intl.NumberFormat("en-IN").format(num);
}
