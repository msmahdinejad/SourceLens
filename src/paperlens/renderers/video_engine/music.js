import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { CONFIG } from "./config.js";
import { cleanText, fetchWithRetry, hash } from "./utils.js";

function runProcess(command, args, cwd) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, windowsHide: true, stdio: ["ignore", "inherit", "inherit"] });
    child.on("error", reject);
    child.on("close", (code) => code === 0 ? resolve() : reject(new Error(`${command} با کد ${code} متوقف شد.`)));
  });
}
function ffmpegExists() { return spawnSync("ffmpeg", ["-version"], { stdio: "ignore", windowsHide: true }).status === 0; }

function unique(values) { return values.filter((value, index, array) => value && array.indexOf(value) === index); }
function normalizeTag(value) { return cleanText(value).toLowerCase().replace(/[^a-z0-9 -]/g, "").trim(); }
function flattenTags(value) {
  const output = [];
  const visit = (item) => {
    if (Array.isArray(item)) item.forEach(visit);
    else if (item && typeof item === "object") Object.values(item).forEach(visit);
    else if (typeof item === "string") output.push(...item.split(/[;,|]/).map(normalizeTag));
  };
  visit(value);
  return unique(output.filter(Boolean));
}

export function buildMusicBrief(plan, duration) {
  const direction = plan.direction?.music || {};
  const profile = plan.direction?.profile || "general";
  const profileGenre = {
    technology: "ambient", business: "lounge", documentary: "ambient",
    education: "ambient", educational: "ambient", emotional: "relaxation",
    social: "lounge", inspirational: "ambient", general: "ambient",
  }[profile] || "ambient";
  const requestedGenre = normalizeTag(direction.genre || profileGenre);
  const genre = ["ambient", "lounge", "relaxation", "soundtrack", "corporate"].includes(requestedGenre)
    ? requestedGenre
    : profileGenre;
  // This project creates a narration bed, not a foreground soundtrack. Calmness and
  // speech space always outrank visual energy or topical intensity.
  const moods = unique([
    "calm", "soft", "gentle", "focused", "peaceful",
    ...(direction.moods || []).map(normalizeTag).filter((item) => !/gloom|dark|dramatic|epic|aggressive|energetic|tense|sad/.test(item)),
  ]).slice(0, 7);
  const instruments = unique(["piano", "pads", ...(direction.instruments || []).map(normalizeTag)]).slice(0, 4);
  const speed = ["low", "medium"];
  const fuzzyTags = unique([genre, "background", "calm", "instrumental", ...moods.slice(0, 2)]).slice(0, 6);
  return {
    provider: "jamendo",
    instrumental: true,
    purpose: "voiceover-bed",
    genre,
    moods,
    instruments,
    speed,
    fuzzyTags,
    durationSeconds: duration,
    energyCurve: "low-steady-soft-resolve",
    contentProfile: profile,
    avoid: unique(["vocals", "gloomy", "dark", "dramatic", "epic", "aggressive", "heavy percussion", ...(direction.avoid || [])]),
    queryDescription: `voiceover bed; ${genre}; ${moods.join(", ")}; ${speed.join(", ")}`,
  };
}

function licenseClass(url) {
  const value = String(url || "").toLowerCase();
  if (!value) return "unknown";
  if (value.includes("zero") || value.includes("publicdomain")) return "cc0";
  const hasNc = value.includes("-nc") || value.includes("/nc");
  const hasNd = value.includes("-nd") || value.includes("/nd");
  const hasSa = value.includes("-sa") || value.includes("/sa");
  if (hasNd) return hasNc ? "nc-nd" : "nd";
  if (hasSa) return hasNc ? "nc-sa" : "sa";
  if (hasNc) return "nc";
  if (value.includes("creativecommons.org/licenses/by/")) return "by";
  return "other";
}

function trackTags(track) {
  return unique([
    ...flattenTags(track.musicinfo?.tags),
    ...flattenTags(track.musicinfo?.vartags),
    ...String(track.name || "").toLowerCase().split(/[^a-z0-9]+/).map(normalizeTag),
    ...String(track.album_name || "").toLowerCase().split(/[^a-z0-9]+/).map(normalizeTag),
  ].filter(Boolean));
}

function overlapScore(wanted, actual) {
  if (!wanted.length) return 0;
  const haystack = new Set(actual);
  return wanted.reduce((sum, tag) => sum + (haystack.has(tag) ? 1 : actual.some((item) => item.includes(tag) || tag.includes(item)) ? 0.55 : 0), 0) / wanted.length;
}

const TAG_ALIASES = new Map([
  ["cinematic", ["soundtrack", "ambient"]],
  ["corporate", ["pop", "lounge", "soundtrack"]],
  ["business", ["corporate", "lounge", "electronic"]],
  ["technology", ["electronic", "ambient", "soundtrack"]],
  ["tech", ["electronic", "ambient"]],
  ["futuristic", ["electronic", "ambient"]],
  ["documentary", ["ambient", "soundtrack"]],
  ["educational", ["ambient", "lounge"]],
  ["inspiring", ["positive", "motivational"]],
  ["motivational", ["inspiring", "positive"]],
  ["calm", ["relaxation", "ambient"]],
  ["dramatic", ["soundtrack", "cinematic"]],
]);

const FEATURED_GENRES = new Set(["lounge", "classical", "electronic", "jazz", "pop", "hiphop", "relaxation", "rock", "songwriter", "world", "metal", "soundtrack"]);

// Background music for short videos should sound like an intentional score, not an
// experimental/noise recording that merely happens to be tagged as instrumental.
const HARD_BLOCKED_BACKGROUND_TAGS = new Set([
  "harsh noise", "noise", "industrial", "industrial ambient", "power electronics",
  "field recording", "sound art", "spoken word", "electroacoustic", "live recording",
]);
const SOFT_RISK_BACKGROUND_TAGS = new Set([
  "experimental", "drone", "dark ambient", "improvisation", "improv", "glitch",
  "avant garde", "abstract", "unclassifiable", "sound collage", "minimal electronic",
]);
const POSITIVE_BACKGROUND_TAGS = new Set([
  "background", "soundtrack", "cinematic", "corporate", "lounge", "chillout",
  "instrumental", "easy listening", "motivational", "inspiring", "uplifting",
  "positive", "ambient", "piano", "acoustic", "downtempo", "electronic",
]);

const HARD_BLOCKED_VOICEOVER_TAGS = new Set([
  "gloomy", "dark", "sad", "melancholic", "depressing", "horror", "terror",
  "suspense", "tense", "tension", "aggressive", "epic", "dramatic", "action",
  "energetic", "hard", "heavy", "metal", "rock", "punk", "rap", "hip hop",
  "hiphop", "trap", "techno", "trance", "dubstep", "drum and bass", "dance",
]);
const SOFT_RISK_VOICEOVER_TAGS = new Set([
  "beat", "beats", "drums", "percussion", "groovy", "funk", "upbeat", "fast",
  "rhythmic", "solo", "virtuoso", "orchestral", "trailer", "anthem",
]);
const POSITIVE_VOICEOVER_TAGS = new Set([
  "calm", "peaceful", "soft", "gentle", "relaxing", "relaxation", "ambient",
  "background", "underscore", "lounge", "chillout", "chill", "piano", "pads",
  "acoustic", "minimal", "meditative", "documentary", "corporate", "easy listening",
  "slow", "warm", "focus", "focused", "atmospheric", "quiet", "smooth",
]);

function expandJamendoTags(values) {
  const output = [];
  for (const raw of values || []) {
    const normalized = normalizeTag(raw);
    if (!normalized) continue;
    const tokens = normalized.split(/\s+/).filter(Boolean);
    output.push(normalized, ...tokens);
    for (const token of [normalized, ...tokens]) output.push(...(TAG_ALIASES.get(token) || []));
  }
  return unique(output.map(normalizeTag).filter(Boolean));
}

function isInstrumentalTrack(track) {
  if (track.__instrumentalSearchMatch === true) return true;
  const declared = normalizeTag(track.musicinfo?.vocalinstrumental || "");
  if (declared === "instrumental") return true;
  if (declared === "vocal") return false;
  const declaredTags = flattenTags(track.musicinfo?.tags).map(normalizeTag);
  if (declaredTags.some((value) => containsNormalizedTerm([value], "instrumental"))) return true;
  if (declaredTags.some((value) => containsNormalizedTerm([value], "vocal"))) return false;
  const searchable = normalizeTag(`${track.name || ""} ${track.album_name || ""}`);
  return /\b(instrumental|background|underscore|soundtrack|ambient|cinematic)\b/.test(searchable);
}

function contentIdState(track) {
  if (track.content_id_free === true || track.content_id_free === 1 || track.content_id_free === "true") return true;
  if (track.content_id_free === false || track.content_id_free === 0 || track.content_id_free === "false") return false;
  return null;
}

function containsNormalizedTerm(values, term) {
  const escaped = String(term).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(?:^|\\s)${escaped}(?:$|\\s)`, "i");
  return values.some((value) => value === term || pattern.test(value));
}

function backgroundSuitability(track, tags) {
  const duration = Number(track.duration || 0);
  const titleAlbum = normalizeTag(`${track.name || ""} ${track.album_name || ""}`);
  const combined = unique([...tags, titleAlbum]);
  const hardTags = [...HARD_BLOCKED_BACKGROUND_TAGS].filter((item) => containsNormalizedTerm(combined, item));
  const riskTags = [...SOFT_RISK_BACKGROUND_TAGS].filter((item) => containsNormalizedTerm(combined, item));
  const positiveTags = [...POSITIVE_BACKGROUND_TAGS].filter((item) => containsNormalizedTerm(combined, item));
  const extremeLength = duration > 600;
  const longExperimental = duration > 420 && riskTags.length > 0;
  const liveOrDemo = /\b(live|demo|rehearsal|field recording)\b/.test(titleAlbum);
  const blocked = !CONFIG.jamendoAllowExperimental && (hardTags.length > 0 || longExperimental || liveOrDemo || extremeLength);
  const durationFit = duration >= 45 && duration <= 360 ? 1 : duration <= 480 ? 0.72 : duration <= 600 ? 0.42 : 0.16;
  const riskPenalty = Math.min(0.65, riskTags.length * 0.16);
  const positiveBoost = Math.min(0.2, positiveTags.length * 0.035);
  return {
    blocked,
    hardTags,
    riskTags,
    extremeLength,
    positiveTags,
    durationFit,
    score: Math.max(0, Math.min(1.15, 0.72 + positiveBoost - riskPenalty)) * durationFit,
  };
}

function voiceoverBedSuitability(track, tags) {
  const titleAlbum = normalizeTag(`${track.name || ""} ${track.album_name || ""}`);
  const declaredSpeed = normalizeTag(track.musicinfo?.speed || "");
  const combined = unique([...tags, titleAlbum, declaredSpeed]);
  const hardTags = [...HARD_BLOCKED_VOICEOVER_TAGS].filter((item) => containsNormalizedTerm(combined, item));
  const riskTags = [...SOFT_RISK_VOICEOVER_TAGS].filter((item) => containsNormalizedTerm(combined, item));
  const positiveTags = [...POSITIVE_VOICEOVER_TAGS].filter((item) => containsNormalizedTerm(combined, item));
  const highSpeed = /(?:very high|high|fast)/.test(declaredSpeed);
  const gloomyTitle = /\b(gloomy|dark|sad|melanchol|horror|dramatic|epic|aggressive|tension|suspense)\b/.test(titleAlbum);
  const lacksBackgroundEvidence = positiveTags.length === 0;
  const blocked = hardTags.length > 0 || highSpeed || gloomyTitle || lacksBackgroundEvidence;
  const positiveScore = Math.min(1, positiveTags.length / 4);
  const riskPenalty = Math.min(0.7, riskTags.length * 0.16);
  const speedScore = declaredSpeed === "low" ? 1 : declaredSpeed === "medium" || !declaredSpeed ? 0.78 : 0.2;
  return {
    blocked,
    hardTags,
    riskTags,
    positiveTags,
    declaredSpeed,
    lacksBackgroundEvidence,
    score: Math.max(0, Math.min(1.1, 0.36 + positiveScore * 0.48 + speedScore * 0.18 - riskPenalty)),
  };
}

export function scoreJamendoTrack(track, brief) {
  const tags = trackTags(track);
  const tagScore = overlapScore(brief.fuzzyTags, tags);
  const moodScore = overlapScore(brief.moods, tags);
  const instrumentScore = brief.instruments.length ? overlapScore(brief.instruments, tags) : 0.5;
  const duration = Number(track.duration || 0);
  const durationScore = duration >= brief.durationSeconds + 8 ? 1 : duration >= brief.durationSeconds ? 0.82 : duration >= 30 ? 0.58 : 0.2;
  const license = licenseClass(track.license_ccurl);
  const licenseScore = ["cc0", "by", "nc"].includes(license)
    ? 1
    : ["sa", "nc-sa"].includes(license)
      ? 0.72
      : license === "unknown"
        ? 0.1
        : 0;
  const downloadable = track.audiodownload_allowed !== false
    && (track.audiodownload_allowed === true || Boolean(track.audiodownload || track.audio));
  const instrumental = isInstrumentalTrack(track);
  const contentIdFree = contentIdState(track);
  const contentIdScore = contentIdFree === true ? 1 : contentIdFree === false ? 0.35 : 0.55;
  const stats = track.stats || {};
  const popularity = Math.min(1, Math.log10(1 + Number(stats.listens || stats.downloads || 0)) / 6);
  const suitability = backgroundSuitability(track, tags);
  const voiceoverSuitability = voiceoverBedSuitability(track, tags);
  const baseScore = tagScore * 0.12 + moodScore * 0.09 + instrumentScore * 0.04 + durationScore * 0.08 + licenseScore * 0.13 + contentIdScore * 0.08 + popularity * 0.03 + suitability.score * 0.12 + voiceoverSuitability.score * 0.31;
  return {
    score: downloadable && instrumental && !suitability.blocked && !voiceoverSuitability.blocked ? baseScore : -1,
    tags, license, downloadable, instrumental, contentIdFree, suitability, voiceoverSuitability,
  };
}

function fallbackGenres(brief) {
  const profileMap = {
    technology: ["ambient", "lounge", "relaxation"],
    business: ["lounge", "relaxation", "ambient"],
    documentary: ["ambient", "relaxation", "classical"],
    education: ["ambient", "lounge", "relaxation"],
    educational: ["ambient", "lounge", "relaxation"],
    emotional: ["relaxation", "ambient", "classical"],
    social: ["lounge", "ambient", "relaxation"],
    inspirational: ["ambient", "relaxation", "lounge"],
    general: ["ambient", "lounge", "relaxation"],
  };
  const expanded = expandJamendoTags([brief.genre, ...(brief.moods || []), ...(profileMap[brief.contentProfile] || profileMap.general)]);
  const featured = expanded.filter((tag) => FEATURED_GENRES.has(tag));
  return unique([...featured, "ambient", "lounge", "relaxation", "soundtrack"]);
}

function buildSearchStages(brief) {
  const expanded = expandJamendoTags([brief.genre, ...(brief.moods || []), ...(brief.instruments || [])]);
  const semantic = unique([...expanded, ...expandJamendoTags(brief.fuzzyTags)]).slice(0, 6);
  const genres = fallbackGenres(brief);
  const primaryGenre = genres[0] || "ambient";
  const secondaryGenre = genres[1] || "lounge";
  return [
    { id: "voiceover-relaxation-safe", tags: ["relaxation"], speed: ["low", "medium"], instrumental: true, contentIdFree: true, excludeNd: true, grouped: true },
    { id: "voiceover-lounge-safe", tags: ["lounge"], speed: ["low", "medium"], instrumental: true, contentIdFree: true, excludeNd: true, grouped: true },
    { id: "voiceover-ambient-safe", fuzzytags: ["ambient", "calm", "background"], speed: ["low", "medium"], instrumental: true, contentIdFree: true, excludeNd: true, grouped: true },
    { id: "voiceover-semantic-safe", fuzzytags: unique([primaryGenre, "calm", ...semantic.slice(0, 2)]), speed: ["low", "medium"], instrumental: true, contentIdFree: true, excludeNd: true, grouped: true },
    { id: "voiceover-relaxed-content-id", fuzzytags: unique([primaryGenre, "calm", "background"]), speed: ["low", "medium"], instrumental: true, contentIdFree: false, excludeNd: true, grouped: true },
    { id: "voiceover-genre-relaxed", fuzzytags: unique([primaryGenre, secondaryGenre]), speed: ["low", "medium"], instrumental: true, contentIdFree: false, excludeNd: false, grouped: true },
    { id: "popular-instrumental", instrumental: true, contentIdFree: false, excludeNd: false, grouped: false, order: "popularity_month" },
    { id: "popular-instrumental-page2", instrumental: true, contentIdFree: false, excludeNd: false, grouped: false, order: "downloads_total", offset: 50 },
    { id: "metadata-instrumental", instrumental: false, metadataInstrumentalOnly: true, contentIdFree: false, excludeNd: false, grouped: false, order: "popularity_total" },
  ];
}

function rankEntries(entries, brief) {
  const blockedLicenses = new Set(["nd", "nc-nd", "unknown", "other"]);
  return [...entries.values()]
    .map((entry) => ({ track: entry.track, matchStages: entry.matchStages, ...scoreJamendoTrack(entry.track, brief) }))
    .filter((item) => item.downloadable && item.instrumental && item.score >= 0 && !blockedLicenses.has(item.license) && (!CONFIG.jamendoRequireContentIdFree || item.contentIdFree === true))
    .sort((a, b) => b.score - a.score);
}

export async function searchJamendoMusic(brief, { fetcher = fetchWithRetry, onProgress = null } = {}) {
  if (!CONFIG.jamendoClientId) throw new Error("JAMENDO_CLIENT_ID در فایل .env تنظیم نشده است.");
  const stages = buildSearchStages(brief);
  const entries = new Map();
  const queryUrls = [];
  const headers = [];
  const attempts = [];
  let lastError = null;

  for (let index = 0; index < stages.length; index += 1) {
    const stage = stages[index];
    const url = new URL("https://api.jamendo.com/v3.0/tracks/");
    url.searchParams.set("client_id", CONFIG.jamendoClientId);
    url.searchParams.set("format", "json");
    url.searchParams.set("limit", String(Math.max(20, Math.min(100, CONFIG.jamendoMaxResults))));
    url.searchParams.set("type", "single albumtrack");
    if (stage.fuzzytags?.length) url.searchParams.set("fuzzytags", stage.fuzzytags.join(" "));
    if (stage.tags?.length) url.searchParams.set("tags", stage.tags.join(" "));
    if (stage.instrumental) url.searchParams.set("vocalinstrumental", "instrumental");
    if (stage.speed?.length) url.searchParams.set("speed", stage.speed.join(" "));
    // The selected section is looped when needed, so short tracks remain valid candidates.
    url.searchParams.set("durationbetween", "30_900");
    if (stage.contentIdFree) url.searchParams.set("content_id_free", "true");
    if (stage.excludeNd) url.searchParams.set("ccnd", "false");
    if (stage.featured) url.searchParams.set("featured", "true");
    url.searchParams.set("include", "licenses musicinfo stats");
    url.searchParams.set("audioformat", CONFIG.jamendoAudioFormat);
    url.searchParams.set("audiodlformat", CONFIG.jamendoAudioFormat);
    if (stage.grouped) url.searchParams.set("groupby", "artist_id");
    if (stage.offset) url.searchParams.set("offset", String(stage.offset));
    if (stage.order) url.searchParams.set("order", stage.order);
    else url.searchParams.set("boost", "popularity_month");

    const safeUrl = url.toString().replace(CONFIG.jamendoClientId, "***");
    queryUrls.push(safeUrl);
    const attempt = { stage: stage.id, query: safeUrl, received: 0, eligibleAfterStage: 0, warnings: "", status: "pending" };
    attempts.push(attempt);
    try {
      const response = await fetcher(url.toString(), {}, `Jamendo music search ${index + 1}`, 3);
      if (!response.ok) throw new Error(`Jamendo API (${response.status}): ${(await response.text()).slice(0, 500)}`);
      const payload = await response.json();
      if (payload?.headers?.status && payload.headers.status !== "success") throw new Error(`Jamendo API: ${payload.headers.error_message || "پاسخ ناموفق"}`);
      headers.push(payload.headers || {});
      const results = Array.isArray(payload.results) ? payload.results : [];
      attempt.received = results.length;
      attempt.warnings = cleanText(payload.headers?.warnings || "");
      attempt.status = "success";

      for (const rawTrack of results) {
        const track = stage.instrumental ? { ...rawTrack, __instrumentalSearchMatch: true } : rawTrack;
        if (stage.metadataInstrumentalOnly && !isInstrumentalTrack(track)) continue;
        const key = String(track.id || `${track.artist_name}|${track.name}`);
        const existing = entries.get(key);
        if (existing) {
          existing.matchStages = unique([...existing.matchStages, stage.id]);
          if (contentIdState(existing.track) !== true && contentIdState(track) === true) existing.track = track;
          else if (track.__instrumentalSearchMatch === true) existing.track.__instrumentalSearchMatch = true;
        } else {
          entries.set(key, { track, matchStages: [stage.id] });
        }
      }

      const rankedNow = rankEntries(entries, brief);
      attempt.eligibleAfterStage = rankedNow.length;
      onProgress?.({ index: index + 1, total: stages.length, stage: stage.id, received: results.length, eligible: rankedNow.length, warnings: attempt.warnings });
      if (rankedNow.length >= 8) break;
    } catch (error) {
      lastError = error;
      attempt.status = "error";
      attempt.error = error.message;
      onProgress?.({ index: index + 1, total: stages.length, stage: stage.id, received: 0, eligible: rankEntries(entries, brief).length, error: error.message });
    }
  }

  if (!entries.size && lastError && attempts.every((item) => item.status === "error")) {
    lastError.attempts = attempts;
    lastError.queryUrls = queryUrls;
    throw lastError;
  }
  const ranked = rankEntries(entries, brief);
  const rankedIds = new Set(ranked.map((item) => String(item.track.id || `${item.track.artist_name}|${item.track.name}`)));
  const rejected = [...entries.values()]
    .map((entry) => ({ track: entry.track, matchStages: entry.matchStages, ...scoreJamendoTrack(entry.track, brief) }))
    .filter((item) => !rankedIds.has(String(item.track.id || `${item.track.artist_name}|${item.track.name}`)))
    .slice(0, 30)
    .map((item) => ({
      id: String(item.track.id || ""),
      title: item.track.name || "",
      artist: item.track.artist_name || "",
      license: item.license,
      downloadable: item.downloadable,
      instrumental: item.instrumental,
      contentIdFree: item.contentIdFree,
      matchStages: item.matchStages,
      reason: !item.downloadable
        ? "download-not-allowed"
        : !item.instrumental
          ? "not-instrumental"
          : item.suitability?.blocked
            ? `background-style-blocked:${[...(item.suitability.hardTags || []), ...(item.suitability.riskTags || [])].join(",") || "unsuitable"}`
            : item.voiceoverSuitability?.blocked
              ? `voiceover-bed-blocked:${[...(item.voiceoverSuitability.hardTags || []), ...(item.voiceoverSuitability.riskTags || [])].join(",") || "unsuitable"}`
            : ["nd", "nc-nd", "unknown", "other"].includes(item.license)
              ? "license-blocked"
              : "score-rejected",
    }));
  return { queryUrl: queryUrls[0] || "", queryUrls, ranked, rejected, headers, attempts, rawCount: entries.size };
}

function parseWaveform(track) {
  try {
    const raw = typeof track.waveform === "string" ? JSON.parse(track.waveform) : track.waveform;
    return Array.isArray(raw?.peaks) ? raw.peaks.map(Number).filter(Number.isFinite) : [];
  } catch { return []; }
}

export function chooseMusicOffset(track, duration, energy = "balanced") {
  const trackDuration = Number(track.duration || 0);
  const maxOffset = Math.max(0, trackDuration - duration - 1);
  if (!maxOffset) return 0;
  const peaks = parseWaveform(track);
  // Without a trustworthy waveform, never jump deep into a long track. Long ambient
  // recordings often contain drones/noise passages far from their intro.
  if (peaks.length < 20) {
    const safeIntroOffset = energy === "dynamic" ? 4 : energy === "calm" ? 1.5 : 2.5;
    return Math.min(maxOffset, safeIntroOffset);
  }
  const windowSize = Math.max(8, Math.round((duration / trackDuration) * peaks.length));
  let best = { score: -Infinity, index: 0 };
  for (let index = 0; index + windowSize <= peaks.length; index += Math.max(1, Math.round(windowSize / 9))) {
    const window = peaks.slice(index, index + windowSize);
    const average = window.reduce((sum, value) => sum + value, 0) / window.length;
    const variance = window.reduce((sum, value) => sum + (value - average) ** 2, 0) / window.length;
    const target = energy === "dynamic" ? 48 : energy === "calm" ? 34 : 40;
    const targetFit = 1 - Math.min(1, Math.abs(average - target) / 70);
    const motion = Math.min(1, Math.sqrt(variance) / 35);
    const score = targetFit * 0.88 + motion * (energy === "calm" ? 0.02 : 0.08);
    if (score > best.score) best = { score, index };
  }
  return Math.min(maxOffset, (best.index / peaks.length) * trackDuration);
}

async function downloadTrack(track) {
  const sourceUrl = track.audiodownload || track.audio;
  if (!sourceUrl) throw new Error("Jamendo لینک صوتی قابل استفاده برنگرداند.");
  const key = hash(`jamendo|${track.id}|${sourceUrl}|${CONFIG.jamendoAudioFormat}`).slice(0, 20);
  const target = path.join(CONFIG.cacheDir, `jamendo-${key}.mp3`);
  if (!fsSync.existsSync(target)) {
    const response = await fetchWithRetry(sourceUrl, { headers: { Accept: "audio/mpeg,audio/*,*/*" } }, `دانلود موسیقی Jamendo ${track.id}`, 3);
    if (!response.ok) throw new Error(`دانلود موسیقی Jamendo: HTTP ${response.status}`);
    const announced = Number(response.headers.get("content-length") || 0);
    if (announced && announced > CONFIG.maxAudioBytes) throw new Error("حجم موسیقی بیش از حد مجاز است.");
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length > CONFIG.maxAudioBytes) throw new Error("حجم موسیقی بیش از حد مجاز است.");
    await fs.writeFile(target, buffer);
  }
  return target;
}

function probeAudioFile(filePath) {
  const result = spawnSync("ffprobe", [
    "-v", "error", "-select_streams", "a:0",
    "-show_entries", "stream=codec_name,sample_rate,channels:format=duration,format_name",
    "-of", "json", filePath,
  ], { encoding: "utf8", windowsHide: true, maxBuffer: 4 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(`فایل دانلودشده صوت معتبر نیست: ${(result.stderr || "ffprobe failed").trim()}`);
  const parsed = JSON.parse(result.stdout || "{}");
  const stream = parsed.streams?.[0];
  const duration = Number(parsed.format?.duration || 0);
  if (!stream || !stream.codec_name || !Number.isFinite(duration) || duration < 20) throw new Error("فایل دانلودشده Stream صوتی معتبر یا مدت کافی ندارد.");
  return {
    codec: stream.codec_name,
    sampleRate: Number(stream.sample_rate || 0),
    channels: Number(stream.channels || 0),
    duration,
    format: parsed.format?.format_name || "",
  };
}

function analyzeAudioExcerpt(filePath, offset, requestedDuration) {
  const excerpt = Math.max(8, Math.min(24, Number(requestedDuration || 20)));
  const result = spawnSync("ffmpeg", [
    "-hide_banner", "-nostats", "-ss", Number(offset || 0).toFixed(3), "-t", excerpt.toFixed(3),
    "-i", filePath, "-af", "volumedetect", "-f", "null", "-",
  ], { encoding: "utf8", windowsHide: true, maxBuffer: 8 * 1024 * 1024 });
  const output = `${result.stdout || ""}
${result.stderr || ""}`;
  const meanMatch = output.match(/mean_volume:\s*(-?[0-9.]+) dB/i);
  const maxMatch = output.match(/max_volume:\s*(-?[0-9.]+) dB/i);
  const meanVolumeDb = meanMatch ? Number(meanMatch[1]) : null;
  const maxVolumeDb = maxMatch ? Number(maxMatch[1]) : null;
  const reasons = [];
  if (result.status !== 0) reasons.push("ffmpeg-analysis-failed");
  if (meanVolumeDb != null && meanVolumeDb < -46) reasons.push("almost-silent");
  if (maxVolumeDb != null && maxVolumeDb > 0.2) reasons.push("clipped-source");
  return { passed: reasons.length === 0, excerptSeconds: excerpt, meanVolumeDb, maxVolumeDb, reasons };
}

function validateDownloadedTrack(track, sourcePath, offset, duration) {
  const probe = probeAudioFile(sourcePath);
  const analysis = analyzeAudioExcerpt(sourcePath, offset, duration);
  return { passed: analysis.passed, probe, analysis };
}

export function buildMusicEnvelope(plan, duration) {
  const scenes = Array.isArray(plan?.scenes) ? plan.scenes : [];
  if (!scenes.length) return [];
  const requested = scenes.map((scene) => Number(scene.durationSeconds ?? scene.duration_seconds ?? 0));
  const weights = requested.map((value) => Number.isFinite(value) && value > 0 ? value : 1);
  const totalWeight = weights.reduce((sum, value) => sum + value, 0);
  let cursor = 0;
  return scenes.map((scene, index) => {
    const sceneDuration = duration * weights[index] / totalWeight;
    const intent = scene.visual?.intent || scene.visualIntent || "footage";
    // Keep the score stable and subordinate to narration. Visual intensity should not
    // turn the background bed into foreground music.
    let factor = 0.88;
    if (index === 0) factor = 0.76;
    else if (index === scenes.length - 1) factor = 0.7;
    else if (["data", "process", "comparison", "timeline"].includes(intent)) factor = 0.92;
    else if (["quote", "statement"].includes(intent)) factor = 0.8;
    const window = {
      scene: index + 1,
      start: Number(cursor.toFixed(3)),
      end: Number((cursor + sceneDuration).toFixed(3)),
      intent,
      factor: Number(factor.toFixed(3)),
    };
    cursor += sceneDuration;
    return window;
  });
}

export function backgroundMasterFilter(duration, plan) {
  const intro = Math.min(4, duration * 0.12);
  const outro = Math.min(5, duration * 0.14);
  const envelope = buildMusicEnvelope(plan, duration);
  return [
    "aresample=48000",
    "highpass=f=55",
    "lowpass=f=14500",
    // Carve a little room around the most important speech-presence frequencies.
    "equalizer=f=2600:t=q:w=1.15:g=-2.2",
    "equalizer=f=5200:t=q:w=1.1:g=-1.3",
    `loudnorm=I=${CONFIG.musicMasterLufs.toFixed(1)}:TP=-1.5:LRA=9`,
    ...envelope.map((item) => `volume=${item.factor.toFixed(3)}:enable='between(t,${item.start.toFixed(3)},${item.end.toFixed(3)})'`),
    `afade=t=in:st=0:d=${Math.min(1.25, intro).toFixed(3)}`,
    `afade=t=out:st=${Math.max(0, duration - Math.min(1.9, outro)).toFixed(3)}:d=${Math.min(1.9, outro).toFixed(3)}`,
    "alimiter=limit=0.96",
  ].join(",");
}

export async function prepareJamendoMusic(plan, targetDir, { duration, mode = "auto", usage = "noncommercial" } = {}) {
  const brief = buildMusicBrief(plan, duration);
  const diagnosticsPath = path.join(targetDir, "music-plan.json");
  if (mode === "off") {
    const result = { status: "disabled", provider: "jamendo", brief };
    await fs.writeFile(diagnosticsPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    return { ...result, diagnosticsPath };
  }
  if (usage === "commercial") {
    const result = { status: "skipped", provider: "jamendo", reason: "Jamendo free API/music is not enabled for commercial use in this project.", brief };
    await fs.writeFile(diagnosticsPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    return { ...result, diagnosticsPath };
  }
  if (!CONFIG.jamendoClientId) {
    const result = { status: "unavailable", provider: "jamendo", reason: "JAMENDO_CLIENT_ID is missing.", brief };
    await fs.writeFile(diagnosticsPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    return { ...result, diagnosticsPath };
  }
  if (!ffmpegExists()) throw new Error("FFmpeg برای آماده‌سازی موسیقی پیدا نشد.");

  try {
    const search = await searchJamendoMusic(brief, {
      onProgress: ({ index, total, stage, received, eligible, warnings, error }) => {
        const suffix = error ? `خطا: ${error}` : `${received} نتیجه، ${eligible} گزینه قابل استفاده${warnings ? `، هشدار: ${warnings}` : ""}`;
        console.log(`Jamendo ${index}/${total} [${stage}]: ${suffix}`);
      },
    });
    if (!search.ranked.length) {
      const result = {
        status: "not-found",
        provider: "jamendo",
        reason: search.rawCount
          ? "Jamendo نتیجه برگرداند، اما هیچ قطعه‌ای پس از بررسی دانلود، مجوز و سبک بک‌گراند قابل استفاده نبود."
          : "Jamendo برای جست‌وجوهای ساخته‌شده قطعه‌ای برنگرداند.",
        brief,
        query: search.queryUrl,
        queryAlternatives: search.queryUrls,
        candidates: [],
        rejected: search.rejected || [],
        attempts: search.attempts || [],
        rawCount: search.rawCount || 0,
      };
      await fs.writeFile(diagnosticsPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
      return { ...result, diagnosticsPath };
    }

    let chosen = null;
    let sourcePath = "";
    let offset = 0;
    let quality = null;
    const qualityRejected = [];
    for (const candidate of search.ranked.slice(0, 12)) {
      try {
        const candidateSource = await downloadTrack(candidate.track);
        const candidateOffset = chooseMusicOffset(candidate.track, duration, plan.direction?.energy || "balanced");
        const candidateQuality = validateDownloadedTrack(candidate.track, candidateSource, candidateOffset, duration);
        if (!candidateQuality.passed) {
          qualityRejected.push({
            id: String(candidate.track.id || ""), title: candidate.track.name || "", artist: candidate.track.artist_name || "",
            reason: candidateQuality.analysis.reasons.join(",") || "audio-quality-rejected", quality: candidateQuality,
          });
          continue;
        }
        chosen = candidate;
        sourcePath = candidateSource;
        offset = candidateOffset;
        quality = candidateQuality;
        break;
      } catch (error) {
        qualityRejected.push({
          id: String(candidate.track.id || ""), title: candidate.track.name || "", artist: candidate.track.artist_name || "",
          reason: error.message,
        });
      }
    }

    if (!chosen) {
      const result = {
        status: "not-found", provider: "jamendo",
        reason: "قطعه‌های Jamendo پیدا شدند، اما همه در کنترل کیفیت فایل صوتی رد شدند.",
        brief, query: search.queryUrl, queryAlternatives: search.queryUrls,
        candidates: search.ranked.slice(0, 12).map((item) => ({
          id: String(item.track.id || ""), title: item.track.name || "", artist: item.track.artist_name || "", score: Number(item.score.toFixed(4)),
        })),
        rejected: [...(search.rejected || []), ...qualityRejected], attempts: search.attempts || [], rawCount: search.rawCount || 0,
      };
      await fs.writeFile(diagnosticsPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
      return { ...result, diagnosticsPath };
    }
    const outputPath = path.join(targetDir, "background-music.m4a");
    await runProcess("ffmpeg", [
      "-y", "-loglevel", "error", "-ss", offset.toFixed(3), "-stream_loop", "-1", "-i", sourcePath,
      "-t", Number(duration).toFixed(3), "-vn", "-af", backgroundMasterFilter(duration, plan),
      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", outputPath,
    ], targetDir);
    const result = {
      status: "ready", provider: "jamendo", brief: { ...brief, energyEnvelope: buildMusicEnvelope(plan, duration) }, query: search.queryUrl, queryAlternatives: search.queryUrls, outputPath, sourcePath,
      selected: {
        id: String(chosen.track.id || ""), title: chosen.track.name || "", artist: chosen.track.artist_name || "",
        duration: Number(chosen.track.duration || 0), startOffset: Number(offset.toFixed(3)), score: Number(chosen.score.toFixed(4)),
        licenseUrl: chosen.track.license_ccurl || "", licenseClass: chosen.license,
        shareAlike: ["sa", "nc-sa"].includes(chosen.license),
        sourceUrl: chosen.track.shareurl || chosen.track.shorturl || "", contentIdFree: chosen.contentIdFree === true,
        contentIdStatus: chosen.contentIdFree === true ? "free" : chosen.contentIdFree === false ? "not-free" : "unknown",
        matchStages: chosen.matchStages || [],
        suitability: chosen.suitability || null,
        voiceoverSuitability: chosen.voiceoverSuitability || null,
        quality,
        masterTargetLufs: CONFIG.musicMasterLufs,
      },
      attempts: search.attempts || [],
      rejected: [...(search.rejected || []), ...qualityRejected],
      candidates: search.ranked.slice(0, 8).map((item) => ({
        id: String(item.track.id || ""), title: item.track.name || "", artist: item.track.artist_name || "",
        score: Number(item.score.toFixed(4)), license: item.license, contentIdFree: item.contentIdFree,
        matchStages: item.matchStages || [], suitability: item.suitability || null,
        voiceoverSuitability: item.voiceoverSuitability || null,
      })),
    };
    await fs.writeFile(diagnosticsPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    return { ...result, diagnosticsPath };
  } catch (error) {
    const result = {
      status: "error", provider: "jamendo", reason: error.message, brief,
      attempts: error.attempts || [], queryAlternatives: error.queryUrls || [],
    };
    await fs.writeFile(diagnosticsPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    return { ...result, diagnosticsPath };
  }
}

export function voiceoverFitPolicy({ sourceDuration = 0, targetDuration = 0 } = {}) {
  const source = Math.max(0, Number(sourceDuration || 0));
  const target = Math.max(0, Number(targetDuration || 0));
  return {
    sourceDuration: source,
    targetDuration: target,
    tempoRatio: 1,
    speedChanged: false,
    overflowSeconds: Number(Math.max(0, source - target).toFixed(6)),
  };
}

export async function muxAudioVersions(
  silentVideoPath,
  music,
  targetDir,
  { voiceoverAudio = "", voiceoverSourceDuration = 0, duration } = {},
) {
  const hasMusic = Boolean(music && music.status === "ready" && music.outputPath && fsSync.existsSync(music.outputPath));
  const resolvedVoiceover = voiceoverAudio ? path.resolve(voiceoverAudio) : "";
  const hasVoiceover = Boolean(resolvedVoiceover && fsSync.existsSync(resolvedVoiceover));
  const base = path.basename(silentVideoPath, path.extname(silentVideoPath)).replace(/-silent$/, "");
  const exactDuration = Number(duration).toFixed(3);
  const voiceoverFit = voiceoverFitPolicy({
    sourceDuration: voiceoverSourceDuration,
    targetDuration: duration,
  });
  let musicVideoPath = "";
  if (hasMusic) {
    musicVideoPath = path.join(targetDir, `${base}-with-music.mp4`);
    await runProcess("ffmpeg", [
      "-y", "-loglevel", "error", "-i", silentVideoPath, "-stream_loop", "-1", "-i", music.outputPath,
      "-filter_complex", `[1:a]apad=pad_dur=${exactDuration},atrim=duration=${exactDuration}[a]`,
      "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
      "-t", exactDuration, "-movflags", "+faststart", musicVideoPath,
    ], targetDir);
  }

  let finalVideoPath = "";
  if (hasVoiceover) {
    finalVideoPath = path.join(targetDir, `${base}-final.mp4`);
    if (hasMusic) {
      await runProcess("ffmpeg", [
        "-y", "-loglevel", "error", "-i", silentVideoPath, "-stream_loop", "-1", "-i", music.outputPath, "-i", resolvedVoiceover,
        "-filter_complex",
        `[1:a]aresample=48000,volume=${CONFIG.musicVolume.toFixed(3)},apad=pad_dur=${exactDuration},atrim=duration=${exactDuration}[m];[2:a]aresample=48000,loudnorm=I=-16:TP=-1.2:LRA=8,apad=pad_dur=${exactDuration},atrim=duration=${exactDuration},asplit=2[vsc][vmix];[m][vsc]sidechaincompress=threshold=0.018:ratio=10:attack=18:release=420[duck];[duck][vmix]amix=inputs=2:duration=longest:dropout_transition=0,atrim=duration=${exactDuration},alimiter=limit=0.96[a]`,
        "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", exactDuration, "-movflags", "+faststart", finalVideoPath,
      ], targetDir);
    } else {
      await runProcess("ffmpeg", [
        "-y", "-loglevel", "error", "-i", silentVideoPath, "-i", resolvedVoiceover,
        "-filter_complex", `[1:a]aresample=48000,loudnorm=I=-16:TP=-1.2:LRA=8,afade=t=in:st=0:d=0.18,apad=pad_dur=${exactDuration},atrim=duration=${exactDuration},alimiter=limit=0.96[a]`,
        "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", exactDuration, "-movflags", "+faststart", finalVideoPath,
      ], targetDir);
    }
  }
  return {
    silentVideoPath,
    musicVideoPath,
    finalVideoPath,
    voiceoverFit,
  };
}

export async function writeAudioCredits(music, targetDir) {
  const target = path.join(targetDir, "audio-credits.txt");
  const lines = ["Audio credits", "============="];
  if (music?.status === "ready") {
    lines.push(
      `Track: ${music.selected.title}`,
      `Artist: ${music.selected.artist}`,
      `Provider: Jamendo`,
      `Source: ${music.selected.sourceUrl}`,
      `License: ${music.selected.licenseUrl}`,
      `Content ID status: ${music.selected.contentIdStatus || (music.selected.contentIdFree ? "free" : "unknown/not-free")}`,
      `ShareAlike: ${music.selected.shareAlike ? "yes — keep the license/credits file with the published video" : "no"}`,
      "Usage note: Jamendo's free API access and the selected Creative Commons track license must be respected; this build treats the result as non-commercial.",
    );
  } else {
    lines.push(`No background music was embedded. Status: ${music?.status || "disabled"}${music?.reason ? ` — ${music.reason}` : ""}`);
  }
  await fs.writeFile(target, `${lines.join("\n")}\n`, "utf8");
  return target;
}
