/** Plain-language descriptions of failed health checks, for people who are not engineers. */

type Failure = { check: string; reason: string; expected?: unknown; actual?: unknown };
export type PlainRow = { label: string; expected: string; actual: string };
export type PlainFailure = { title: string; explanation: string; rows: PlainRow[] };

const CHECKS: Record<string, { title: string; explanation: string; money?: boolean }> = {
  // NovaMart
  quantity_line_totals: {
    title: "Prices are wrong when buying more than one",
    explanation: "Buying several of the same item should cost the item price times how many you buy.",
    money: true,
  },
  promotion_discounts: {
    title: "Promo codes give the wrong discount",
    explanation: "Each promo code should take off exactly what it advertises.",
    money: true,
  },
  checkout_quote_totals: {
    title: "Checkout totals don't add up",
    explanation: "A sample cart's total at checkout doesn't match what it should cost.",
    money: true,
  },
  inventory_oversell_guard: {
    title: "The shop allows ordering more than is in stock",
    explanation: "Customers should only be able to buy what is actually available.",
  },
  catalog_integrity: { title: "The product list has problems", explanation: "Some products are missing or have bad details." },
  order_store_access: { title: "Orders can't be saved or read", explanation: "The shop can't reach the place it keeps orders." },
  customer_flow: { title: "A customer feature is switched off", explanation: "Part of the shop is unavailable to customers." },
  // NovaConnect
  database_reachable: { title: "Messages can't be stored", explanation: "The app can't reach the place it keeps messages." },
  send_and_receive: { title: "Messages aren't getting through", explanation: "A test message was sent but didn't arrive." },
  idempotent_retry: {
    title: "Messages can arrive twice",
    explanation: "If a phone resends a message after a hiccup, it should still appear only once.",
  },
  delivery_status: {
    title: "“Delivered” shows up too early",
    explanation: "A message should only say delivered once the other person has actually received it.",
  },
  message_ordering: { title: "Messages show in the wrong order", explanation: "Chats should list messages oldest to newest." },
  conversation_isolation: {
    title: "Messages can leak between chats",
    explanation: "A message sent in one chat should never appear in another.",
  },
  // Both
  probe_has_no_side_effects: {
    title: "The health check changed real data",
    explanation: "Checking the app's health should never change anything.",
  },
};

const dollars = (cents: number) => `$${(cents / 100).toFixed(2)}`;

function words(key: string): string {
  const text = key.replace(/_cents$/, "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Probe keys like "2499x3" or "WELCOME10@20000(below_min)" in everyday words. */
function label(key: string, money: boolean): string {
  const qty = /^(\d+)x(\d+)$/.exec(key);
  if (money && qty) return `${qty[2]} × ${dollars(Number(qty[1]))}`;
  const promo = /^([A-Z0-9]+)@(\d+)(?:\((.+)\))?$/.exec(key);
  if (money && promo) return `${promo[1]} on a ${dollars(Number(promo[2]))} order${promo[3] ? " (below the minimum)" : ""}`;
  return words(key);
}

function value(v: unknown, money: boolean, key = ""): string {
  if (v === null || v === undefined) return "nothing";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return money && key !== "item_count" ? dollars(v) : String(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.length === 0 ? "none" : v.map((x) => value(x, money)).join(", ");
  return JSON.stringify(v);
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function plainFailure(f: Failure): PlainFailure {
  const known = CHECKS[f.check];
  const money = Boolean(known?.money);
  let rows: PlainRow[] = [];
  if (isRecord(f.expected) && isRecord(f.actual)) {
    const actual = f.actual;
    rows = Object.entries(f.expected)
      .filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(actual[k]))
      .map(([k, v]) => ({ label: label(k, money), expected: value(v, money, k), actual: value(actual[k], money, k) }));
  } else if (f.expected !== undefined || f.actual !== undefined) {
    rows = [{ label: "Result", expected: value(f.expected, money), actual: value(f.actual, money) }];
  }
  return {
    title: known?.title ?? words(f.check),
    explanation: known?.explanation ?? f.reason,
    rows,
  };
}
