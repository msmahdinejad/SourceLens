export function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

export function asArray(value) {
  return Array.isArray(value) ? value : [];
}

export function cleanText(value, fallback = "") {
  if (value == null) return fallback;
  return String(value).replace(/\s+/g, " ").trim() || fallback;
}

export function truncate(value, maximum) {
  const text = cleanText(value);
  return text.length <= maximum
    ? text
    : `${text.slice(0, Math.max(1, maximum - 1)).trim()}…`;
}

export function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function isHex(value) {
  return /^[0-9a-fA-F]{6}$/.test(String(value || "").replace(/^#/, ""));
}

export function hex(value, fallback) {
  return isHex(value) ? String(value).replace(/^#/, "").toUpperCase() : fallback;
}

export function withHash(value) {
  return `#${String(value).replace(/^#/, "")}`;
}
