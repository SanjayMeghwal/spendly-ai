// Display-only Number() use throughout - amounts stay signed decimal
// strings everywhere they're stored, compared, or sent back to the API;
// this only ever feeds toLocaleString for formatting, never storage or
// arithmetic, so the float precision loss it could introduce never reaches
// anything that matters. See api/transactions.ts's Transaction.amount
// comment for the full reasoning.
export function formatMoney(amount: string): string {
  return Number(amount).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

// Compact form (1.2K, 4.2M) for tight spaces like chart bar-tip labels,
// where a full "$1,234.00" would overflow or force the chart wider.
export function formatCompactMoney(amount: string): string {
  return Number(amount).toLocaleString(undefined, {
    notation: 'compact',
    maximumFractionDigits: 1,
  })
}
