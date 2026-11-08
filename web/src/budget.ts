export function budgetAmount(cents: number | null, currency?: string | null) {
  if (cents == null) return "未填";
  const amount = (cents / 100).toFixed(2);
  return currency ? currency + " " + amount : amount + "（历史记录未注明币种）";
}

export function budgetCents(value: string | number) {
  const text = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw new Error("预算须为非负金额，最多两位小数");
  const [units, fraction = ""] = text.split(".");
  const amount = Number(units + fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(amount)) throw new Error("预算金额过大");
  return amount;
}
