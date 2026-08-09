import { LAYOUTS } from "./layouts.mjs";
import { ICON_NAMES } from "./icons.mjs";
import { asArray, cleanText, truncate, hex, safeNumber } from "./utils.mjs";

function pick(value, camel, snake) {
  return value?.[camel] ?? value?.[snake];
}

const DEFAULT_THEME = {
  preset: "neo",
  primary: "111827",
  secondary: "0F766E",
  accent: "F97316",
  accent2: "38BDF8",
  background: "F5F7FB",
  surface: "FFFFFF",
  text: "111827",
  muted: "64748B",
  imageTreatment: "cinematic",
  radius: "large",
};

function normalizeTheme(raw = {}) {
  const theme = { ...DEFAULT_THEME };
  for (const key of ["primary", "secondary", "accent", "accent2", "background", "surface", "text", "muted"]) {
    theme[key] = hex(raw[key], theme[key]);
  }
  theme.preset = cleanText(raw.preset, theme.preset);
  const treatment = pick(raw, "imageTreatment", "image_treatment");
  theme.imageTreatment = ["cinematic", "natural", "duotone", "clean"].includes(treatment) ? treatment : theme.imageTreatment;
  theme.radius = ["none", "small", "medium", "large"].includes(raw.radius) ? raw.radius : theme.radius;
  return theme;
}

function normalizeItem(raw = {}, index = 0) {
  const icon = ICON_NAMES.includes(raw?.icon) ? raw.icon : ICON_NAMES[index % ICON_NAMES.length];
  return {
    icon,
    value: truncate(raw?.value, 22),
    title: truncate(raw?.title || raw?.label, 72),
    body: truncate(raw?.body || raw?.description, 180),
    tone: cleanText(raw?.tone),
  };
}

function normalizeItems(raw, min = 0, max = 6) {
  return asArray(raw)
    .filter(Boolean)
    .map(normalizeItem)
    .filter((item) => item.title && (item.body || item.value || !isPlaceholderTitle(item.title)))
    .slice(0, max);
}

const PLACEHOLDER_TITLES = new Set([
  "context", "evidence", "decision", "action", "current approach", "constraint",
  "risk", "improved approach", "opportunity", "outcome",
]);

function isPlaceholderTitle(value) {
  const normalized = cleanText(value).toLowerCase().replace(/\s+/g, " ");
  return PLACEHOLDER_TITLES.has(normalized) || /^point\s+\d+$/.test(normalized);
}

function fallbackSlide(layout, topic, audience, index) {
  return {
    layout,
    mode: index % 3 === 0 ? "dark" : "light",
    kicker: "",
    title: topic,
    subtitle: "",
    imageQueries: [],
    focalPoint: "center",
    items: [], leftItems: [], rightItems: [],
    leftTitle: "", rightTitle: "",
    chart: null, quote: "", quoteSource: "", callout: "", footnote: "", equation: "",
    equationLabel: "", visualHierarchy: "balanced", spacing: "balanced", speakerNotes: "",
  };
}

function looksLikeEquation(value) {
  const text = cleanText(value);
  return /[=≈≤≥∑∫√→]/u.test(text)
    && /[A-Za-zΑ-ω]\s*(?:\([^)]*\)|[_^]?[A-Za-z0-9])/u.test(text);
}

function chooseMode(layout, rawMode, index) {
  if (["light", "dark", "accent", "photo"].includes(rawMode)) return rawMode;
  if (["cover_cinematic", "quote_photo", "photo_mosaic"].includes(layout)) return "photo";
  if (["comparison_dual", "process_flow", "closing_actions"].includes(layout)) return "dark";
  if (["matrix_2x2", "timeline_story", "funnel"].includes(layout)) return "accent";
  return index % 4 === 2 ? "dark" : "light";
}

function normalizeChart(raw) {
  if (!raw || typeof raw !== "object") return null;
  const requested = String(raw.type || "bar").toLowerCase();
  const type = ["bar", "column"].includes(requested)
    ? "bar"
    : ["line", "doughnut", "area"].includes(requested) ? requested : "bar";
  const labels = asArray(raw.labels).map((x) => truncate(x, 28)).slice(0, 10);
  const sourceSeries = asArray(raw.series).length
    ? asArray(raw.series)
    : asArray(raw.values).length
      ? [{ name: raw.label || raw.title || "Series 1", values: raw.values }]
      : [];
  const series = sourceSeries.slice(0, 3).map((entry, index) => ({
    name: truncate(entry?.name, 36) || `سری ${index + 1}`,
    values: asArray(entry?.values).slice(0, labels.length).map((x) => safeNumber(x, 0)),
  })).filter((entry) => entry.values.length === labels.length && labels.length > 1);
  if (!series.length) return null;
  return { type, title: truncate(raw.title, 72), labels, series };
}

function ensureLayoutData(slide, topic, audience, index) {
  const base = fallbackSlide(slide.layout, topic, audience, index);
  const result = { ...base, ...slide };
  result.items = normalizeItems(slide.items, 0, 6);
  result.leftItems = normalizeItems(slide.leftItems, 0, 5);
  result.rightItems = normalizeItems(slide.rightItems, 0, 5);
  result.chart = normalizeChart(slide.chart);

  const simpleLayout = () => result.items.length >= 2 ? "cards_icon" : "big_statement";
  const reflow = (layout, reason) => {
    result.layout = layout;
    result.reflowReason = reason;
  };
  const chartCitations = asArray(slide.chart?.citations).filter(Boolean);
  const hasChartEvidence = asArray(slide.citations).filter(Boolean).length > 0
    || chartCitations.length > 0;

  switch (result.layout) {
    case "cards_icon":
      if (result.items.length < 2) {
        reflow("big_statement", "cards require at least two substantive items");
      }
      break;
    case "process_flow":
    case "timeline_story":
      if (result.items.length < 2) {
        reflow("big_statement", "sequence requires at least two substantive stages");
      }
      break;
    case "matrix_2x2":
      if (result.items.length !== 4 || !result.xAxisLabel || !result.yAxisLabel) {
        reflow(simpleLayout(), "matrix requires four evidence-backed quadrants and named axes");
      }
      break;
    case "metrics_dashboard":
      if (result.items.length < 2 || result.items.some((item) => !item.value)) {
        reflow(simpleLayout(), "metrics require at least two explicit values");
      }
      break;
    case "comparison_dual":
      if (!result.leftItems.length || !result.rightItems.length) {
        result.items = [...result.rightItems, ...result.leftItems, ...result.items].slice(0, 6);
        reflow(simpleLayout(), "comparison requires evidence on both sides");
      }
      break;
    case "chart_focus":
    case "dashboard":
      if (!result.chart || !hasChartEvidence) {
        reflow(simpleLayout(), "chart requires numeric data and source citations");
      }
      break;
    case "photo_mosaic":
      if (result.imageQueries.length < 3) {
        reflow(simpleLayout(), "photo mosaic requires three API media queries");
      }
      break;
    case "case_study":
      if (result.items.length < 2 || result.imageQueries.length < 2) {
        reflow(simpleLayout(), "case study requires two substantive states and two API media queries");
      }
      break;
    case "table_clean":
      if (result.items.length < 2 || result.items.some((item) => !item.value)) {
        reflow(simpleLayout(), "table requires complete rows with values");
      }
      break;
    case "funnel":
      if (result.items.length < 2) {
        reflow("big_statement", "funnel requires at least two stages");
      }
      break;
    case "closing_actions":
      if (result.items.length < 2) {
        reflow("big_statement", "action close requires at least two concrete actions");
      }
      break;
    case "editorial_split":
      if (!result.imageQueries.length) {
        reflow(simpleLayout(), "editorial split requires API media");
      }
      break;
    case "cover_cinematic":
    case "quote_photo":
      if (!result.imageQueries.length) {
        reflow("big_statement", "photo layout requires API media");
      }
      break;
    default:
      break;
  }
  return result;
}

function normalizeSlide(raw, layout, topic, audience, index) {
  const base = fallbackSlide(layout, topic, audience, index);
  let footnote = truncate(raw?.footnote, 160);
  let equation = truncate(raw?.equation, 240);
  if (!equation && looksLikeEquation(footnote)) {
    equation = footnote;
    footnote = "";
  }
  let visualHierarchy = cleanText(pick(raw, "visualHierarchy", "visual_hierarchy")).toLowerCase();
  if (!["title", "equation", "visual", "content", "balanced"].includes(visualHierarchy)) {
    visualHierarchy = equation ? "equation" : "balanced";
  }
  if (equation) visualHierarchy = "equation";
  const rawSpacing = cleanText(raw?.spacing).toLowerCase();
  const spacing = ["compact", "balanced", "airy"].includes(rawSpacing) ? rawSpacing : "balanced";
  const slide = {
    ...base,
    ...(raw && typeof raw === "object" ? raw : {}),
    layout,
    mode: chooseMode(layout, raw?.mode, index),
    kicker: truncate(raw?.kicker || base.kicker, 36),
    title: truncate(raw?.title || base.title, 115),
    subtitle: truncate(raw?.subtitle || base.subtitle, 220),
    callout: truncate(raw?.callout, 160),
    quote: truncate(raw?.quote, 220),
    quoteSource: truncate(pick(raw, "quoteSource", "quote_source"), 80),
    footnote,
    equation,
    equationLabel: truncate(pick(raw, "equationLabel", "equation_label"), 100),
    visualHierarchy,
    spacing,
    speakerNotes: truncate(pick(raw, "speakerNotes", "speaker_notes"), 1400),
    imageQueries: asArray(pick(raw, "imageQueries", "image_queries")).map((x) => truncate(x, 90)).filter(Boolean).slice(0, 3),
    focalPoint: ["left", "center", "right", "top", "bottom"].includes(pick(raw, "focalPoint", "focal_point")) ? pick(raw, "focalPoint", "focal_point") : "center",
    leftTitle: truncate(pick(raw, "leftTitle", "left_title") || base.leftTitle, 72),
    rightTitle: truncate(pick(raw, "rightTitle", "right_title") || base.rightTitle, 72),
    xAxisLabel: truncate(pick(raw, "xAxisLabel", "x_axis_label"), 72),
    yAxisLabel: truncate(pick(raw, "yAxisLabel", "y_axis_label"), 72),
    citations: asArray(raw?.citations).map((value) => cleanText(value)).filter(Boolean),
  };
  slide.leftItems = pick(raw, "leftItems", "left_items");
  slide.rightItems = pick(raw, "rightItems", "right_items");
  return ensureLayoutData(slide, topic, audience, index);
}

export function normalizeDeck(raw, {
  topic,
  audience,
  count,
  presenterName = "",
  organization = "",
}) {
  const source = asArray(raw?.slides).filter((slide) => slide && typeof slide === "object");
  if (!source.length) throw new Error("Slide plan must contain at least one substantive slide");
  const resized = [...source];

  const layouts = resized.map((slide, index) => {
    if (LAYOUTS.includes(slide.layout)) return slide.layout;
    if (index === 0) return "cover_cinematic";
    if (index === resized.length - 1) return "closing_actions";
    return "big_statement";
  });

  const deck = {
    title: truncate(raw?.title || topic, 100),
    subtitle: truncate(raw?.subtitle || `ارائه برای ${audience}`, 180),
    presenterName: truncate(presenterName, 120),
    organization: truncate(organization, 120),
    theme: normalizeTheme(raw?.theme),
    slides: resized.map((slide, index) => normalizeSlide(slide, layouts[index], topic, audience, index)),
  };

  const warnings = [];
  deck.slides.forEach((slide, index) => {
    if (slide.title.length > 100) warnings.push(`عنوان اسلاید ${index + 1} طولانی است.`);
    if (slide.reflowReason) warnings.push(`Slide ${index + 1} reflowed: ${slide.reflowReason}.`);
  });
  return { deck, warnings };
}

export function selfTestNormalize() {
  const tests = [
    { raw: { slides: [{ layout: "big_statement", title: "تست" }] }, count: 1 },
    { raw: { slides: [{ layout: "cover_cinematic", title: "شروع" }, { layout: "closing_actions", title: "پایان" }] }, count: 2 },
    { raw: { slides: Array.from({ length: 20 }, () => ({ layout: "cards_icon", title: "تکراری" })) }, count: 9 },
  ];
  return tests.map((test) => normalizeDeck(test.raw, { topic: "تست", audience: "مدیران", count: test.count }).deck.slides.length === test.raw.slides.length);
}
