export function parseTs(iso: string): Date {
  return new Date(iso);
}

export function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function timeOf(iso: string) {
  return parseTs(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** Sidebar timestamp: time today, "Yesterday", weekday this week, otherwise a short date. */
export function relativeStamp(iso: string, now = new Date()) {
  const d = parseTs(iso);
  if (sameDay(d, now)) return timeOf(iso);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return "Yesterday";
  const diffDays = (now.getTime() - d.getTime()) / 86_400_000;
  if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function dayLabel(iso: string, now = new Date()) {
  const d = parseTs(iso);
  if (sameDay(d, now)) return "Today";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}

export function firstName(name: string) {
  return name.split(" ")[0];
}

export function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
}

const URL_RE = /(https?:\/\/[^\s<>"')]+)/g;

export function splitLinks(text: string): { text: string; href?: string }[] {
  const parts: { text: string; href?: string }[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_RE)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: text.slice(last, index) });
    parts.push({ text: match[0], href: match[0] });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

export function extractLinks(text: string): string[] {
  return Array.from(text.matchAll(URL_RE), (m) => m[0]);
}

/** True when the text is only 1-3 emoji: rendered large without a bubble. */
export function isJumboEmoji(text: string) {
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 16) return false;
  return /^(\p{Extended_Pictographic}(‍\p{Extended_Pictographic}|️|\p{Emoji_Modifier})*\s*){1,3}$/u.test(trimmed);
}
