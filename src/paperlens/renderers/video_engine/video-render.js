import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { spawn, spawnSync } from "node:child_process";
import { Resvg } from "@resvg/resvg-js";
import { CONFIG } from "./config.js";
import { boundedMap, configuredWorkers, recommendedRenderWorkers } from "./render-runtime.js";
import { cleanText, hex, slugify, hash } from "./utils.js";
import { iconSvgBody } from "./icons.js";

const BASE_WIDTH = 1920;
const BASE_HEIGHT = 1080;
const DEFAULT_WIDTH = 1920;
const DEFAULT_HEIGHT = 1080;

export function videoDimensions(options = {}) {
  const resolution = String(options.resolution || "1080p").toLowerCase();
  const quality = resolution === "720p"
    ? { long: 1280, short: 720, preset: "fast", crf: 20 }
    : resolution === "1440p" || resolution === "2k"
      ? { long: 2560, short: 1440, preset: "medium", crf: 17 }
      : { long: 1920, short: 1080, preset: "medium", crf: 21 };
  const aspect = String(options.aspect_ratio || options.aspectRatio || "landscape").toLowerCase();
  if (aspect === "portrait") {
    return { width: quality.short, height: quality.long, preset: quality.preset, crf: quality.crf };
  }
  if (aspect === "square") {
    return { width: quality.short, height: quality.short, preset: quality.preset, crf: quality.crf };
  }
  return { width: quality.long, height: quality.short, preset: quality.preset, crf: quality.crf };
}
const DEFAULT_FPS = 30;


function graphemes(value) {
  const text = String(value ?? "");
  try {
    return [...new Intl.Segmenter("fa", { granularity: "grapheme" }).segment(text)].map((item) => item.segment);
  } catch {
    return Array.from(text);
  }
}

function revealWrappedLines(fullLines, visibleCount, cursorVisible = false) {
  let remaining = Math.max(0, Number(visibleCount || 0));
  const output = [];
  for (const line of fullLines) {
    const chars = graphemes(line);
    if (remaining <= 0) break;
    const taken = chars.slice(0, remaining).join("");
    output.push(taken);
    remaining -= chars.length;
    if (taken.length < line.length) break;
  }
  if (cursorVisible) {
    if (!output.length) output.push("│");
    else output[output.length - 1] = `${output[output.length - 1]}│`;
  }
  return output;
}

function escapeXml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function wrapText(value, maxChars = 34, maxLines = 3) {
  const words = cleanText(value).split(" ").filter(Boolean).flatMap((word) => {
    const chars = graphemes(word);
    if (chars.length <= maxChars) return [word];
    const chunks = [];
    for (let index = 0; index < chars.length; index += maxChars) {
      chunks.push(chars.slice(index, index + maxChars).join(""));
    }
    return chunks;
  });
  if (!words.length || maxLines < 1) return [];
  const lines = [];
  let current = "";
  let wordIndex = 0;
  for (; wordIndex < words.length;) {
    const word = words[wordIndex];
    const next = current ? `${current} ${word}` : word;
    if (graphemes(next).length <= maxChars) {
      current = next;
      wordIndex += 1;
    } else if (current) {
      lines.push(current);
      current = "";
      if (lines.length >= maxLines) break;
    }
  }
  if (current && lines.length < maxLines) lines.push(current);
  if (wordIndex < words.length && lines.length) {
    const last = graphemes(lines.at(-1).replace(/…$/, ""));
    lines[lines.length - 1] = `${last.slice(0, Math.max(1, maxChars - 1)).join("")}…`;
  }
  return lines;
}

async function embeddedFontCss() {
  let fileNames = [];
  if (CONFIG.fontFile && fsSync.existsSync(CONFIG.fontFile)) {
    fileNames = [path.basename(CONFIG.fontFile)];
  } else {
    try {
      const entries = await fs.readdir(CONFIG.fontDir, { withFileTypes: true });
      fileNames = entries
        .filter((entry) => entry.isFile() && /\.(?:ttf|otf)$/i.test(entry.name))
        .map((entry) => entry.name);
    } catch {
      return "";
    }
  }
  const weightFor = (fileName) => {
    const value = fileName.toLowerCase();
    if (value.includes("black") || value.includes("extrablack")) return 900;
    if (value.includes("extrabold")) return 800;
    if (value.includes("semibold")) return 600;
    if (value.includes("bold")) return 700;
    if (value.includes("medium")) return 500;
    if (value.includes("extralight")) return 200;
    if (value.includes("light")) return 300;
    if (value.includes("thin")) return 100;
    return 400;
  };
  const candidates = fileNames
    .map((fileName) => [fileName, weightFor(fileName)])
    .sort((left, right) => left[1] - right[1]);
  const rules = [];
  for (const [fileName, weight] of candidates) {
    const filePath = CONFIG.fontFile || path.join(CONFIG.fontDir, fileName);
    if (!fsSync.existsSync(filePath)) continue;
    const format = path.extname(filePath).toLowerCase() === ".otf" ? "opentype" : "truetype";
    rules.push(`@font-face{font-family:'Vazirmatn Embedded';src:url('${pathToFileURL(filePath).href}') format('${format}');font-weight:${weight};font-style:normal;}`);
  }
  return rules.join("");
}

function estimatedTextWidth(value, fontSize, letterSpacing = 0) {
  const chars = graphemes(value);
  const emWidth = chars.reduce((total, char) => {
    if (/\s/u.test(char)) return total + 0.32;
    if (/[\u0590-\u08ff]/u.test(char)) return total + 0.62;
    if (/[MW@%&]/u.test(char)) return total + 0.96;
    if (/[A-Z0-9]/u.test(char)) return total + 0.68;
    if (/[ilI|.,:;'!]/u.test(char)) return total + 0.3;
    return total + 0.55;
  }, 0);
  return emWidth * fontSize + Math.max(0, chars.length - 1) * letterSpacing;
}

function svgText(lines, { x, y, fontSize, lineHeight = 1.28, fill = "FFFFFF", weight = 700, anchor = "end", opacity = 1, letterSpacing = 0, maxWidth = 0 } = {}) {
  if (!lines?.length) return "";
  const step = Math.round(fontSize * lineHeight);
  return lines.map((line, index) => {
    const direction = /[\u0590-\u08ff]/u.test(line) ? "rtl" : "ltr";
    const lineY = y + (index * step);
    const safeWidth = Number(maxWidth) > 0
      ? Number(maxWidth)
      : anchor === "end" && Number(x) > 90
        ? Number(x) - 90
        : anchor === "middle" && Number(x) > 90 && Number(x) < 1830
          ? 2 * Math.min(Number(x) - 90, 1830 - Number(x))
          : 0;
    const fitted = safeWidth > 0 && estimatedTextWidth(line, fontSize, letterSpacing) > safeWidth
      ? ` textLength="${Math.round(safeWidth)}" lengthAdjust="spacingAndGlyphs"`
      : "";
    return `<text x="${x}" y="${lineY}"${fitted} text-anchor="${anchor}" direction="${direction}" unicode-bidi="plaintext" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma, Arial" font-size="${fontSize}" font-weight="${weight}" letter-spacing="${letterSpacing}" fill="#${fill}" opacity="${opacity}">${escapeXml(line)}</text>`;
  }).join("");
}

function color(theme, key, fallback) { return hex(theme?.[key], fallback); }

function bodyWrapForLayout(layout) {
  return ["split-panel", "glass-card", "data-bars", "comparison", "timeline"].includes(layout) ? 34 : 48;
}

function landscapeTextMetrics(layout, titleLineCount, bodyLineCount) {
  if (layout === "hero-cinematic") {
    const dense = titleLineCount >= 3 || bodyLineCount >= 2;
    return dense
      ? { titleY: 530, titleSize: 78, bodyY: 820, bodySize: 32 }
      : { titleY: 610, titleSize: 86, bodyY: 885, bodySize: 36 };
  }
  if (layout === "glass-card") {
    const dense = titleLineCount >= 3;
    return dense
      ? { titleY: 490, titleSize: 50, bodyY: 780, bodySize: 31 }
      : { titleY: 570, titleSize: 57, bodyY: 780, bodySize: 31 };
  }
  if (layout === "spotlight") {
    const dense = titleLineCount >= 3 || bodyLineCount >= 2;
    return dense
      ? { titleY: 500, titleSize: 66, bodyY: 770, bodySize: 31 }
      : { titleY: 560, titleSize: 76, bodyY: 790, bodySize: 34 };
  }
  if (layout === "editorial") {
    const dense = titleLineCount >= 3 || bodyLineCount >= 2;
    return dense
      ? { titleY: 525, titleSize: 64, bodyY: 800, bodySize: 32 }
      : { titleY: 610, titleSize: 74, bodyY: 835, bodySize: 34 };
  }
  if (layout === "minimal-type") {
    if (titleLineCount >= 3) return { titleY: 440, titleSize: 68, bodyY: 742, bodySize: 34 };
    if (titleLineCount === 2) return { titleY: 500, titleSize: 76, bodyY: 742, bodySize: 34 };
    return { titleY: 565, titleSize: 92, bodyY: 742, bodySize: 34 };
  }
  if (layout === "closing-statement") {
    const dense = titleLineCount >= 3;
    return dense
      ? { titleY: 500, titleSize: 68, ruleY: 710, bodyY: 795, bodySize: 34 }
      : { titleY: 585, titleSize: 80, ruleY: 735, bodyY: 825, bodySize: 34 };
  }
  if (layout === "quote") {
    const dense = titleLineCount >= 3 || bodyLineCount >= 2;
    return dense
      ? { titleY: 500, titleSize: 66, bodyY: 790, bodySize: 31 }
      : { titleY: 590, titleSize: 76, bodyY: 820, bodySize: 33 };
  }
  return {};
}

function iconMarkup(name, { x, y, size = 72, stroke = "FFFFFF", background = "FFFFFF", backgroundOpacity = 0.12, strokeWidth = 1.7 } = {}) {
  const scale = size / 24;
  return `<g transform="translate(${x} ${y})">
    <rect x="0" y="0" width="${size}" height="${size}" rx="${Math.round(size * 0.28)}" fill="#${background}" fill-opacity="${backgroundOpacity}"/>
    <g transform="translate(${Math.round(size * 0.18)} ${Math.round(size * 0.18)}) scale(${scale * 0.64})" fill="none" stroke="#${stroke}" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${iconSvgBody(name)}</g>
  </g>`;
}

function commonProgress(index, total, accent, minimal = false) {
  const progress = Math.max(0.04, (index + 1) / total);
  if (minimal) {
    return `<circle cx="1810" cy="1008" r="25" fill="#FFFFFF" fill-opacity="0.07"/>
      <text x="1810" y="1016" text-anchor="middle" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="18" fill="#FFFFFF" fill-opacity="0.8">${String(index + 1).padStart(2, "0")}</text>`;
  }
  return `<text x="90" y="1015" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="20" fill="#FFFFFF" fill-opacity="0.62">${String(index + 1).padStart(2, "0")} / ${String(total).padStart(2, "0")}</text>
    <rect x="90" y="1038" width="1740" height="4" rx="2" fill="#FFFFFF" fill-opacity="0.12"/>
    <rect x="90" y="1038" width="${Math.round(1740 * progress)}" height="4" rx="2" fill="#${accent}"/>`;
}

function metadataLines(scene) {
  const presenter = scene.presenter && typeof scene.presenter === "object" ? scene.presenter : {};
  const presenterLine = [cleanText(presenter.name), cleanText(presenter.organization)].filter(Boolean).join(" · ");
  const citations = scene.showCitations && Array.isArray(scene.citations)
    ? scene.citations.map(cleanText).filter(Boolean).slice(0, 4).join(" · ")
    : "";
  return { presenterLine, citations };
}

function landscapeMetadataMarkup(scene, muted, minimal = false) {
  const { presenterLine, citations } = metadataLines(scene);
  const presenterY = minimal ? 925 : 955;
  const citationY = minimal ? 958 : 988;
  return `${presenterLine ? svgText(wrapText(presenterLine, 72, 1), { x: 1830, y: presenterY, fontSize: 22, fill: muted, weight: 550 }) : ""}
    ${citations ? svgText(wrapText(citations, 96, 1), { x: 1830, y: citationY, fontSize: 17, fill: muted, weight: 450, opacity: 0.72 }) : ""}`;
}

function responsiveProgress(index, total, accent, width, height) {
  const margin = Math.round(width * 0.075);
  const barWidth = width - margin * 2;
  const y = height - Math.round(height * 0.045);
  const progress = Math.max(0.04, (index + 1) / total);
  return `<text x="${margin}" y="${y - 18}" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="20" fill="#FFFFFF" fill-opacity="0.66">${String(index + 1).padStart(2, "0")} / ${String(total).padStart(2, "0")}</text>
    <rect x="${margin}" y="${y}" width="${barWidth}" height="5" rx="2.5" fill="#FFFFFF" fill-opacity="0.14"/>
    <rect x="${margin}" y="${y}" width="${Math.round(barWidth * progress)}" height="5" rx="2.5" fill="#${accent}"/>`;
}

function responsiveOverlaySvg(scene, media, index, total, theme, fontCss, renderState, width, height) {
  const accent = color(theme, "accent", "F6B73C");
  const primary = color(theme, "primary", "2457E6");
  const background = color(theme, "background", "091321");
  const surface = color(theme, "surface", "101D31");
  const muted = color(theme, "muted", "D8E1EE");
  const portrait = height > width * 1.2;
  const margin = Math.round(width * 0.075);
  const right = width - margin;
  const safeTextWidth = width - margin * 2;
  const visual = scene.visual || {};
  const spacing = visual.spacing || "balanced";
  const emphasis = visual.emphasis || "balanced";
  const titleBase = portrait ? 70 : 62;
  const titleSize = Math.round(titleBase * (emphasis === "title" ? 1.14 : emphasis === "body" ? 0.9 : 1));
  const bodySize = Math.round((portrait ? 34 : 30) * (emphasis === "body" ? 1.12 : 1));
  const maxChars = portrait ? (spacing === "compact" ? 24 : 19) : (spacing === "compact" ? 31 : 25);
  const fullTitleLines = wrapText(scene.title, maxChars, portrait ? 4 : 3);
  const titleLines = renderState.hideTitle ? [] : fullTitleLines;
  const bodyLines = wrapText(cleanText(scene.onScreenText || scene.subtitle), portrait ? 31 : 42, 3);
  const kicker = cleanText(scene.kicker || `بخش ${index + 1}`);
  const titleY = portrait ? (visual.layout === "hero-cinematic" ? 560 : 360) : 315;
  const bodyY = titleY + titleSize * Math.max(1, fullTitleLines.length) * 1.2 + (spacing === "airy" ? 105 : 70);
  const structural = visual.intent === "data" ? chartMarkup(visual.chart, accent, primary)
    : visual.intent === "process" ? processMarkup(visual.steps, accent, primary)
      : visual.intent === "comparison" ? comparisonMarkup(visual.comparison, accent, primary)
        : visual.intent === "timeline" ? timelineMarkup(visual.timeline, accent, primary)
          : "";
  const structuralScale = width / BASE_WIDTH;
  const structuralY = portrait ? Math.round(height * 0.43) : Math.round(height * 0.34);
  const { presenterLine, citations } = metadataLines(scene);
  const metadataY = height - Math.round(height * (portrait ? 0.105 : 0.145));
  const metadataChars = Math.max(28, Math.floor((width - margin * 2) / 14));
  const card = spacing === "compact"
    ? `<rect x="${margin - 22}" y="${titleY - 120}" width="${width - (margin - 22) * 2}" height="${Math.min(height * 0.43, bodyY - titleY + 360)}" rx="42" fill="#${surface}" fill-opacity="0.82" stroke="#FFFFFF" stroke-opacity="0.12"/>`
    : "";
  return `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
      <style>${fontCss}</style>
      <defs>
        <linearGradient id="responsiveShade" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.10"/><stop offset="48%" stop-color="#${background}" stop-opacity="0.62"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.97"/></linearGradient>
        <radialGradient id="responsiveGlow"><stop offset="0%" stop-color="#${primary}" stop-opacity="0.34"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.02"/></radialGradient>
      </defs>
      ${media?.kind === "card" ? `<rect width="${width}" height="${height}" fill="#${background}"/>` : ""}
      <rect width="${width}" height="${height}" fill="url(#responsiveShade)"/>
      <circle cx="${Math.round(width * 0.18)}" cy="${Math.round(height * 0.16)}" r="${Math.round(width * 0.42)}" fill="url(#responsiveGlow)"/>
      ${card}
      ${iconMarkup(visual.icon || "sparkles", { x: right - 82, y: titleY - 155, size: 76, stroke: background, background: accent, backgroundOpacity: 1 })}
      <rect x="${right - Math.round(width * 0.37)}" y="${titleY - 52}" width="${Math.round(width * 0.37)}" height="5" rx="2.5" fill="#${accent}"/>
      ${svgText([kicker], { x: right, y: titleY, fontSize: 25, fill: accent, weight: 800, maxWidth: safeTextWidth })}
      ${svgText(titleLines, { x: right, y: titleY + 92, fontSize: titleSize, lineHeight: 1.18, weight: 900, maxWidth: safeTextWidth })}
      ${svgText(bodyLines, { x: right, y: bodyY, fontSize: bodySize, lineHeight: 1.48, fill: muted, weight: 480, maxWidth: safeTextWidth })}
      ${structural ? `<g transform="translate(0 ${structuralY}) scale(${structuralScale})">${structural}</g>` : ""}
      ${presenterLine ? svgText(wrapText(presenterLine, metadataChars, 1), { x: right, y: metadataY, fontSize: 24, fill: muted, weight: 600, maxWidth: safeTextWidth }) : ""}
      ${citations ? svgText(wrapText(citations, metadataChars, 1), { x: right, y: metadataY + 36, fontSize: 18, fill: muted, weight: 450, opacity: 0.72, maxWidth: safeTextWidth }) : ""}
      ${responsiveProgress(index, total, accent, width, height)}
    </svg>`;
}

function decorativeDots(seed, accent, primary) {
  const values = seed.split("").map((char) => char.charCodeAt(0));
  return values.slice(0, 7).map((value, index) => {
    const x = 90 + ((value * 97 + index * 211) % 1740);
    const y = 80 + ((value * 53 + index * 131) % 870);
    const r = 3 + (value % 8);
    const fill = index % 2 ? accent : primary;
    const opacity = (0.08 + (value % 12) / 100).toFixed(2);
    return `<circle cx="${x}" cy="${y}" r="${r}" fill="#${fill}" fill-opacity="${opacity}"/>`;
  }).join("");
}

function chartMarkup(chart, accent, primary) {
  const labels = chart?.labels || ["شروع", "آزمایش", "رشد"];
  const values = chart?.values || [34, 62, 88];
  const maxH = 260;
  const baseY = 815;
  const barW = 120;
  const gap = 78;
  const startX = 185;
  return `<g>
    <text x="190" y="470" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="21" fill="#FFFFFF" fill-opacity="0.48">نمایش مفهومی روند</text>
    <line x1="170" y1="${baseY}" x2="760" y2="${baseY}" stroke="#FFFFFF" stroke-opacity="0.16" stroke-width="2"/>
    ${values.map((value, index) => {
      const h = Math.round((Math.max(8, Math.min(100, value)) / 100) * maxH);
      const x = startX + index * (barW + gap);
      const fill = index === values.length - 1 ? accent : primary;
      return `<rect x="${x}" y="${baseY - h}" width="${barW}" height="${h}" rx="26" fill="#${fill}" fill-opacity="${index === values.length - 1 ? 0.95 : 0.66}"/>
        <circle cx="${x + barW / 2}" cy="${baseY - h}" r="7" fill="#FFFFFF"/>
        ${svgText(wrapText(labels[index] || "", 14, 2), { x: x + barW / 2, y: baseY + 42, fontSize: 19, lineHeight: 1.15, weight: 500, anchor: "middle", opacity: 0.78, maxWidth: 160 })}`;
    }).join("")}
  </g>`;
}

function processMarkup(steps, accent, primary) {
  const values = steps?.length >= 3 ? steps.slice(0, 3) : [];
  if (!values.length) return "";
  const xs = [390, 960, 1530];
  return `<g>
    <line x1="390" y1="690" x2="1530" y2="690" stroke="#FFFFFF" stroke-opacity="0.18" stroke-width="5" stroke-linecap="round"/>
    ${values.map((step, index) => `<g>
      <circle cx="${xs[index]}" cy="690" r="76" fill="#${index === 1 ? accent : primary}" fill-opacity="${index === 1 ? 0.95 : 0.68}"/>
      <circle cx="${xs[index]}" cy="690" r="94" fill="none" stroke="#FFFFFF" stroke-opacity="0.13" stroke-width="2"/>
      <text x="${xs[index]}" y="704" text-anchor="middle" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="34" font-weight="900" fill="#FFFFFF">${index + 1}</text>
      ${svgText(wrapText(step, 18, 2), { x: xs[index], y: 820, fontSize: 26, lineHeight: 1.18, weight: 650, anchor: "middle", maxWidth: 450 })}
    </g>`).join("")}
  </g>`;
}

function comparisonMarkup(items, accent, primary) {
  const values = items?.length >= 2 ? items.slice(0, 2) : [];
  if (!values.length) return "";
  return `<g>
    <rect x="170" y="565" width="720" height="245" rx="42" fill="#FFFFFF" fill-opacity="0.07" stroke="#FFFFFF" stroke-opacity="0.12"/>
    <rect x="1030" y="565" width="720" height="245" rx="42" fill="#${primary}" fill-opacity="0.30" stroke="#${accent}" stroke-opacity="0.35"/>
    <circle cx="960" cy="688" r="53" fill="#${accent}"/><text x="960" y="702" text-anchor="middle" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="28" font-weight="900" fill="#07111F">VS</text>
    ${svgText(wrapText(values[0], 28, 2), { x: 530, y: 690, fontSize: 34, lineHeight: 1.2, weight: 750, anchor: "middle", maxWidth: 600 })}
    ${svgText(wrapText(values[1], 28, 2), { x: 1390, y: 690, fontSize: 34, lineHeight: 1.2, weight: 850, anchor: "middle", maxWidth: 600 })}
  </g>`;
}

function timelineMarkup(items, accent, primary) {
  const values = items?.length >= 3 ? items.slice(0, 3) : [];
  if (!values.length) return "";
  const xs = [360, 960, 1560];
  return `<g>
    <line x1="360" y1="705" x2="1560" y2="705" stroke="#FFFFFF" stroke-opacity="0.20" stroke-width="6" stroke-linecap="round"/>
    ${values.map((item, i) => `<g>
      <circle cx="${xs[i]}" cy="705" r="54" fill="#${i === 1 ? accent : primary}" fill-opacity="${i === 1 ? 1 : 0.72}"/>
      <circle cx="${xs[i]}" cy="705" r="78" fill="none" stroke="#FFFFFF" stroke-opacity="0.12" stroke-width="2"/>
      <text x="${xs[i]}" y="718" text-anchor="middle" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="28" font-weight="900" fill="#FFFFFF">${i + 1}</text>
      ${svgText(wrapText(item, 20, 2), { x: xs[i], y: 825, fontSize: 28, lineHeight: 1.18, weight: 700, anchor: "middle", maxWidth: 450 })}
    </g>`).join("")}
  </g>`;
}

function overlaySvg(scene, media, index, total, theme, fontCss, renderState = {}, viewport = {}) {
  const width = Number(viewport.width || BASE_WIDTH);
  const height = Number(viewport.height || BASE_HEIGHT);
  if (height > width * 0.72) {
    return responsiveOverlaySvg(scene, media, index, total, theme, fontCss, renderState, width, height);
  }
  const accent = color(theme, "accent", "F6B73C");
  const primary = color(theme, "primary", "2457E6");
  const secondary = color(theme, "secondary", "0F766E");
  const background = color(theme, "background", "091321");
  const surface = color(theme, "surface", "101D31");
  const muted = color(theme, "muted", "D8E1EE");
  const layout = scene.visual?.layout || "lower-third";
  const icon = scene.visual?.icon || "sparkles";
  const seed = scene.visual?.decorativeSeed || "abcdef";
  const kind = media?.kind || "card";
  const isCard = kind === "card";
  const titleWrap = {
    "hero-cinematic": [31, 3],
    "split-panel": [23, 3],
    "glass-card": [23, 3],
    spotlight: [31, 3],
    editorial: [29, 3],
    "minimal-type": [27, 3],
    "data-bars": [22, 3],
    process: [31, 2],
    quote: [29, 3],
    comparison: [29, 2],
    timeline: [31, 2],
    "closing-statement": [31, 3],
    "lower-third": [34, 2],
  }[layout] || [32, 2];
  const bodyWrap = bodyWrapForLayout(layout);
  const fullTitleLines = wrapText(scene.title, titleWrap[0], titleWrap[1]);
  const titleLines = renderState.hideTitle
    ? []
    : Number.isFinite(renderState.visibleGraphemes)
      ? revealWrappedLines(fullTitleLines, renderState.visibleGraphemes, Boolean(renderState.cursorVisible))
      : fullTitleLines;
  const bodyLines = wrapText(cleanText(scene.onScreenText || scene.subtitle), bodyWrap, 2);
  const typography = landscapeTextMetrics(layout, fullTitleLines.length, bodyLines.length);
  const kicker = cleanText(scene.kicker || `بخش ${index + 1}`);
  const baseCard = isCard
    ? `<rect width="1920" height="1080" fill="url(#fullCard)"/>${decorativeDots(seed, accent, primary)}`
    : "";
  let content = "";
  let minimalProgress = false;

  if (layout === "hero-cinematic") {
    content = `${baseCard}
      <rect width="1920" height="1080" fill="url(#heroShade)"/>
      <text x="118" y="335" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="255" font-weight="900" fill="#FFFFFF" fill-opacity="0.055">${String(index + 1).padStart(2, "0")}</text>
      ${iconMarkup(icon, { x: 1570, y: 128, size: 92, stroke: background, background: accent, backgroundOpacity: 1, strokeWidth: 1.8 })}
      <rect x="1245" y="255" width="410" height="5" rx="2.5" fill="#${accent}"/>
      ${svgText([kicker], { x: 1655, y: 320, fontSize: 28, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1655, y: typography.titleY, fontSize: typography.titleSize, lineHeight: 1.18, weight: 900 })}
      ${svgText(bodyLines, { x: 1655, y: typography.bodyY, fontSize: typography.bodySize, lineHeight: 1.42, fill: muted, weight: 450 })}`;
  } else if (layout === "split-panel") {
    content = `${baseCard}
      <rect x="1010" width="910" height="1080" fill="url(#sidePanel)"/>
      <rect x="1010" y="0" width="7" height="1080" fill="#${accent}" fill-opacity="0.82"/>
      ${iconMarkup(icon, { x: 1565, y: 150, size: 86, stroke: accent, background: "FFFFFF", backgroundOpacity: 0.08 })}
      ${svgText([kicker], { x: 1680, y: 265, fontSize: 25, fill: accent, weight: 750 })}
      ${svgText(titleLines, { x: 1680, y: 500, fontSize: 62, lineHeight: 1.19, weight: 900 })}
      ${svgText(bodyLines, { x: 1680, y: 745, fontSize: 33, lineHeight: 1.48, fill: muted, weight: 450 })}`;
  } else if (layout === "glass-card") {
    content = `${baseCard}
      <rect width="1920" height="1080" fill="url(#softShade)"/>
      <rect x="940" y="170" width="790" height="700" rx="54" fill="#${surface}" fill-opacity="0.78" stroke="#FFFFFF" stroke-opacity="0.13" stroke-width="2"/>
      <circle cx="1625" cy="276" r="88" fill="#${accent}" fill-opacity="0.15"/>
      ${iconMarkup(icon, { x: 1568, y: 219, size: 84, stroke: accent, background: "FFFFFF", backgroundOpacity: 0.07 })}
      ${svgText([kicker], { x: 1625, y: 390, fontSize: 25, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1625, y: typography.titleY, fontSize: typography.titleSize, lineHeight: 1.19, weight: 900 })}
      <rect x="1170" y="695" width="455" height="4" rx="2" fill="#${accent}"/>
      ${svgText(bodyLines, { x: 1625, y: typography.bodyY, fontSize: typography.bodySize, lineHeight: 1.48, fill: muted, weight: 450 })}`;
  } else if (layout === "spotlight") {
    content = `${baseCard}
      <rect width="1920" height="1080" fill="url(#centerShade)"/>
      <circle cx="960" cy="510" r="390" fill="#${primary}" fill-opacity="0.15"/>
      <circle cx="960" cy="510" r="280" fill="#${accent}" fill-opacity="0.07"/>
      ${iconMarkup(icon, { x: 918, y: 190, size: 84, stroke: background, background: accent, backgroundOpacity: 1 })}
      ${svgText([kicker], { x: 960, y: 350, fontSize: 27, fill: accent, weight: 800, anchor: "middle" })}
      ${svgText(titleLines, { x: 960, y: typography.titleY, fontSize: typography.titleSize, lineHeight: 1.20, weight: 900, anchor: "middle" })}
      ${svgText(bodyLines, { x: 960, y: typography.bodyY, fontSize: typography.bodySize, lineHeight: 1.46, fill: muted, weight: 450, anchor: "middle" })}`;
  } else if (layout === "editorial") {
    content = `${baseCard || `<rect width="1920" height="1080" fill="url(#editorialShade)"/>`}
      <rect x="120" y="115" width="1680" height="850" rx="36" fill="#${background}" fill-opacity="${isCard ? 0.52 : 0.78}" stroke="#FFFFFF" stroke-opacity="0.1"/>
      <text x="220" y="355" font-family="Vazirmatn Embedded, ${escapeXml(CONFIG.font)}, Tahoma" font-size="245" font-weight="900" fill="#${accent}" fill-opacity="0.10">${String(index + 1).padStart(2, "0")}</text>
      ${iconMarkup(icon, { x: 1510, y: 185, size: 88, stroke: background, background: accent, backgroundOpacity: 1 })}
      ${svgText([kicker], { x: 1605, y: 375, fontSize: 27, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1605, y: typography.titleY, fontSize: typography.titleSize, lineHeight: 1.18, weight: 900 })}
      ${svgText(bodyLines, { x: 1605, y: typography.bodyY, fontSize: typography.bodySize, lineHeight: 1.44, fill: muted, weight: 450 })}`;
  } else if (layout === "minimal-type") {
    minimalProgress = true;
    content = `${baseCard}
      <rect width="1920" height="1080" fill="url(#minimalShade)"/>
      <rect x="1535" y="190" width="150" height="6" rx="3" fill="#${accent}"/>
      ${svgText([kicker], { x: 1685, y: 260, fontSize: 25, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1685, y: typography.titleY, fontSize: typography.titleSize, lineHeight: 1.16, weight: 900 })}
      <rect x="1660" y="690" width="12" height="72" rx="6" fill="#${accent}"/>
      ${svgText(bodyLines, { x: 1630, y: typography.bodyY, fontSize: typography.bodySize, lineHeight: 1.42, fill: muted, weight: 450 })}`;
  } else if (layout === "data-bars") {
    content = `${baseCard || `<rect width="1920" height="1080" fill="url(#dataShade)"/>`}
      <rect x="1050" y="125" width="705" height="830" rx="52" fill="#${surface}" fill-opacity="0.78" stroke="#FFFFFF" stroke-opacity="0.12"/>
      ${iconMarkup(icon, { x: 1560, y: 190, size: 82, stroke: accent, background: "FFFFFF", backgroundOpacity: 0.07 })}
      ${svgText([kicker], { x: 1645, y: 335, fontSize: 25, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1645, y: 550, fontSize: 57, lineHeight: 1.19, weight: 900 })}
      ${svgText(bodyLines, { x: 1645, y: 770, fontSize: 31, lineHeight: 1.46, fill: muted, weight: 450 })}
      ${chartMarkup(scene.visual?.chart, accent, primary)}`;
  } else if (layout === "process") {
    content = `${baseCard || `<rect width="1920" height="1080" fill="url(#processShade)"/>`}
      ${svgText([kicker], { x: 1690, y: 160, fontSize: 25, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1690, y: 310, fontSize: 66, lineHeight: 1.18, weight: 900 })}
      ${svgText(bodyLines, { x: 1690, y: 450, fontSize: 30, lineHeight: 1.45, fill: muted, weight: 450 })}
      ${processMarkup(scene.visual?.steps, accent, primary)}`;
  } else if (layout === "comparison") {
    content = `${baseCard || `<rect width="1920" height="1080" fill="url(#processShade)"/>`}
      ${svgText([kicker], { x: 1690, y: 165, fontSize: 25, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1690, y: 330, fontSize: 66, lineHeight: 1.18, weight: 900 })}
      ${svgText(bodyLines, { x: 1690, y: 455, fontSize: 30, lineHeight: 1.45, fill: muted, weight: 450 })}
      ${comparisonMarkup(scene.visual?.comparison, accent, primary)}`;
  } else if (layout === "timeline") {
    content = `${baseCard || `<rect width="1920" height="1080" fill="url(#processShade)"/>`}
      ${svgText([kicker], { x: 1690, y: 165, fontSize: 25, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1690, y: 330, fontSize: 66, lineHeight: 1.18, weight: 900 })}
      ${svgText(bodyLines, { x: 1690, y: 455, fontSize: 30, lineHeight: 1.45, fill: muted, weight: 450 })}
      ${timelineMarkup(scene.visual?.timeline, accent, primary)}`;
  } else if (layout === "quote") {
    minimalProgress = true;
    content = `${baseCard}
      <rect width="1920" height="1080" fill="url(#quoteShade)"/>
      <text x="290" y="460" font-family="Georgia, serif" font-size="420" fill="#${accent}" fill-opacity="0.20">“</text>
      ${iconMarkup(icon, { x: 1510, y: 170, size: 82, stroke: accent, background: "FFFFFF", backgroundOpacity: 0.07 })}
      ${svgText([kicker], { x: 1600, y: 320, fontSize: 26, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1600, y: typography.titleY, fontSize: typography.titleSize, lineHeight: 1.18, weight: 900 })}
      ${svgText(bodyLines, { x: 1600, y: typography.bodyY, fontSize: typography.bodySize, lineHeight: 1.48, fill: muted, weight: 450 })}`;
  } else if (layout === "closing-statement") {
    minimalProgress = true;
    content = `${baseCard}
      <rect width="1920" height="1080" fill="url(#closingShade)"/>
      <circle cx="960" cy="505" r="335" fill="#${primary}" fill-opacity="0.13"/>
      ${iconMarkup(icon, { x: 918, y: 178, size: 84, stroke: background, background: accent, backgroundOpacity: 1 })}
      ${svgText([kicker], { x: 960, y: 345, fontSize: 27, fill: accent, weight: 800, anchor: "middle" })}
      ${svgText(titleLines, { x: 960, y: typography.titleY, fontSize: typography.titleSize, lineHeight: 1.16, weight: 900, anchor: "middle" })}
      <rect x="760" y="${typography.ruleY}" width="400" height="5" rx="2.5" fill="#${accent}"/>
      ${svgText(bodyLines, { x: 960, y: typography.bodyY, fontSize: typography.bodySize, lineHeight: 1.45, fill: muted, weight: 450, anchor: "middle" })}`;
  } else {
    content = `${baseCard}
      <rect width="1920" height="1080" fill="url(#lowerShade)"/>
      ${iconMarkup(icon, { x: 1575, y: 540, size: 78, stroke: background, background: accent, backgroundOpacity: 1 })}
      ${svgText([kicker], { x: 1680, y: 650, fontSize: 24, fill: accent, weight: 800 })}
      ${svgText(titleLines, { x: 1680, y: 735, fontSize: 60, lineHeight: 1.16, weight: 900 })}
      ${svgText(bodyLines, { x: 1680, y: 865, fontSize: 28, lineHeight: 1.38, fill: muted, weight: 450 })}`;
  }

  return Buffer.from(`
    <svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid meet">
      <defs>
        <style>${fontCss}</style>
        <linearGradient id="fullCard" x1="0" x2="1" y1="0" y2="1"><stop offset="0%" stop-color="#${background}"/><stop offset="52%" stop-color="#${surface}"/><stop offset="100%" stop-color="#${primary}"/></linearGradient>
        <linearGradient id="heroShade" x1="0" x2="0.85" y1="0" y2="1"><stop offset="0%" stop-color="#02050A" stop-opacity="0.05"/><stop offset="55%" stop-color="#06101D" stop-opacity="0.28"/><stop offset="100%" stop-color="#030A13" stop-opacity="0.95"/></linearGradient>
        <linearGradient id="sidePanel" x1="0" x2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.32"/><stop offset="38%" stop-color="#${background}" stop-opacity="0.82"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.98"/></linearGradient>
        <linearGradient id="softShade" x1="0" x2="1" y1="0" y2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.04"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.72"/></linearGradient>
        <radialGradient id="centerShade"><stop offset="0%" stop-color="#${background}" stop-opacity="0.18"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.91"/></radialGradient>
        <linearGradient id="editorialShade" x1="0" x2="1"><stop offset="0%" stop-color="#${secondary}" stop-opacity="0.10"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.92"/></linearGradient>
        <linearGradient id="minimalShade" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.18"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.94"/></linearGradient>
        <linearGradient id="dataShade" x1="0" x2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.88"/><stop offset="100%" stop-color="#${primary}" stop-opacity="0.35"/></linearGradient>
        <linearGradient id="processShade" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.90"/><stop offset="100%" stop-color="#${surface}" stop-opacity="0.94"/></linearGradient>
        <linearGradient id="quoteShade" x1="0" x2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.78"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.96"/></linearGradient>
        <radialGradient id="closingShade"><stop offset="0%" stop-color="#${primary}" stop-opacity="0.34"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.96"/></radialGradient>
        <linearGradient id="lowerShade" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#${background}" stop-opacity="0.02"/><stop offset="55%" stop-color="#${background}" stop-opacity="0.18"/><stop offset="100%" stop-color="#${background}" stop-opacity="0.94"/></linearGradient>
      </defs>
      ${content}
      ${landscapeMetadataMarkup(scene, muted, minimalProgress)}
      ${commonProgress(index, total, accent, minimalProgress)}
    </svg>
  `);
}

function ffmpegExists() {
  const result = spawnSync("ffmpeg", ["-version"], { stdio: "ignore", windowsHide: true });
  return result.status === 0;
}

export function probeVideoQuality(
  file,
  { width, height, duration, expectAudio = false, tolerance = 0.2 } = {},
) {
  const result = spawnSync("ffprobe", [
    "-v", "error",
    "-show_streams",
    "-show_format",
    "-of", "json",
    file,
  ], { encoding: "utf8", windowsHide: true });
  if (result.status !== 0) {
    throw new Error(`کنترل کیفیت MP4 شکست خورد: ${(result.stderr || "ffprobe failed").trim()}`);
  }
  const payload = JSON.parse(result.stdout || "{}");
  const streams = Array.isArray(payload.streams) ? payload.streams : [];
  const video = streams.find((stream) => stream.codec_type === "video");
  const audio = streams.find((stream) => stream.codec_type === "audio");
  if (!video) throw new Error("کنترل کیفیت MP4: جریان ویدیو وجود ندارد.");
  if (Number(video.width) !== Number(width) || Number(video.height) !== Number(height)) {
    throw new Error(`کنترل کیفیت MP4: ابعاد ${video.width}x${video.height} با ${width}x${height} برابر نیست.`);
  }
  const measuredDuration = Number(payload.format?.duration || video.duration || 0);
  if (!Number.isFinite(measuredDuration) || Math.abs(measuredDuration - Number(duration)) > tolerance) {
    throw new Error(`کنترل کیفیت MP4: مدت ${measuredDuration.toFixed(3)} با زمان هدف ${Number(duration).toFixed(3)} برابر نیست.`);
  }
  if (expectAudio && !audio) throw new Error("کنترل کیفیت MP4: جریان صوت مورد انتظار وجود ندارد.");
  return {
    passed: true,
    durationSeconds: Number(measuredDuration.toFixed(3)),
    width: Number(video.width),
    height: Number(video.height),
    videoCodec: String(video.codec_name || ""),
    audioCodec: audio ? String(audio.codec_name || "") : "",
    hasAudio: Boolean(audio),
  };
}

function runProcess(command, args, cwd, { quiet = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      windowsHide: true,
      stdio: quiet ? ["ignore", "ignore", "pipe"] : ["ignore", "inherit", "inherit"],
    });
    let stderr = "";
    if (quiet && child.stderr) child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", (error) => reject(error));
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} با کد ${code} متوقف شد.${stderr ? `\n${stderr.slice(-2500)}` : ""}`));
    });
  });
}

async function rasterizeSvg(svgPath, pngPath, width, height) {
  let fontFiles = [];
  if (CONFIG.fontFile && fsSync.existsSync(CONFIG.fontFile)) {
    fontFiles = [CONFIG.fontFile];
  } else if (fsSync.existsSync(CONFIG.fontDir)) {
    fontFiles = (await fs.readdir(CONFIG.fontDir))
      .filter((name) => /\.(?:ttf|otf|woff2?)$/i.test(name))
      .map((name) => path.join(CONFIG.fontDir, name));
  }
  const renderer = new Resvg(await fs.readFile(svgPath), {
    fitTo: { mode: "width", value: width },
    font: { fontFiles, loadSystemFonts: false, defaultFontFamily: CONFIG.font },
  });
  const rendered = renderer.render();
  if (rendered.width !== width || rendered.height !== height) {
    throw new Error(`SVG raster size ${rendered.width}x${rendered.height}; expected ${width}x${height}.`);
  }
  await fs.writeFile(pngPath, rendered.asPng());
  return "resvg";
}

function deterministicUnit(seed, index) {
  const value = Number.parseInt(hash(`${seed}|${index}`).slice(0, 8), 16);
  return Number.isFinite(value) ? value / 0xffffffff : 0.5;
}

export function buildTypewriterSchedule(text, typing = {}, totalDuration = 6, seed = "typing") {
  const chars = graphemes(cleanText(text));
  if (!chars.length) return [{ visibleGraphemes: 0, cursorVisible: false, duration: totalDuration }];
  const maxKeyframes = 80;
  const chunkSize = Math.max(1, Math.ceil(chars.length / maxKeyframes));
  const cps = Math.max(4.5, Math.min(11.5, Number(typing.cps || 7.8)));
  const variance = Math.max(0, Math.min(0.35, Number(typing.variance ?? 0.2)));
  const initialDelay = Math.max(0.08, Number(typing.initialDelay || 0.16));
  const cursorEnabled = typing.cursor !== false;
  const cursorHold = cursorEnabled ? Math.max(0.15, Math.min(0.55, Number(typing.cursorHold || 0.34))) : 0;
  const raw = [];
  let visible = 0;
  while (visible < chars.length) {
    const next = Math.min(chars.length, visible + chunkSize);
    const typed = chars.slice(visible, next).join("");
    const last = chars[next - 1] || "";
    const jitter = 1 - variance + deterministicUnit(seed, next) * variance * 2;
    let duration = ((next - visible) / cps) * jitter;
    if (/\s/.test(last)) duration += 0.055;
    if (/[،؛:,.!?؟]/.test(last)) duration += 0.18 + deterministicUnit(seed, next + 91) * 0.14;
    raw.push({ visibleGraphemes: next, cursorVisible: cursorEnabled, duration: Math.max(0.06, duration), typed });
    visible = next;
  }
  const maxTypingTime = Math.max(1.4, totalDuration - initialDelay - cursorHold - 0.45);
  const rawTotal = raw.reduce((sum, frame) => sum + frame.duration, 0);
  const scale = rawTotal > maxTypingTime ? maxTypingTime / rawTotal : 1;
  const frames = [{ visibleGraphemes: 0, cursorVisible: false, duration: initialDelay }];
  frames.push(...raw.map((frame) => ({ ...frame, duration: frame.duration * scale })));
  // Keep the caret briefly after the last character, then remove it once. No end blinking.
  if (cursorHold > 0) frames.push({ visibleGraphemes: chars.length, cursorVisible: true, duration: cursorHold });
  const used = frames.reduce((sum, frame) => sum + frame.duration, 0);
  frames.push({ visibleGraphemes: chars.length, cursorVisible: false, duration: Math.max(0.25, totalDuration - used) });
  const finalTotal = frames.reduce((sum, frame) => sum + frame.duration, 0);
  if (finalTotal > totalDuration) {
    const excess = finalTotal - totalDuration;
    frames[frames.length - 1].duration = Math.max(0.08, frames[frames.length - 1].duration - excess);
  }
  return frames;
}

function transitionDurationFor(scene) {
  return Math.max(0.28, Math.min(0.85, Number(scene?.visual?.transitionDuration || 0.52)));
}

export function computeVideoTiming(plan, options = {}) {
  const duration = Math.max(30, Math.min(1_200, Number(options.duration || 180)));
  const count = Math.max(1, plan.scenes.length);
  const transitionDurations = plan.scenes.slice(0, -1).map(transitionDurationFor);
  const requested = plan.scenes.map((scene) => Number(scene.durationSeconds ?? scene.duration_seconds ?? 0));
  const safeWeights = requested.map((value) => Number.isFinite(value) && value > 0 ? value : 1);
  const weightTotal = safeWeights.reduce((sum, value) => sum + value, 0);
  const nominalDurations = safeWeights.map((value) => duration * value / weightTotal);
  // Each outgoing transition extends its source segment.  The XFade overlap
  // subtracts exactly that extension, preserving the agent's scene windows.
  const segmentDurations = nominalDurations.map((value, index) => value + (transitionDurations[index] || 0));
  const sceneWindows = [];
  let startSeconds = 0;
  for (let index = 0; index < count; index += 1) {
    const nominalDuration = nominalDurations[index];
    const segmentDuration = segmentDurations[index];
    sceneWindows.push({
      scene: index + 1,
      startSeconds: Number(startSeconds.toFixed(3)),
      endSeconds: Number(Math.min(duration, startSeconds + segmentDuration).toFixed(3)),
      nominalStartSeconds: Number(startSeconds.toFixed(3)),
      nominalEndSeconds: Number((startSeconds + nominalDuration).toFixed(3)),
      transitionIn: index ? transitionDurations[index - 1] : 0,
      transitionOut: index < count - 1 ? transitionDurations[index] : 0,
    });
    startSeconds += nominalDuration;
  }
  return {
    duration,
    segmentDuration: duration / count,
    segmentDurations,
    nominalDurations,
    transitionDurations,
    sceneWindows,
  };
}

function assTime(seconds) {
  const safe = Math.max(0, Number(seconds || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = Math.floor(safe % 60);
  const centis = Math.floor((safe - Math.floor(safe)) * 100);
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(centis).padStart(2, "0")}`;
}

function escapeAssText(value) {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/\{/g, "\\{")
    .replace(/\}/g, "\\}")
    .replace(/\r?\n/g, "\\N");
}

function typewriterPlacement(scene, width = BASE_WIDTH, height = BASE_HEIGHT) {
  const visual = scene.visual || {};
  if (height > width * 0.72) {
    const portrait = height > width * 1.2;
    const emphasis = visual.emphasis || "balanced";
    const size = Math.round((portrait ? 70 : 62) * (emphasis === "title" ? 1.14 : 1));
    const margin = Math.round(width * 0.075);
    return {
      x: width - Math.round(width * 0.075),
      y: portrait ? (visual.layout === "hero-cinematic" ? 652 : 452) : 407,
      size,
      alignment: 6,
      maxChars: portrait ? 19 : 25,
      maxLines: portrait ? 4 : 3,
      maxWidth: width - margin * 2,
    };
  }
  const layout = scene.visual?.layout || "lower-third";
  const map = {
    "hero-cinematic": { x: 1655, y: 610, size: 86, alignment: 6, maxChars: 31, maxLines: 3 },
    "split-panel": { x: 1680, y: 500, size: 62, alignment: 6, maxChars: 23, maxLines: 3 },
    "glass-card": { x: 1625, y: 570, size: 57, alignment: 6, maxChars: 23, maxLines: 3 },
    spotlight: { x: 960, y: 555, size: 86, alignment: 5, maxChars: 31, maxLines: 3 },
    editorial: { x: 1660, y: 515, size: 72, alignment: 6, maxChars: 29, maxLines: 3 },
    "minimal-type": { x: 1685, y: 565, size: 92, alignment: 6, maxChars: 27, maxLines: 3 },
    "data-bars": { x: 1690, y: 480, size: 59, alignment: 6, maxChars: 22, maxLines: 3 },
    process: { x: 960, y: 360, size: 66, alignment: 5, maxChars: 31, maxLines: 2 },
    quote: { x: 1600, y: 540, size: 76, alignment: 6, maxChars: 29, maxLines: 3 },
    comparison: { x: 960, y: 390, size: 66, alignment: 5, maxChars: 29, maxLines: 2 },
    timeline: { x: 960, y: 390, size: 66, alignment: 5, maxChars: 31, maxLines: 2 },
    "closing-statement": { x: 960, y: 545, size: 84, alignment: 5, maxChars: 31, maxLines: 3 },
    "lower-third": { x: 1700, y: 770, size: 58, alignment: 6, maxChars: 34, maxLines: 2 },
  };
  const placement = map[layout] || map["lower-third"];
  const titleLineCount = wrapText(scene.title, placement.maxChars, placement.maxLines).length;
  const bodyLineCount = wrapText(
    cleanText(scene.onScreenText || scene.subtitle),
    bodyWrapForLayout(layout),
    2,
  ).length;
  const typography = landscapeTextMetrics(layout, titleLineCount, bodyLineCount);
  const adjusted = {
    ...placement,
    y: typography.titleY || placement.y,
    size: typography.titleSize || placement.size,
  };
  return {
    ...adjusted,
    maxWidth: adjusted.alignment === 6 ? adjusted.x - 90 : 1740,
  };
}

function revealedWrappedAss(title, placement, visibleGraphemes, cursorVisible) {
  const fullLines = wrapText(title, placement.maxChars, placement.maxLines);
  const lines = revealWrappedLines(fullLines, visibleGraphemes, cursorVisible);
  return escapeAssText(lines.join("\n"));
}

async function createTypewriterAss(scene, index, targetDir, segmentDuration, width, height) {
  const id = String(index + 1).padStart(2, "0");
  const schedule = buildTypewriterSchedule(scene.title, scene.visual?.typing, segmentDuration, scene.visual?.decorativeSeed || id);
  const placement = typewriterPlacement(scene, width, height);
  const fullTitleLines = wrapText(scene.title, placement.maxChars, placement.maxLines);
  const widestTitle = Math.max(0, ...fullTitleLines.map((line) => estimatedTextWidth(line, placement.size)));
  const scaleX = widestTitle > placement.maxWidth
    ? Math.max(1, Math.floor((placement.maxWidth / widestTitle) * 100))
    : 100;
  const assAlignment = placement.alignment === 6 ? 9 : placement.alignment === 5 ? 8 : placement.alignment;
  const assY = placement.y - Math.round(placement.size * 0.72);
  const fontName = cleanText(CONFIG.font || "Vazirmatn") || "Vazirmatn";
  const assPath = path.join(targetDir, `typewriter-${id}.ass`);
  const header = `[Script Info]\nScriptType: v4.00+\nPlayResX: ${width}\nPlayResY: ${height}\nScaledBorderAndShadow: yes\nWrapStyle: 2\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Typewriter,${fontName},${placement.size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,${assAlignment},0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n`;
  const lines = [];
  let cursor = 0;
  for (const frame of schedule) {
    const start = cursor;
    const end = Math.min(segmentDuration, cursor + Math.max(0.02, Number(frame.duration || 0.02)));
    cursor = end;
    if (frame.visibleGraphemes > 0 || frame.cursorVisible) {
      const text = revealedWrappedAss(scene.title, placement, frame.visibleGraphemes, frame.cursorVisible);
      const tags = `{\\an${assAlignment}\\pos(${placement.x},${assY})\\fscx${scaleX}\\q2\\blur0.25}`;
      lines.push(`Dialogue: 0,${assTime(start)},${assTime(end)},Typewriter,,0,0,0,,${tags}${text}`);
    }
    if (cursor >= segmentDuration) break;
  }
  await fs.writeFile(assPath, `${header}${lines.join("\n")}\n`, "utf8");
  return { assPath, schedule, placement };
}

export async function renderSceneOverlays(plan, media, targetDir, options = {}) {
  if (!ffmpegExists()) throw new Error("FFmpeg در PATH پیدا نشد.");
  const width = Number(options.width || DEFAULT_WIDTH);
  const height = Number(options.height || DEFAULT_HEIGHT);
  const timing = computeVideoTiming(plan, options);
  const svgDir = path.join(targetDir, "overlay-svg");
  const overlayDir = path.join(targetDir, "overlays");
  await fs.mkdir(svgDir, { recursive: true });
  await fs.mkdir(overlayDir, { recursive: true });
  const fontCss = await embeddedFontCss();
  if (!fontCss) console.log("هشدار: فایل‌های Vazirmatn در assets/fonts پیدا نشد؛ از فونت نصب‌شده سیستم استفاده می‌شود.");
  const started = performance.now();
  const renderers = new Set();
  const itemDurations = [];
  const overlayWorkers = configuredWorkers(options.overlayWorkers, recommendedRenderWorkers());
  const overlays = await boundedMap(plan.scenes, overlayWorkers, async (scene, index) => {
    const itemStarted = performance.now();
    const id = String(index + 1).padStart(2, "0");
    const typingEnabled = Boolean(scene.visual?.typing?.enabled) && graphemes(cleanText(scene.title)).length >= 4;
    const svgPath = path.join(svgDir, `overlay-${id}.svg`);
    const pngPath = path.join(overlayDir, `overlay-${id}.png`);
    await fs.writeFile(svgPath, overlaySvg(scene, media[index], index, plan.scenes.length, plan.theme, fontCss, typingEnabled ? { hideTitle: true } : {}, { width, height }));
    const renderer = await rasterizeSvg(svgPath, pngPath, width, height, targetDir);
    renderers.add(renderer);
    if (typingEnabled) {
      const typing = await createTypewriterAss(
        scene,
        index,
        overlayDir,
        timing.segmentDurations[index],
        width,
        height,
      );
      itemDurations[index] = performance.now() - itemStarted;
      return { type: "image", path: pngPath, renderer, typewriter: true, schedule: typing.schedule, assPath: typing.assPath };
    } else {
      itemDurations[index] = performance.now() - itemStarted;
      return { type: "image", path: pngPath, renderer, typewriter: false, schedule: [], assPath: "" };
    }
  });
  overlays.metrics = {
    duration_ms: Math.round(performance.now() - started),
    item_duration_ms: itemDurations.map(Math.round),
    worker_count: overlayWorkers,
    rasterizer: "resvg",
    browser_renders: 0,
  };
  return overlays;
}

function cropBase(focalPoint, axis, index) {
  if (axis === "x") {
    if (focalPoint === "left") return "0";
    if (focalPoint === "right") return "iw-ow";
    return index % 2 ? "(iw-ow)*0.58" : "(iw-ow)*0.42";
  }
  if (focalPoint === "top") return "0";
  if (focalPoint === "bottom") return "ih-oh";
  return "(ih-oh)/2";
}

function gradeFilter(treatment) {
  if (treatment === "cinematic") return "eq=saturation=0.88:contrast=1.12:brightness=-0.025:gamma=0.98";
  if (treatment === "vibrant") return "eq=saturation=1.12:contrast=1.055:brightness=-0.008";
  if (treatment === "mono") return "hue=s=0,eq=contrast=1.12:brightness=-0.015";
  if (treatment === "warm") return "eq=saturation=1.035:contrast=1.04:gamma_r=1.035:gamma_b=0.97";
  if (treatment === "cool") return "eq=saturation=0.98:contrast=1.05:gamma_b=1.035:gamma_r=0.985";
  if (treatment === "soft") return "eq=saturation=0.92:contrast=0.99:brightness=0.005";
  return "eq=saturation=1.0:contrast=1.025:brightness=-0.008";
}

function overlayAnimation(scene, width, height, fps, duration, isTypewriter) {
  const motion = scene.visual?.motion || "fade";
  const base = `[1:v]fps=${fps},format=rgba,scale=${width}:${height}:flags=lanczos,trim=duration=${duration.toFixed(3)},setpts=PTS-STARTPTS`;
  const fadeDuration = isTypewriter ? 0.12 : 0.34;
  const fade = `fade=t=in:st=0:d=${fadeDuration}:alpha=1`;
  if (motion === "type-reveal") return { filters: `${base},${fade}[ov]`, x: "0", y: "0" };
  if (motion === "reveal-up") return { filters: `${base},${fade}[ov]`, x: "0", y: `H*0.045*(1-min(1,t/0.62))` };
  if (motion === "slide-up") return { filters: `${base},${fade}[ov]`, x: "0", y: `H*0.04*(1-min(1,t/0.58))` };
  if (motion === "slide-left") return { filters: `${base},${fade}[ov]`, x: `W*0.04*(1-min(1,t/0.58))`, y: "0" };
  if (motion === "float-right") return { filters: `${base},${fade}[ov]`, x: `-W*0.03*(1-min(1,t/0.72))`, y: "0" };
  if (motion === "scale-in") return { filters: `${base},${fade}[ov]`, x: "0", y: `H*0.022*(1-min(1,t/0.55))` };
  return { filters: `${base},${fade}[ov]`, x: "0", y: "0" };
}

function escapeFfmpegFilterPath(value) {
  return String(value ?? "")
    .replace(/\\/g, "/")
    .replace(/:/g, "\\:")
    .replace(/'/g, "\\'")
    .replace(/,/g, "\\,")
    .replace(/\[/g, "\\[")
    .replace(/\]/g, "\\]");
}

async function renderSegment(scene, media, overlaySpec, segmentPath, options) {
  const {
    width, height, fps, segmentDuration, intermediateCrf, intermediatePreset, index, total,
  } = options;
  const scaleW = Math.ceil(width * 1.09 / 2) * 2;
  const scaleH = Math.ceil(height * 1.09 / 2) * 2;
  const baseX = cropBase(scene.focalPoint, "x", index);
  const baseY = cropBase(scene.focalPoint, "y", index);
  const driftX = index % 2 ? "-(iw-ow)*0.055*sin(t*0.42)" : "+(iw-ow)*0.055*sin(t*0.42)";
  const driftY = index % 3 === 0 ? "+(ih-oh)*0.035*sin(t*0.36)" : "";
  const grade = gradeFilter(scene.visual?.treatment);
  const animation = overlayAnimation(scene, width, height, fps, segmentDuration, Boolean(overlaySpec.typewriter));
  let composite = `[bg][ov]overlay=x='${animation.x}':y='${animation.y}':format=auto,fps=${fps},setpts=PTS-STARTPTS[composed]`;
  let finalChain = "[composed]";
  const tailFilters = [];
  if (overlaySpec.assPath) {
    const assPath = escapeFfmpegFilterPath(overlaySpec.assPath);
    const fontsDir = escapeFfmpegFilterPath(CONFIG.fontDir);
    tailFilters.push(`${finalChain}ass=filename='${assPath}':fontsdir='${fontsDir}':alpha=1[typed]`);
    finalChain = "[typed]";
  }
  let finish = `${finalChain}fps=${fps},setpts=PTS-STARTPTS`;
  if (index === 0) finish += ",fade=t=in:st=0:d=0.40:color=black";
  if (index === total - 1) finish += `,fade=t=out:st=${Math.max(0, segmentDuration - 0.46).toFixed(3)}:d=0.46:color=black`;
  finish += ",format=yuv420p[out]";
  tailFilters.push(finish);
  const filterTail = `${animation.filters};${composite};${tailFilters.join(";")}`;
  const overlayInput = ["-loop", "1", "-framerate", String(fps), "-i", overlaySpec.path];
  let args;

  if (media.kind === "video" && media.path) {
    const maxStart = Math.max(0, Number(media.duration || 0) - segmentDuration - 0.35);
    const start = maxStart > 0 ? ((index * 1.93) % maxStart).toFixed(3) : "0";
    const background = `[0:v]fps=${fps},scale=${scaleW}:${scaleH}:force_original_aspect_ratio=increase:flags=lanczos,crop=${width}:${height}:x='min(iw-ow,max(0,${baseX}${driftX}))':y='min(ih-oh,max(0,${baseY}${driftY}))',${grade},unsharp=5:5:0.28:5:5:0.0,trim=duration=${segmentDuration.toFixed(3)},setpts=PTS-STARTPTS[bg]`;
    args = ["-y", "-loglevel", "error", "-stream_loop", "-1", "-ss", start, "-i", media.path, ...overlayInput, "-filter_complex", `${background};${filterTail}`, "-map", "[out]", "-t", segmentDuration.toFixed(3)];
  } else if (media.kind === "photo" && media.path) {
    const driftPhotoX = index % 2 === 0
      ? `(iw-ow)*(0.18+0.62*min(1,t/${segmentDuration.toFixed(3)}))`
      : `(iw-ow)*(0.82-0.62*min(1,t/${segmentDuration.toFixed(3)}))`;
    const background = `[0:v]fps=${fps},scale=${scaleW}:${scaleH}:force_original_aspect_ratio=increase:flags=lanczos,crop=${width}:${height}:x='min(iw-ow,max(0,${driftPhotoX}))':y='(ih-oh)/2',${grade},unsharp=5:5:0.32:5:5:0.0,trim=duration=${segmentDuration.toFixed(3)},setpts=PTS-STARTPTS[bg]`;
    args = ["-y", "-loglevel", "error", "-loop", "1", "-framerate", String(fps), "-i", media.path, ...overlayInput, "-filter_complex", `${background};${filterTail}`, "-map", "[out]", "-t", segmentDuration.toFixed(3)];
  } else {
    const bgColor = color(options.theme, "background", "091321");
    const background = `[0:v]fps=${fps},format=yuv420p,setpts=PTS-STARTPTS[bg]`;
    args = ["-y", "-loglevel", "error", "-f", "lavfi", "-i", `color=c=0x${bgColor}:s=${width}x${height}:r=${fps}:d=${segmentDuration.toFixed(3)}`, ...overlayInput, "-filter_complex", `${background};${filterTail}`, "-map", "[out]", "-t", segmentDuration.toFixed(3)];
  }
  args.push(
    "-an", "-c:v", "libx264", "-preset", intermediatePreset, "-crf", String(intermediateCrf), "-profile:v", "high", "-level:v", "4.2",
    "-pix_fmt", "yuv420p", "-r", String(fps), "-g", String(fps * 2), "-bf", "3",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart", segmentPath,
  );
  await runProcess("ffmpeg", args, path.dirname(segmentPath));
}

const XFADE_TRANSITIONS = new Set([
  "fade", "dissolve", "smoothleft", "smoothright", "smoothup", "smoothdown", "hblur", "zoomin",
  "circleopen", "circleclose", "revealleft", "revealright", "revealup", "revealdown", "fadewhite", "fadeblack",
]);
function safeTransition(value) { return XFADE_TRANSITIONS.has(value) ? value : "fade"; }

async function renderXfadeComposition(segments, outputPath, plan, timing, options) {
  const { fps, width, height, preset, crf } = options;
  const args = ["-y", "-loglevel", "error"];
  segments.forEach((segment) => args.push("-i", segment));
  const filters = segments.map((_, index) => `[${index}:v]settb=AVTB,setpts=PTS-STARTPTS,fps=${fps},scale=${width}:${height}:flags=lanczos,format=yuv420p[v${index}]`);
  let current = "v0";
  for (let index = 0; index < segments.length - 1; index += 1) {
    const transitionDuration = timing.transitionDurations[index];
    const offset = timing.nominalDurations
      .slice(0, index + 1)
      .reduce((sum, value) => sum + value, 0);
    const next = `mix${index + 1}`;
    filters.push(`[${current}][v${index + 1}]xfade=transition=${safeTransition(plan.scenes[index]?.visual?.transition)}:duration=${transitionDuration.toFixed(3)}:offset=${offset.toFixed(3)}[${next}]`);
    current = next;
  }
  filters.push(`[${current}]trim=duration=${timing.duration.toFixed(3)},setpts=PTS-STARTPTS,format=yuv420p[out]`);
  args.push(
    "-filter_complex", filters.join(";"), "-map", "[out]", "-an", "-t", timing.duration.toFixed(3),
    "-c:v", "libx264", "-preset", preset, "-crf", String(crf), "-profile:v", "high", "-level:v", "4.2",
    "-pix_fmt", "yuv420p", "-r", String(fps), "-g", String(fps * 2), "-bf", "3",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart", outputPath,
  );
  await runProcess("ffmpeg", args, path.dirname(outputPath));
}

export async function renderVideo(media, overlays, outputPath, plan, options = {}) {
  if (!ffmpegExists()) throw new Error("FFmpeg در PATH پیدا نشد.");
  const timing = computeVideoTiming(plan, options);
  const width = Number(options.width || DEFAULT_WIDTH);
  const height = Number(options.height || DEFAULT_HEIGHT);
  const fps = Number(options.fps || DEFAULT_FPS);
  const segmentsDir = path.join(path.dirname(outputPath), "video-segments");
  await fs.mkdir(segmentsDir, { recursive: true });
  const segmentDurationsMs = [];
  const preset = options.preset || "medium";
  const crf = Math.max(14, Math.min(28, Number(options.crf || 18)));
  const intermediatePreset = "ultrafast";
  const intermediateCrf = 1;

  const renderWorkers = configuredWorkers(options.renderWorkers, recommendedRenderWorkers());
  const segmentWallStarted = performance.now();
  const segments = await boundedMap(media, renderWorkers, async (mediaItem, index) => {
    const segmentStarted = performance.now();
    const segmentPath = path.join(segmentsDir, `segment-${String(index + 1).padStart(2, "0")}.mp4`);
    await renderSegment(plan.scenes[index], mediaItem, overlays[index], segmentPath, {
      width,
      height,
      fps,
      segmentDuration: timing.segmentDurations[index],
      index,
      total: media.length,
      intermediateCrf,
      intermediatePreset,
      theme: plan.theme,
    });
    segmentDurationsMs[index] = Math.round(performance.now() - segmentStarted);
    return segmentPath;
  });
  const segmentWallMs = Math.round(performance.now() - segmentWallStarted);

  let transitionMode = "xfade-composite";
  const compositionStarted = performance.now();
  try {
    await renderXfadeComposition(segments, outputPath, plan, timing, { fps, width, height, preset, crf });
  } catch (error) {
    console.log(`هشدار: ترنزیشن XFade روی این FFmpeg اجرا نشد؛ اتصال امن جایگزین می‌شود. (${error.message})`);
    const concatPath = path.join(segmentsDir, "concat.txt");
    const lines = segments.map((segmentPath) => `file '${segmentPath.replace(/\\/g, "/").replace(/'/g, "'\\''")}'`);
    await fs.writeFile(concatPath, `${lines.join("\n")}\n`, "utf8");
    await runProcess("ffmpeg", [
      "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", concatPath,
      "-t", timing.duration.toFixed(3), "-c:v", "libx264", "-preset", preset, "-crf", String(crf), "-an", "-movflags", "+faststart", outputPath,
    ], path.dirname(outputPath));
    transitionMode = "safe-concat-fallback";
  }

  return {
    ...timing, fps, width, height, style: plan.direction?.style || "auto", seed: plan.direction?.seed || "",
    transitionMode,
    metrics: {
      segment_duration_ms: segmentDurationsMs,
      segment_wall_ms: segmentWallMs,
      segment_cpu_ms: segmentDurationsMs.reduce((sum, value) => sum + value, 0),
      composition_ms: Math.round(performance.now() - compositionStarted),
      worker_count: renderWorkers,
      recommended_worker_count: recommendedRenderWorkers(),
      intermediate: { codec: "libx264", preset: intermediatePreset, crf: intermediateCrf },
    },
  };
}

function timestamp(seconds) {
  const totalMs = Math.max(0, Math.round(seconds * 1000));
  const hours = Math.floor(totalMs / 3_600_000);
  const minutes = Math.floor((totalMs % 3_600_000) / 60_000);
  const secs = Math.floor((totalMs % 60_000) / 1000);
  const ms = totalMs % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
}
function clock(seconds) { return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`; }

export async function writeVideoContentFiles(plan, media, targetDir, timing, audio = {}) {
  const base = slugify(plan.title || "video");
  const nominalVisible = timing.duration / plan.scenes.length;
  const voiceoverPath = path.join(targetDir, `${base}-voiceover.txt`);
  const contentPath = path.join(targetDir, `${base}-content.md`);
  const srtPath = path.join(targetDir, `${base}-subtitles.srt`);
  const timelinePath = path.join(targetDir, `${base}-timeline.json`);
  const creditsPath = path.join(targetDir, `${base}-media-credits.txt`);
  const voiceover = [
    plan.title,
    "",
    ...plan.scenes.flatMap((scene, index) => {
      const window = timing.sceneWindows?.[index] || {
        nominalStartSeconds: index * nominalVisible,
        nominalEndSeconds: (index + 1) * nominalVisible,
      };
      return [
        `[${clock(window.nominalStartSeconds)} تا ${clock(window.nominalEndSeconds)}]`,
        scene.narration,
        "",
      ];
    }),
  ];
  const audioDescription = audio.music?.status === "ready"
    ? `نسخه بی‌صدا و نسخه دارای موسیقی Jamendo تولید شده است${audio.finalVideoPath ? "؛ نسخه نهایی با نریشن و Ducking نیز تولید شده است" : ""}`
    : audio.finalVideoPath
      ? "نسخه بی‌صدا و نسخه نهایی دارای نریشن تولید شده است؛ موسیقی در دسترس نبود یا غیرفعال بود"
      : "نسخه بی‌صدا تولید شده است";
  const md = [
    `# ${plan.title}`, "",
    `- مدت: ${timing.duration} ثانیه`,
    `- کیفیت: ${timing.width}×${timing.height} با ${timing.fps}fps`,
    `- حالت کارگردانی: ${plan.direction?.mode || "autonomous"}`,
    `- پروفایل محتوا: ${plan.direction?.profile || "general"}`,
    `- استایل: ${plan.direction?.styleLabel || plan.direction?.style || "Auto"}`,
    `- انرژی: ${plan.direction?.energy || "Auto"}`,
    `- سیاست رسانه: ${plan.direction?.mediaPolicy || "video-first"}`,
    `- ترنزیشن: ${timing.transitionMode || "xfade"}`,
    `- Seed: ${plan.direction?.seed || "—"}`,
    `- صدا: ${audioDescription}`, "",
  ];
  if (audio.music?.status === "ready") {
    md.push(`## موسیقی پس‌زمینه`, "", `- قطعه: ${audio.music.selected.title}`, `- هنرمند: ${audio.music.selected.artist}`, `- مجوز: ${audio.music.selected.licenseUrl}`, `- منبع: ${audio.music.selected.sourceUrl}`, "");
  }
  const srt = [];
  const timeline = [];
  const credits = [];
  plan.scenes.forEach((scene, index) => {
    const window = timing.sceneWindows?.[index] || {
      startSeconds: index * nominalVisible,
      endSeconds: (index + 1) * nominalVisible,
      nominalStartSeconds: index * nominalVisible,
      nominalEndSeconds: (index + 1) * nominalVisible,
      transitionIn: 0,
      transitionOut: 0,
    };
    const start = window.nominalStartSeconds;
    const end = window.nominalEndSeconds;
    const asset = media[index] || { kind: "card" };
    const visual = scene.visual || {};
    md.push(
      `## صحنه ${index + 1} — ${clock(start)} تا ${clock(end)}`, "",
      `**نوع رسانه:** ${asset.kind === "video" ? "ویدیوی واقعی" : asset.kind === "photo" ? "عکس متحرک" : "موشن‌اسلاید طراحی‌شده"}`, "",
      `**علت انتخاب رسانه:** ${asset.fallbackReason || "—"}`, "",
      `**پرس‌وجوی منتخب:** ${asset.selectedQuery || scene.mediaQuery || "—"}`, "",
      `**هدف بصری:** ${visual.intent || "footage"}`, "",
      `**قالب بصری:** ${visual.layout || "—"}`, "",
      `**افکت ورود:** ${visual.motion || "—"}`, "",
      `**تایپ واقعی:** ${visual.typing?.enabled ? "فعال" : "غیرفعال"}`, "",
      `**ترنزیشن خروجی:** ${visual.transition || "—"} (${Number(visual.transitionDuration || 0).toFixed(2)}s)`, "",
      `**آیکون:** ${visual.icon || "—"}`, "",
      `**عنوان:** ${scene.title}`, "",
      `**متن روی تصویر:** ${scene.onScreenText || scene.subtitle || "—"}`, "",
      `**متن گویندگی:** ${scene.narration}`, "",
    );
    srt.push(String(index + 1), `${timestamp(start)} --> ${timestamp(Math.max(start + 0.2, end - 0.08))}`, cleanText(scene.narration), "");
    if (asset.credit) credits.push(`صحنه ${index + 1}: ${asset.credit}${asset.sourceUrl ? ` — ${asset.sourceUrl}` : ""}`);
    timeline.push({
      scene: index + 1,
      startSeconds: window.startSeconds,
      endSeconds: window.endSeconds,
      narrationStartSeconds: window.nominalStartSeconds,
      narrationEndSeconds: window.nominalEndSeconds,
      transitionInSeconds: window.transitionIn,
      transitionOutSeconds: window.transitionOut,
      mediaType: asset.kind,
      mediaPath: asset.path ? path.basename(asset.path) : "",
      mediaCredit: asset.credit || "",
      mediaSourceUrl: asset.sourceUrl || "",
      mediaSelectedQuery: asset.selectedQuery || "",
      mediaFallbackReason: asset.fallbackReason || "",
      title: scene.title,
      onScreenText: scene.onScreenText || scene.subtitle || "",
      narration: scene.narration,
      mediaQueries: scene.mediaQueries || [scene.mediaQuery || scene.imageQuery || ""],
      visual: {
        intent: visual.intent || "",
        layout: visual.layout || "",
        motion: visual.motion || "",
        transition: visual.transition || "",
        transitionDuration: visual.transitionDuration || 0,
        treatment: visual.treatment || "",
        icon: visual.icon || "",
        typing: visual.typing || null,
        chart: visual.chart || null,
        steps: visual.steps || null,
        comparison: visual.comparison || null,
        timeline: visual.timeline || null,
      },
    });
  });
  const timelineDocument = {
    title: plan.title,
    durationSeconds: timing.duration,
    fps: timing.fps,
    resolution: `${timing.width}x${timing.height}`,
    silentVideo: audio.silentVideoPath ? path.basename(audio.silentVideoPath) : "",
    musicVideo: audio.musicVideoPath ? path.basename(audio.musicVideoPath) : "",
    finalVideo: audio.finalVideoPath ? path.basename(audio.finalVideoPath) : "",
    audio: {
      backgroundMusic: audio.music?.status === "ready",
      musicProvider: audio.music?.provider || "",
      musicPlanPath: audio.music?.diagnosticsPath
        ? path.basename(audio.music.diagnosticsPath)
        : "",
      voiceoverMixed: Boolean(audio.finalVideoPath),
    },
    direction: plan.direction || {},
    scenes: timeline,
  };
  await Promise.all([
    fs.writeFile(voiceoverPath, `${voiceover.join("\n").trim()}\n`, "utf8"),
    fs.writeFile(contentPath, `${md.join("\n").trim()}\n`, "utf8"),
    fs.writeFile(srtPath, srt.join("\n"), "utf8"),
    fs.writeFile(creditsPath, `${credits.join("\n").trim()}\n`, "utf8"),
    fs.writeFile(timelinePath, `${JSON.stringify(timelineDocument, null, 2)}\n`, "utf8"),
  ]);
  return { voiceoverPath, contentPath, srtPath, timelinePath, creditsPath };
}
