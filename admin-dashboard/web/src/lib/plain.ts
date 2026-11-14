/** Plain-language descriptions of failed health checks, for people who are not engineers. */

type Failure = { check: string; reason: string; expected?: unknown; actual?: unknown };
export type PlainRow = { label: string; expected: string; actual: string };
export type PlainFailure = { title: string; explanation: string; rows: PlainRow[] };

type Shortage = [string, number, number]; // [product, requested, available]
const isShortage = (v: unknown): v is Shortage =>
  Array.isArray(v) && v.length === 3 && typeof v[1] === "number" && typeof v[2] === "number";

/** Orders the shop should block (expected shortages) versus the ones it actually blocked. */
function stockRows(expected: unknown, actual: unknown): PlainRow[] | null {
  if (!Array.isArray(expected) || !Array.isArray(actual)) {
    if (expected === "InsufficientStockError") {
      return [{ label: "Ordering more than is in stock", expected: "Stopped", actual: "Went through" }];
    }
    return null;
  }
  if (!expected.every(isShortage) || !actual.every(isShortage)) return null;
  const key = (s: Shortage) => s.join("|");
  const blocked = new Set(actual.map(key));
  const shouldBlock = new Set(expected.map(key));
  return [
    ...expected.filter((s) => !blocked.has(key(s))).map(([, want, have]) => ({
      label: `Ordering ${want} when only ${have} ${have === 1 ? "is" : "are"} in stock`, expected: "Blocked", actual: "Allowed",
    })),
    ...actual.filter((s) => !shouldBlock.has(key(s))).map(([, want, have]) => ({
      label: `Ordering ${want} when ${have} ${have === 1 ? "is" : "are"} in stock`, expected: "Allowed", actual: "Blocked",
    })),
  ];
}

const CHECKS: Record<string, {
  title: string;
  explanation: string;
  money?: boolean;
  rows?: (expected: unknown, actual: unknown) => PlainRow[] | null;
}> = {
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
    rows: stockRows,
  },
  catalog_integrity: { title: "The product list has problems", explanation: "Some products are missing or have bad details." },
  product_images: {
    title: "Product images aren't loading",
    explanation: "The shop asks for its product photos at a web address that doesn't exist, so shoppers see empty boxes.",
  },
  stock_levels: {
    title: "Everything shows as out of stock",
    explanation: "Products that are in the warehouse show 0 left, so shoppers can't add them to their cart.",
  },
  payment_provider: {
    title: "Payments are failing because Stripe is down",
    explanation: "Stripe, the company that processes NovaMart's card payments, isn't responding. This is their outage, not a bug in our code, so there is nothing for the AI to fix. Customers' cards are not charged.",
  },
  order_store_access: { title: "Orders can't be saved or read", explanation: "The shop can't reach the place it keeps orders." },
  customer_flow: { title: "A customer feature is switched off", explanation: "Part of the shop is unavailable to customers." },
  // NovaConnect
  database_reachable: { title: "Messages can't be stored", explanation: "The app can't reach the place it keeps messages." },
  send_and_receive: {
    title: "Messages aren't getting through",
    explanation: "A test message is sent from one person to another. This shows which part broke: sending, receiving, or both.",
  },
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
  const text = key.replace(/_cents$/, "").replace(/_/g, " ").replace(/\bapi\b/g, "API");
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
  const custom = known?.rows?.(f.expected, f.actual);
  return {
    title: known?.title ?? words(f.check),
    explanation: known?.explanation ?? f.reason,
    rows: custom && custom.length > 0 ? custom : genericRows(f, money),
  };
}

function genericRows(f: Failure, money: boolean): PlainRow[] {
  if (isRecord(f.expected) && isRecord(f.actual)) {
    const actual = f.actual;
    return Object.entries(f.expected)
      .filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(actual[k]))
      .map(([k, v]) => ({ label: label(k, money), expected: value(v, money, k), actual: value(actual[k], money, k) }));
  }
  if (f.expected !== undefined || f.actual !== undefined) {
    return [{ label: "Result", expected: value(f.expected, money), actual: value(f.actual, money) }];
  }
  return [];
}
