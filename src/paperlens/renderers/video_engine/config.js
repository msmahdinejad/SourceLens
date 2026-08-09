import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export const ROOT = path.resolve(__dirname);
export const PROJECT_ROOT = path.resolve(ROOT, "../../../..");

function loadEnv(filePath) {
  if (!fs.existsSync(filePath)) return;
  for (const raw of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const index = line.indexOf("=");
    if (index <= 0) continue;
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}

loadEnv(path.join(PROJECT_ROOT, ".env"));

function boundedNumber(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  return Math.max(minimum, Math.min(maximum, Number.isFinite(parsed) ? parsed : fallback));
}

export const CONFIG = {
  root: ROOT,
  projectRoot: PROJECT_ROOT,
  outputDir: path.join(PROJECT_ROOT, "outputs"),
  cacheDir: path.resolve(process.env.SOURCELENS_VISUAL_CACHE_DIR?.trim() || path.join(PROJECT_ROOT, "data", "visual-cache", "video")),
  demoDir: path.join(ROOT, "assets", "demo"),
  fontDir: path.resolve(process.env.FONT_DIR?.trim() || path.join(PROJECT_ROOT, "src", "paperlens", "assets", "fonts", "Vazirmatn")),
  fontFile: process.env.FONT_FILE?.trim() ? path.resolve(process.env.FONT_FILE.trim()) : "",
  font: process.env.FONT_FAMILY?.trim() || "Vazirmatn",
  geminiApiKey: process.env.GEMINI_API_KEY?.trim() || "",
  pexelsApiKey: process.env.PEXELS_API_KEY?.trim() || process.env.PEXELS_API_KEY_1?.trim() || "",
  jamendoClientId: process.env.JAMENDO_CLIENT_ID?.trim() || process.env.JAMENDO_CLIENT_ID_1?.trim() || "",
  jamendoAudioFormat: (process.env.JAMENDO_AUDIO_FORMAT || "mp32").trim().toLowerCase(),
  jamendoMaxResults: boundedNumber(process.env.JAMENDO_MAX_RESULTS, 50, 10, 200),
  jamendoRequireContentIdFree: String(process.env.JAMENDO_REQUIRE_CONTENT_ID_FREE || "0") === "1",
  jamendoAllowExperimental: String(process.env.JAMENDO_ALLOW_EXPERIMENTAL || "0") === "1",
  musicVolume: boundedNumber(process.env.MUSIC_VOLUME, 0.24, 0.05, 0.8),
  musicMasterLufs: boundedNumber(process.env.MUSIC_MASTER_LUFS, -20, -24, -14),
  preferredModels: (process.env.GEMINI_MODELS || "gemini-3.6-flash,gemini-3.5-flash,gemini-3.1-flash-lite")
    .split(",").map((value) => value.trim()).filter(Boolean),
  requestTimeoutMs: boundedNumber(process.env.REQUEST_TIMEOUT_MS, 90_000, 5_000, 300_000),
  maxRetries: boundedNumber(process.env.MAX_RETRIES, 4, 1, 8),
  strictAi: String(process.env.STRICT_AI || "0") === "1",
  proxy: process.env.HTTPS_PROXY?.trim() || process.env.HTTP_PROXY?.trim() || "",
  imageQuality: (process.env.IMAGE_QUALITY || "original").toLowerCase(),
  maxImageBytes: boundedNumber(process.env.MAX_IMAGE_BYTES, 35_000_000, 2_000_000, 100_000_000),
  maxVideoBytes: boundedNumber(process.env.MAX_VIDEO_BYTES, 180_000_000, 20_000_000, 1_000_000_000),
  maxAudioBytes: boundedNumber(process.env.MAX_AUDIO_BYTES, 50_000_000, 5_000_000, 250_000_000),
  keepCredits: String(process.env.SHOW_IMAGE_CREDITS || "1") !== "0",
  enableAiReview: String(process.env.AI_REVIEW || "0") === "1",
};

export function configure(options = {}, outputRoot = "") {
  if (outputRoot) {
    CONFIG.outputDir = path.resolve(outputRoot);
    CONFIG.cacheDir = path.join(CONFIG.outputDir, "diagnostics", "cache");
  }
  if (String(options.font_dir || "").trim()) CONFIG.fontDir = path.resolve(String(options.font_dir));
  if (String(options.font_file || "").trim()) CONFIG.fontFile = path.resolve(String(options.font_file));
  if (String(options.font_family || "").trim() && String(options.font_family).toLowerCase() !== "auto") {
    CONFIG.font = String(options.font_family).trim();
  }
}

export const W = 13.333;
export const H = 7.5;
