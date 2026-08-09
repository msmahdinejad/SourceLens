import { cleanText, hash } from "./utils.js";
import { ICON_NAMES } from "./icons.js";

const STYLE_FAMILIES = {
  cinematic: {
    label: "Cinematic Dark",
    theme: { primary: "365CF5", secondary: "19A7A0", accent: "FFBF47", background: "07111F", surface: "101D31", text: "FFFFFF", muted: "D6E0EE" },
    grade: "cinematic",
  },
  editorial: {
    label: "Editorial Contrast",
    theme: { primary: "E4552D", secondary: "316B83", accent: "FFD166", background: "141313", surface: "28221F", text: "FFFFFF", muted: "E7DED0" },
    grade: "warm",
  },
  neon: {
    label: "Neon Technology",
    theme: { primary: "7C5CFC", secondary: "06D6A0", accent: "41EAD4", background: "080B19", surface: "121A38", text: "FFFFFF", muted: "C7D2FE" },
    grade: "vibrant",
  },
  ocean: {
    label: "Ocean Modern",
    theme: { primary: "0EA5E9", secondary: "14B8A6", accent: "FDE047", background: "071B2B", surface: "0D2B3E", text: "FFFFFF", muted: "D1EAF5" },
    grade: "cool",
  },
  warm: {
    label: "Warm Documentary",
    theme: { primary: "D97757", secondary: "6B8E7D", accent: "F2C14E", background: "171514", surface: "2B2521", text: "FFFFFF", muted: "E9DED5" },
    grade: "warm",
  },
  mono: {
    label: "Monochrome Premium",
    theme: { primary: "D9D9D9", secondary: "8E9AAF", accent: "FFFFFF", background: "0B0B0C", surface: "1C1C1F", text: "FFFFFF", muted: "C8C8CC" },
    grade: "mono",
  },
};

const CONTENT_PROFILES = {
  technology: {
    styles: ["neon", "ocean", "cinematic"],
    energy: "dynamic",
    music: { genre: "ambient", moods: ["calm", "focused", "modern"], instruments: ["soft synthesizer", "piano", "pads"], speed: ["low", "medium"] },
  },
  educational: {
    styles: ["ocean", "editorial", "cinematic"],
    energy: "balanced",
    music: { genre: "ambient", moods: ["calm", "focused", "gentle"], instruments: ["piano", "pads"], speed: ["low", "medium"] },
  },
  documentary: {
    styles: ["warm", "cinematic", "mono"],
    energy: "balanced",
    music: { genre: "ambient", moods: ["thoughtful", "calm", "warm"], instruments: ["piano", "soft strings", "pads"], speed: ["low"] },
  },
  social: {
    styles: ["editorial", "neon", "warm"],
    energy: "dynamic",
    music: { genre: "lounge", moods: ["calm", "modern", "focused"], instruments: ["soft synthesizer", "light piano", "pads"], speed: ["low", "medium"] },
  },
  inspirational: {
    styles: ["cinematic", "warm", "ocean"],
    energy: "balanced",
    music: { genre: "ambient", moods: ["hopeful", "calm", "gentle"], instruments: ["piano", "soft strings", "pads"], speed: ["low", "medium"] },
  },
  business: {
    styles: ["ocean", "cinematic", "editorial"],
    energy: "balanced",
    music: { genre: "lounge", moods: ["calm", "professional", "focused"], instruments: ["piano", "soft guitar", "pads"], speed: ["low", "medium"] },
  },
  general: {
    styles: ["cinematic", "ocean", "editorial", "warm"],
    energy: "balanced",
    music: { genre: "ambient", moods: ["calm", "gentle", "focused"], instruments: ["piano", "pads"], speed: ["low", "medium"] },
  },
};

const LAYOUTS = [
  "lower-third", "split-panel", "glass-card", "spotlight", "editorial",
  "minimal-type", "data-bars", "process", "quote", "comparison", "timeline",
];
const MOTIONS = ["slide-up", "slide-left", "fade", "type-reveal", "reveal-up", "float-right", "scale-in"];
const NATURAL_TRANSITIONS = ["fade", "dissolve", "smoothleft", "smoothright", "smoothup", "hblur"];
const EXPRESSIVE_TRANSITIONS = ["zoomin", "circleopen", "revealleft", "revealup", "fadewhite"];
const TREATMENTS = ["clean", "cinematic", "vibrant", "mono", "soft", "warm", "cool"];
const HIERARCHIES = new Set(["balanced", "title", "subject", "media", "quote", "data"]);
const SPACING = new Set(["compact", "balanced", "airy"]);
const EMPHASIS = new Set(["balanced", "title", "body", "metric", "quote"]);

function xmur3(value) {
  let h = 1779033703 ^ value.length;
  for (let i = 0; i < value.length; i += 1) {
    h = Math.imul(h ^ value.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return (h ^= h >>> 16) >>> 0;
  };
}
function mulberry32(seed) {
  return () => {
    let t = (seed += 0x6D2B79F5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function makeRandom(seedText) { return mulberry32(xmur3(seedText)()); }
function pick(random, values) { return values[Math.floor(random() * values.length) % values.length]; }
function shuffle(random, values) {
  const result = [...values];
  for (let i = result.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

export function inferContentProfile(plan) {
  const text = cleanText(`${plan.title || ""} ${plan.subtitle || ""} ${(plan.scenes || []).map((scene) => `${scene.title || ""} ${scene.onScreenText || ""}`).join(" ")}`).toLowerCase();
  const rules = [
    ["technology", /(?:هوش مصنوعی|فناوری|تکنولوژی|دیجیتال|نرم.?افزار|ربات|داده|اینترنت|ai|technology|software|digital|robot|data)/i],
    ["educational", /(?:آموزش|یادگیری|دانشجو|درس|چگونه|راهنما|مهارت|school|student|learn|education|how to)/i],
    ["social", /(?:شبکه اجتماعی|اینستاگرام|موبایل|کاربر|جامعه|رسانه|social media|instagram|mobile|society)/i],
    ["business", /(?:کسب.?وکار|فروش|بازار|مشتری|مدیر|محصول|سرمایه|business|sales|market|customer|product)/i],
    ["inspirational", /(?:انگیزه|رشد فردی|عادت|موفقیت|امید|آینده بهتر|motivation|habit|success|hope)/i],
    ["documentary", /(?:تاریخ|جامعه|بحران|محیط زیست|واقعیت|بررسی|تحلیل|history|documentary|climate|crisis)/i],
  ];
  return rules.find(([, pattern]) => pattern.test(text))?.[0] || "general";
}

function detectIcon(scene) {
  const text = `${scene.title || ""} ${scene.onScreenText || ""}`.toLowerCase();
  const rules = [
    [/(هوش|مصنوعی|ai|یادگیری ماشین|machine)/, "brain"], [/(رشد|افزایش|پیشرفت|نتیجه|ترند)/, "trend"],
    [/(داده|آمار|تحلیل|نمودار|شاخص)/, "chart"], [/(زمان|سرعت|سریع|آینده)/, "clock"],
    [/(هدف|تمرکز|اولویت)/, "target"], [/(امنیت|اعتماد|ریسک|حفاظت)/, "shield"],
    [/(تیم|افراد|همکاری|مخاطب)/, "users"], [/(ایده|خلاق|فرصت)/, "sparkles"],
    [/(شروع|اقدام|حرکت|اجرا)/, "rocket"], [/(فرایند|سیستم|تنظیم)/, "gear"],
    [/(ارتباط|شبکه|اتصال)/, "link"], [/(موبایل|گوشی)/, "mobile"],
    [/(فروش|خرید|فروشگاه)/, "cart"], [/(جستجو|کشف)/, "search"], [/(تأیید|موفق|جمع‌بندی)/, "check"],
  ];
  return rules.find(([pattern]) => pattern.test(text))?.[1] || "sparkles";
}

function semanticIntent(scene, index, total) {
  const explicit = cleanText(scene.visualIntent || scene.intent).toLowerCase();
  if (["footage", "data", "process", "quote", "comparison", "timeline", "statement"].includes(explicit)) return explicit;
  const text = `${scene.kicker || ""} ${scene.title || ""} ${scene.onScreenText || ""}`;
  if (index === 0 || index === total - 1) return "statement";
  if (/(مقایسه|در برابر|قبل و بعد|تفاوت|vs\.?)/i.test(text)) return "comparison";
  if (/(مرحله|گام|فرایند|مسیر|روش|اول.*بعد)/i.test(text)) return "process";
  if (/(درصد|آمار|رشد|کاهش|افزایش|شاخص|عدد|برابر)/i.test(text)) return "data";
  if (/(خط زمانی|سال|گذشته|امروز|آینده|timeline)/i.test(text)) return "timeline";
  if (/(نقل قول|باور|اصل|پیام|جمله کلیدی)/i.test(text)) return "quote";
  return "footage";
}

function semanticDirection(scene) {
  const raw = scene.visual && typeof scene.visual === "object" ? scene.visual : {};
  const hierarchy = cleanText(raw.hierarchy || scene.visualHierarchy || scene.visual_hierarchy, "balanced").toLowerCase();
  const spacing = cleanText(raw.spacing || scene.spacing, "balanced").toLowerCase();
  const emphasis = cleanText(raw.emphasis || raw.textEmphasis || scene.text_emphasis, "balanced").toLowerCase();
  return {
    hierarchy: HIERARCHIES.has(hierarchy) ? hierarchy : "balanced",
    spacing: SPACING.has(spacing) ? spacing : "balanced",
    emphasis: EMPHASIS.has(emphasis) ? emphasis : "balanced",
  };
}

function layoutForIntent(intent, index, total, random, deck, direction) {
  if (index === 0) return "hero-cinematic";
  if (index === total - 1) return "closing-statement";
  if (intent === "data") return "data-bars";
  if (intent === "process") return "process";
  if (intent === "comparison") return "comparison";
  if (intent === "timeline") return "timeline";
  if (intent === "quote") return "quote";
  if (direction.hierarchy === "subject") return "spotlight";
  if (direction.hierarchy === "title") return "minimal-type";
  if (direction.hierarchy === "media") return index % 2 ? "split-panel" : "lower-third";
  if (direction.spacing === "airy") return "spotlight";
  if (direction.spacing === "compact") return "glass-card";
  return deck[index % deck.length] || pick(random, LAYOUTS);
}

function groundedChart(scene) {
  const raw = scene.visual?.chart || scene.chart;
  if (!raw || typeof raw !== "object" || raw.illustrative === true) return null;
  const labels = Array.isArray(raw.labels) ? raw.labels.map(cleanText).filter(Boolean).slice(0, 10) : [];
  const seriesValues = Array.isArray(raw.series) && Array.isArray(raw.series[0]?.values)
    ? raw.series[0].values : null;
  const values = (Array.isArray(raw.values) ? raw.values : seriesValues || []).slice(0, labels.length);
  if (labels.length < 2 || values.length !== labels.length || values.some((value) => !Number.isFinite(value))) return null;
  return {
    type: cleanText(raw.type, "bar"), title: cleanText(raw.title), labels, values,
    illustrative: false, citations: Array.isArray(raw.citations) ? raw.citations.filter(Boolean) : [],
    unit: cleanText(raw.unit), method: cleanText(raw.method),
  };
}
function processSteps(scene) {
  const provided = Array.isArray(scene.visual?.steps)
    ? scene.visual.steps.map(cleanText).filter(Boolean)
    : [];
  if (provided.length >= 3) return provided.slice(0, 3);
  const words = cleanText(scene.onScreenText || scene.title).split(/[،,:؛•]/).map(cleanText).filter(Boolean);
  return words.length >= 3 ? words.slice(0, 3) : [];
}
function comparisonItems(scene) {
  const parts = cleanText(scene.onScreenText || scene.title).split(/(?:در برابر|مقابل|،|:|؛|\bvs\.?\b)/i).map(cleanText).filter(Boolean);
  return parts.length >= 2 ? parts.slice(0, 2) : [];
}
function timelineItems(scene) {
  const parts = cleanText(scene.onScreenText || scene.title).split(/[،,:؛•]/).map(cleanText).filter(Boolean);
  return parts.length >= 3 ? parts.slice(0, 3) : [];
}

function resolveStyle(requested, profile, random) {
  const aliases = {
    documentary: "warm",
    academic: "ocean",
    minimal: "mono",
    "data-story": "editorial",
    data_story: "editorial",
  };
  const resolved = aliases[requested] || requested;
  if (resolved && resolved !== "auto" && STYLE_FAMILIES[resolved]) return resolved;
  return pick(random, CONTENT_PROFILES[profile]?.styles || CONTENT_PROFILES.general.styles);
}
function resolveEnergy(requested, profile) {
  if (["calm", "balanced", "dynamic"].includes(requested)) return requested;
  return CONTENT_PROFILES[profile]?.energy || "balanced";
}
function motionFor(layout, random, energy, typewriter) {
  if (typewriter) return "type-reveal";
  if (["data-bars", "process", "comparison", "timeline"].includes(layout)) return "reveal-up";
  if (layout === "hero-cinematic") return energy === "calm" ? "fade" : "scale-in";
  if (layout === "quote") return "float-right";
  const pool = energy === "dynamic" ? MOTIONS.filter((item) => item !== "type-reveal") : ["fade", "slide-up", "slide-left", "reveal-up"];
  return pick(random, pool);
}
function transitionFor(index, total, random, energy, intent, previous, requested = "auto") {
  if (index === total - 1) return "fade";
  if (requested === "fade" || requested === "dissolve") return requested;
  if (requested === "slide") return index % 2 ? "smoothright" : "smoothleft";
  if (requested === "dynamic") {
    return pick(random, EXPRESSIVE_TRANSITIONS.filter((item) => item !== previous));
  }
  const pool = [...NATURAL_TRANSITIONS];
  if (energy === "dynamic" && intent !== "quote" && random() > 0.62) pool.push(...EXPRESSIVE_TRANSITIONS);
  let chosen = pick(random, pool);
  if (chosen === previous) chosen = pick(random, NATURAL_TRANSITIONS.filter((item) => item !== previous));
  return chosen;
}
function typingDecision(scene, index, total, intent, random, energy) {
  const titleLength = Array.from(cleanText(scene.title || "")).length;
  const candidate = titleLength <= 48 && (index === 0 || index === total - 1 || intent === "quote" || intent === "statement" || /[؟?!]/.test(scene.title || ""));
  const probability = energy === "dynamic" ? 0.48 : energy === "calm" ? 0.28 : 0.38;
  const enabled = titleLength <= 42 && (candidate ? true : random() < probability * 0.45);
  return {
    enabled,
    target: "title",
    // Persian type should feel intentionally typed, not rapidly revealed.
    cps: energy === "dynamic" ? 8.5 + random() * 1.5 : energy === "calm" ? 6.2 + random() * 1.3 : 7.2 + random() * 1.5,
    variance: 0.2,
    cursor: true,
    cursorHold: 0.34,
    cursorBlink: false,
    initialDelay: index === 0 ? 0.22 : 0.14,
  };
}

export function directVideoPlan(plan, options = {}) {
  const seed = cleanText(options.seed, `${Date.now()}`);
  const random = makeRandom(`${plan.title}|${seed}`);
  const profile = inferContentProfile(plan);
  const styleKey = resolveStyle(options.style || "auto", profile, random);
  const style = STYLE_FAMILIES[styleKey];
  const energy = resolveEnergy(options.energy || "auto", profile);
  const layoutDeck = shuffle(random, LAYOUTS.filter((item) => !["data-bars", "process", "quote", "comparison", "timeline"].includes(item)));
  const usedLayouts = new Map();
  let lastLayout = "";
  let lastTransition = "";

  const scenes = plan.scenes.map((scene, index) => {
    const direction = semanticDirection(scene);
    let intent = semanticIntent(scene, index, plan.scenes.length);
    const chart = groundedChart(scene);
    const steps = intent === "process" ? processSteps(scene) : [];
    const comparison = intent === "comparison" ? comparisonItems(scene) : [];
    const timeline = intent === "timeline" ? timelineItems(scene) : [];
    if (
      (intent === "data" && !chart)
      || (intent === "process" && steps.length < 3)
      || (intent === "comparison" && comparison.length < 2)
      || (intent === "timeline" && timeline.length < 3)
    ) intent = "footage";
    let layout = layoutForIntent(intent, index, plan.scenes.length, random, layoutDeck, direction);
    if (layout === lastLayout || ((usedLayouts.get(layout) || 0) >= 2 && index > 0 && index < plan.scenes.length - 1)) {
      layout = layoutDeck.find((candidate) => candidate !== lastLayout && (usedLayouts.get(candidate) || 0) < 2) || layout;
    }
    lastLayout = layout;
    usedLayouts.set(layout, (usedLayouts.get(layout) || 0) + 1);
    const typing = typingDecision(scene, index, plan.scenes.length, intent, random, energy);
    const transition = transitionFor(
      index,
      plan.scenes.length,
      random,
      energy,
      intent,
      lastTransition,
      options.transitionStyle || "auto",
    );
    lastTransition = transition;
    const icon = ICON_NAMES.includes(scene.icon) ? scene.icon : detectIcon(scene);
    const visual = {
      intent,
      layout,
      hierarchy: direction.hierarchy,
      spacing: direction.spacing,
      emphasis: direction.emphasis,
      motion: motionFor(layout, random, energy, typing.enabled),
      transition,
      transitionDuration: energy === "dynamic" ? 0.42 : energy === "calm" ? 0.62 : 0.52,
      treatment: style.grade === "mono" ? "mono" : pick(random, [style.grade, ...TREATMENTS]),
      icon,
      typing,
      chart: intent === "data" ? chart : null,
      steps: intent === "process" ? steps : null,
      comparison: intent === "comparison" ? comparison : null,
      timeline: intent === "timeline" ? timeline : null,
      number: String(index + 1).padStart(2, "0"),
      decorativeSeed: hash(`${plan.title}|${seed}|${index}`).slice(0, 10),
    };
    return {
      ...scene,
      // Auto mode is deliberately video-first. Photo and motion-card are fallbacks, not quotas.
      mediaPreference: options.mediaMode === "slides" ? "card" : options.mediaMode === "photo" ? "photo" : "video",
      mediaPolicy: options.mediaMode === "slides" ? ["card"] : options.mediaMode === "photo" ? ["photo", "video", "card"] : ["video", "photo", "card"],
      visual,
    };
  });

  const profileMusic = CONTENT_PROFILES[profile]?.music || CONTENT_PROFILES.general.music;
  return {
    ...plan,
    theme: { ...plan.theme, ...style.theme },
    direction: {
      version: "7.0.4",
      mode: "autonomous",
      seed,
      profile,
      style: styleKey,
      styleLabel: style.label,
      energy,
      mediaPolicy: "video-first",
      music: {
        ...profileMusic,
        instrumental: true,
        purpose: "voiceover-bed",
        foreground: false,
        rhythmDensity: "low",
        avoid: ["vocals", "gloomy", "dark", "dramatic", "epic", "aggressive", "heavy percussion"],
        energyCurve: "low-steady-soft-resolve",
      },
    },
    scenes,
  };
}

export const VIDEO_STYLES = Object.keys(STYLE_FAMILIES);
export const VIDEO_ENERGIES = ["auto", "calm", "balanced", "dynamic"];
