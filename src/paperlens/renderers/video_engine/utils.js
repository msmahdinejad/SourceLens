import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFile } from "node:child_process";
import { CONFIG } from "./config.js";

let FETCH = globalThis.fetch;
let DISPATCHER;
let networkReady = false;

export async function ensureNetwork() {
  if (networkReady) return;
  networkReady = true;
  if (!CONFIG.proxy) return;
  try {
    const undici = await import("undici");
    FETCH = undici.fetch;
    DISPATCHER = new undici.ProxyAgent(CONFIG.proxy);
  } catch (error) {
    throw new Error(`HTTPS_PROXY تنظیم شده ولی undici نصب نیست. دستور npm install undici را اجرا کنید.\n${error.message}`);
  }
}

export function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
export function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
export function asArray(value) { return Array.isArray(value) ? value : []; }
export function cleanText(value, fallback = "") {
  if (value == null) return fallback;
  return String(value).replace(/\s+/g, " ").trim() || fallback;
}
export function truncate(value, max) {
  const text = cleanText(value);
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1)).trim()}…`;
}
export function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}
export function unique(values) { return [...new Set(values.filter(Boolean))]; }
export function hash(value) { return crypto.createHash("sha1").update(String(value)).digest("hex"); }
export function slugify(value) {
  const text = cleanText(value, "presentation")
    .replace(/[<>:"/\\|?*\u0000-\u001F]/g, "-")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return text.slice(0, 90) || "presentation";
}
export function isHex(value) { return /^[0-9a-fA-F]{6}$/.test(String(value || "").replace(/^#/, "")); }
export function hex(value, fallback) { return isHex(value) ? String(value).replace(/^#/, "").toUpperCase() : fallback; }
export function withHash(value) { return `#${String(value).replace(/^#/, "")}`; }

export async function ensureDirs() {
  await fs.mkdir(CONFIG.outputDir, { recursive: true });
  await fs.mkdir(CONFIG.cacheDir, { recursive: true });
}

export async function fetchWithRetry(url, options = {}, label = "request", attempts = CONFIG.maxRetries) {
  await ensureNetwork();
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), CONFIG.requestTimeoutMs);
    try {
      const response = await FETCH(url, {
        ...options,
        signal: controller.signal,
        ...(DISPATCHER ? { dispatcher: DISPATCHER } : {}),
      });
      clearTimeout(timer);
      if ([408, 429, 500, 502, 503, 504].includes(response.status) && attempt < attempts) {
        const wait = Math.min(12000, 900 * (2 ** (attempt - 1)) + Math.floor(Math.random() * 500));
        console.log(`${label}: HTTP ${response.status}؛ تلاش مجدد ${attempt + 1}/${attempts} پس از ${Math.ceil(wait / 1000)} ثانیه...`);
        await sleep(wait);
        continue;
      }
      return response;
    } catch (error) {
      clearTimeout(timer);
      lastError = error;
      if (attempt >= attempts) break;
      const wait = Math.min(12000, 900 * (2 ** (attempt - 1)) + Math.floor(Math.random() * 500));
      console.log(`${label}: خطای شبکه؛ تلاش مجدد ${attempt + 1}/${attempts}...`);
      await sleep(wait);
    }
  }
  const cause = lastError?.cause;
  const details = [cause?.code, cause?.message, lastError?.message].filter(Boolean).join(" | ");
  throw new Error(`${label}: ارتباط برقرار نشد${details ? ` (${details})` : ""}`);
}

export function extractJson(text) {
  const raw = String(text ?? "").replace(/^\uFEFF/, "").trim();
  const unfenced = raw
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/i, "")
    .trim();

  const candidates = [unfenced];
  const starts = [];
  for (let index = 0; index < unfenced.length; index += 1) {
    if (unfenced[index] === "{" || unfenced[index] === "[") starts.push(index);
  }
  for (const start of starts) {
    const opening = unfenced[start];
    const closing = opening === "{" ? "}" : "]";
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let index = start; index < unfenced.length; index += 1) {
      const char = unfenced[index];
      if (inString) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') inString = false;
        continue;
      }
      if (char === '"') { inString = true; continue; }
      if (char === opening) depth += 1;
      if (char === closing) depth -= 1;
      if (depth === 0) {
        candidates.push(unfenced.slice(start, index + 1));
        break;
      }
    }
  }

  for (const candidate of candidates) {
    try { return JSON.parse(candidate); } catch {}
    const withoutTrailingCommas = candidate.replace(/,\s*([}\]])/g, "$1");
    try { return JSON.parse(withoutTrailingCommas); } catch {}
  }
  throw new Error("پاسخ مدل JSON معتبر نبود.");
}

export function openFile(filePath) {
  if (process.platform === "win32") execFile("cmd", ["/c", "start", "", filePath], { windowsHide: true });
  else if (process.platform === "darwin") execFile("open", [filePath]);
  else execFile("xdg-open", [filePath]);
}

export async function readSource(filePath) {
  if (!filePath) return "";
  const resolved = path.resolve(filePath);
  if (!fsSync.existsSync(resolved)) throw new Error(`فایل منبع پیدا نشد: ${resolved}`);
  const ext = path.extname(resolved).toLowerCase();
  if (![".txt", ".md", ".json", ".csv"].includes(ext)) {
    throw new Error("در این نسخه فایل منبع باید txt، md، json یا csv باشد.");
  }
  const content = await fs.readFile(resolved, "utf8");
  return content.slice(0, 120_000);
}

export function parseArgs(argv) {
  const result = { topic: "", audience: "", slides: 10, source: "", demo: false, open: false, diagnose: false, selfTest: false };
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--topic") result.topic = argv[++i] || "";
    else if (arg === "--audience") result.audience = argv[++i] || "";
    else if (arg === "--slides") result.slides = clamp(Number(argv[++i] || 10), 6, 24);
    else if (arg === "--source") result.source = argv[++i] || "";
    else if (arg === "--demo") result.demo = true;
    else if (arg === "--open") result.open = true;
    else if (arg === "--diagnose") result.diagnose = true;
    else if (arg === "--self-test") result.selfTest = true;
    else if (!arg.startsWith("--") && !["\\", "^"].includes(arg.trim())) positional.push(arg);
  }
  if (!result.topic && positional[0]) result.topic = positional[0];
  if (!result.audience && positional[1]) result.audience = positional[1];
  result.topic = cleanText(result.topic, "آینده کسب‌وکار دیجیتال");
  result.audience = cleanText(result.audience, "مدیران و تصمیم‌گیران");
  return result;
}

