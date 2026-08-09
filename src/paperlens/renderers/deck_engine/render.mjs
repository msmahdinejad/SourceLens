import pptxgen from "pptxgenjs";
import { CONFIG, W, H } from "./config.mjs";
import { iconData } from "./icons.mjs";
import { clamp, withHash } from "./utils.mjs";

let SHAPE;
let PPTX;

function dropShadow() {
  return { type: "outer", color: "000000", opacity: 0.16, blur: 2, angle: 45, offset: 1.1 };
}

function bidiSafeText(value) {
  const raw = String(value || "");
  if (!/[\u0600-\u06ff]/u.test(raw) || !/[A-Za-z]/u.test(raw)) return raw;
  return raw.replace(/[A-Za-z][A-Za-z0-9._/+:-]*/gu, (token) => `\u200E${token}\u200E`);
}

function modeTheme(theme, mode) {
  if (mode === "dark") return { bg: theme.primary, surface: "1E293B", surface2: "27364A", text: "F8FAFC", muted: "CBD5E1", accent: theme.accent, accent2: theme.accent2 };
  if (mode === "accent") return { bg: theme.secondary, surface: "FFFFFF", surface2: theme.accent2, text: "FFFFFF", muted: "D9F3EF", accent: theme.accent, accent2: theme.accent2 };
  if (mode === "photo") return { bg: theme.primary, surface: "FFFFFF", surface2: "FFFFFF", text: "FFFFFF", muted: "E2E8F0", accent: theme.accent, accent2: theme.accent2 };
  return { bg: theme.background, surface: theme.surface, surface2: "E9EEF5", text: theme.text, muted: theme.muted, accent: theme.accent, accent2: theme.accent2 };
}

function text(slide, value, options = {}) {
  const raw = String(value || "");
  const hasPersian = /[\u0600-\u06ff]/u.test(raw);
  slide.addText(options.bidiSafe === false ? raw : bidiSafeText(raw), {
    x: options.x ?? 0,
    y: options.y ?? 0,
    w: options.w ?? 1,
    h: options.h ?? 0.4,
    fontFace: options.fontFace || CONFIG.font,
    lang: options.lang || (hasPersian ? "fa-IR" : "en-US"),
    fontSize: options.fontSize ?? 16,
    bold: Boolean(options.bold),
    color: options.color || "111827",
    align: options.align || (hasPersian ? "right" : "left"),
    valign: options.valign || "mid",
    rtlMode: options.rtlMode ?? hasPersian,
    margin: options.margin ?? 0,
    breakLine: false,
    fit: options.fit,
    paraSpaceAfterPt: options.paraSpaceAfterPt ?? 0,
    lineSpacingMultiple: options.lineSpacingMultiple,
    isTextBox: true,
    transparency: options.transparency,
    italic: options.italic,
    charSpacing: options.charSpacing,
    rotate: options.rotate,
  });
}

function semanticStyle(data = {}) {
  const hierarchy = ["title", "equation", "visual", "content", "balanced"]
    .includes(data.visualHierarchy) ? data.visualHierarchy : "balanced";
  const spacing = ["compact", "balanced", "airy"].includes(data.spacing)
    ? data.spacing : "balanced";
  return {
    hierarchy,
    spacing,
    titleDelta: hierarchy === "title" ? 3 : ["visual", "content"].includes(hierarchy) ? -1 : 0,
    gapDelta: spacing === "airy" ? 0.12 : spacing === "compact" ? -0.08 : 0,
  };
}

function equationParts(data = {}) {
  const raw = String(data.equation || "").trim();
  let label = String(data.equationLabel || "").trim();
  let expression = raw;
  if (!label) {
    const match = raw.match(/^([^:：]{1,60})[:：]\s*(.+)$/u);
    if (match && /[\u0600-\u06ff]/u.test(match[1])) {
      [, label, expression] = match;
    }
  }
  return { label, expression };
}

const EQUATION_COMMANDS = new Map([
  ["alpha", "α"], ["beta", "β"], ["gamma", "γ"], ["delta", "δ"],
  ["epsilon", "ε"], ["theta", "θ"], ["lambda", "λ"], ["mu", "μ"],
  ["pi", "π"], ["rho", "ρ"], ["sigma", "σ"], ["phi", "φ"], ["omega", "ω"],
  ["Gamma", "Γ"], ["Delta", "Δ"], ["Theta", "Θ"], ["Lambda", "Λ"],
  ["Pi", "Π"], ["Sigma", "Σ"], ["Phi", "Φ"], ["Omega", "Ω"],
  ["sum", "∑"], ["prod", "∏"], ["int", "∫"], ["infty", "∞"],
  ["times", "×"], ["cdot", "·"], ["pm", "±"], ["mp", "∓"],
  ["le", "≤"], ["leq", "≤"], ["ge", "≥"], ["geq", "≥"],
  ["neq", "≠"], ["approx", "≈"], ["to", "→"], ["rightarrow", "→"],
]);
const SUBSCRIPT = new Map(Object.entries({
  0: "₀", 1: "₁", 2: "₂", 3: "₃", 4: "₄", 5: "₅", 6: "₆", 7: "₇", 8: "₈", 9: "₉",
  a: "ₐ", e: "ₑ", h: "ₕ", i: "ᵢ", j: "ⱼ", k: "ₖ", l: "ₗ", m: "ₘ", n: "ₙ", o: "ₒ",
  p: "ₚ", r: "ᵣ", s: "ₛ", t: "ₜ", u: "ᵤ", v: "ᵥ", x: "ₓ", "+": "₊", "-": "₋",
  "=": "₌", "(": "₍", ")": "₎",
}));
const SUPERSCRIPT = new Map(Object.entries({
  0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹",
  a: "ᵃ", b: "ᵇ", c: "ᶜ", d: "ᵈ", e: "ᵉ", g: "ᵍ", h: "ʰ", i: "ⁱ", j: "ʲ", k: "ᵏ",
  l: "ˡ", m: "ᵐ", n: "ⁿ", o: "ᵒ", p: "ᵖ", r: "ʳ", s: "ˢ", t: "ᵗ", u: "ᵘ",
  v: "ᵛ", w: "ʷ", x: "ˣ", y: "ʸ", z: "ᶻ", "+": "⁺", "-": "⁻", "=": "⁼",
  "(": "⁽", ")": "⁾",
}));

function unicodeScript(value, table, marker) {
  const characters = [...String(value)];
  const converted = characters.map((character) => table.get(character) || "").join("");
  return converted.length === characters.length ? converted : `${marker}(${value})`;
}

export function linearizeEquation(value) {
  let expression = String(value || "").trim();
  for (let pass = 0; pass < 4; pass += 1) {
    expression = expression.replace(/\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}/gu, "($1)/($2)");
    expression = expression.replace(/\\sqrt\s*\{([^{}]+)\}/gu, "√($1)");
  }
  expression = expression.replace(/\\([A-Za-z]+)/gu, (match, command) => (
    EQUATION_COMMANDS.get(command) || command
  ));
  expression = expression.replace(/_\{([^{}]+)\}/gu, (_, content) => unicodeScript(content, SUBSCRIPT, "_"));
  expression = expression.replace(/\^\{([^{}]+)\}/gu, (_, content) => unicodeScript(content, SUPERSCRIPT, "^"));
  expression = expression.replace(/_([A-Za-z0-9])/gu, (_, content) => unicodeScript(content, SUBSCRIPT, "_"));
  expression = expression.replace(/\^([A-Za-z0-9])/gu, (_, content) => unicodeScript(content, SUPERSCRIPT, "^"));
  return expression.replace(/[{}]/gu, "").replace(/\s+/gu, " ").trim();
}

function addIdentity(slide, data, t, { x, y, w, color = t.text, muted = t.muted } = {}) {
  const presenter = String(data.presenterName || "").trim();
  const organization = String(data.organization || "").trim();
  if (!presenter && !organization) return;
  line(slide, x + w - 0.62, y - 0.04, 0.62, 0, t.accent, 2.2);
  if (presenter) {
    text(slide, presenter, {
      x, y, w, h: 0.32, fontSize: 11.5, bold: true, color, align: "right", fit: "shrink",
    });
  }
  if (organization) {
    text(slide, organization, {
      x, y: y + (presenter ? 0.34 : 0), w, h: 0.3, fontSize: 10.5,
      color: muted, align: "right", fit: "shrink",
    });
  }
}

function contrastText(hex) {
  const value = String(hex || "000000").replace(/^#/, "").padEnd(6, "0").slice(0, 6);
  const channels = [0, 2, 4].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16) / 255);
  const luminance = channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  return luminance > 0.62 ? "17211E" : "FFFFFF";
}

function rect(slide, x, y, w, h, fill, options = {}) {
  slide.addShape(options.shape || SHAPE.rect, {
    x, y, w, h,
    fill: typeof fill === "string" ? { color: fill, transparency: options.transparency ?? 0 } : fill,
    line: options.line || { color: fill, transparency: 100 },
    radius: options.radius,
    shadow: options.shadow,
    rotate: options.rotate,
  });
}

function line(slide, x, y, w, h, color, width = 1, dash = "solid", transparency = 0) {
  slide.addShape(SHAPE.line, { x, y, w, h, line: { color, width, dash, transparency, beginArrowType: "none", endArrowType: "none" } });
}

function circle(slide, x, y, size, fill, options = {}) {
  rect(slide, x, y, size, size, fill, { ...options, shape: SHAPE.ellipse });
}

function addIcon(slide, name, x, y, size, color, background = null) {
  if (background) circle(slide, x, y, size, background);
  const pad = background ? size * 0.23 : 0;
  slide.addImage({ data: iconData(name, color), x: x + pad, y: y + pad, w: size - pad * 2, h: size - pad * 2 });
}

function focalValues(focal) {
  return {
    x: focal === "left" ? 0.15 : focal === "right" ? 0.85 : 0.5,
    y: focal === "top" ? 0.15 : focal === "bottom" ? 0.85 : 0.5,
  };
}

function cropOptions(asset, x, y, w, h, focal = "center") {
  const aspect = Math.max(0.01, Number(asset?.width || 1600) / Number(asset?.height || 900));
  const boxAspect = w / h;
  let cx = 0; let cy = 0; let cw = 1; let ch = 1;
  const focus = focalValues(focal);
  if (aspect >= boxAspect) {
    cw = boxAspect / aspect;
    cx = clamp((1 - cw) * focus.x, 0, 1 - cw);
  } else {
    ch = aspect / boxAspect;
    cy = clamp((1 - ch) * focus.y, 0, 1 - ch);
  }
  let virtualW = w / cw;
  let virtualH = virtualW / aspect;
  if (Math.abs(virtualH * ch - h) > 0.000001) {
    virtualH = h / ch;
    virtualW = virtualH * aspect;
  }
  return { x, y, w: virtualW, h: virtualH, sizing: { type: "crop", x: cx * virtualW, y: cy * virtualH, w, h } };
}

function addPhoto(slide, asset, box, options = {}) {
  if (!asset?.path) {
    abstractVisual(slide, box, options.theme, options.variant || 0);
    return;
  }
  slide.addImage({ data: asset.data, ...cropOptions(asset, box.x, box.y, box.w, box.h, options.focal) });
  if (options.border) rect(slide, box.x, box.y, box.w, box.h, { color: "FFFFFF", transparency: 100 }, { line: { color: options.border, width: 1.2, transparency: 15 } });
  if (options.overlay) rect(slide, box.x, box.y, box.w, box.h, options.overlay.color, { transparency: options.overlay.transparency });
}

function abstractVisual(slide, box, theme, variant = 0) {
  const t = modeTheme(theme, "dark");
  rect(slide, box.x, box.y, box.w, box.h, t.bg);
  const large = Math.min(box.w * 0.48, box.h * 0.76);
  const small = Math.min(box.w * 0.34, box.h * 0.52);
  circle(slide, box.x + box.w * 0.08, box.y + box.h * 0.08, large, t.accent, { transparency: 35 });
  circle(slide, box.x + box.w * 0.52, box.y + box.h * 0.42, small, t.accent2, { transparency: 45 });
  for (let i = 0; i < 6; i += 1) {
    const yy = box.y + 0.7 + i * (box.h - 1.4) / 5;
    line(slide, box.x + 0.4, yy, box.w - 0.8, variant % 2 ? 0.18 : -0.18, "FFFFFF", 0.7, "solid", 72);
  }
}

function addKicker(slide, value, t, x = 8.1, y = 0.42, w = 4.7) {
  rect(slide, x + w - 0.17, y, 0.17, 0.34, t.accent);
  text(slide, value, { x, y: y - 0.02, w: w - 0.3, h: 0.4, fontSize: 10.5, bold: true, color: t.accent, align: "right", charSpacing: 0.5 });
}

function addHeader(slide, data, t, options = {}) {
  const semantic = semanticStyle(data);
  addKicker(slide, data.kicker, t, options.x ?? 7.2, options.y ?? 0.38, options.w ?? 5.6);
  text(slide, data.title, { x: options.x ?? 7.2, y: options.titleY ?? 0.8, w: options.w ?? 5.6, h: options.titleH ?? 0.95, fontSize: (options.titleSize ?? 26) + semantic.titleDelta, bold: true, color: options.titleColor || t.text, align: "right", valign: "top" });
  if (data.subtitle) text(slide, data.subtitle, { x: options.x ?? 7.2, y: (options.subtitleY ?? 1.8) + semantic.gapDelta, w: options.w ?? 5.6, h: options.subtitleH ?? 0.65, fontSize: options.subtitleSize ?? 12.5, color: options.subtitleColor || t.muted, align: "right", valign: "top" });
}

function addFooter(slide, index, total, t, assets = [], footnote = "") {
  const current = String(index + 1).padStart(2, "0");
  const count = String(total).padStart(2, "0");
  const pageStyle = {
    y: 7.12, h: 0.18, fontSize: 7.5, color: t.muted, align: "center", rtlMode: false,
  };
  text(slide, current, { ...pageStyle, x: 0.35, w: 0.26 });
  text(slide, "/", { ...pageStyle, x: 0.61, w: 0.14 });
  text(slide, count, { ...pageStyle, x: 0.75, w: 0.26 });
  if (footnote) text(slide, footnote, { x: 2.0, y: 7.05, w: 8.9, h: 0.26, fontSize: 7.2, color: t.muted, align: "center" });
  const credits = [...new Set(assets.map((a) => a?.credit).filter(Boolean))].join(" · ");
  if (CONFIG.keepCredits && credits) text(slide, credits, { x: 10.2, y: 7.08, w: 2.75, h: 0.2, fontSize: 6.2, color: t.muted, align: "right" });
}

function card(slide, box, t, options = {}) {
  rect(slide, box.x, box.y, box.w, box.h, options.fill || t.surface, {
    shape: options.rounded === false ? SHAPE.rect : SHAPE.roundRect,
    line: { color: options.line || t.surface2, width: options.lineWidth || 0.7, transparency: options.lineTransparency ?? 35 },
    shadow: options.shadow === false ? undefined : dropShadow(),
  });
}

function renderCover(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "photo");
  const semantic = semanticStyle(data);
  addPhoto(slide, assets[0], { x: 0, y: 0, w: W, h: H }, { theme, focal: data.focalPoint, overlay: { color: "07111F", transparency: 28 } });
  rect(slide, 7.3, 0, 6.03, H, "07111F", { transparency: 18 });
  circle(slide, 11.35, 0.12, 1.7, t.accent2, { transparency: 70 });
  rect(slide, 7.75, 1.0, 0.12, 1.1, t.accent);
  text(slide, data.kicker, { x: 8.05, y: 0.94, w: 4.65, h: 0.35, fontSize: 10, bold: true, color: t.accent2 });
  text(slide, data.title, { x: 7.75, y: 1.42, w: 5.0, h: 2.05, fontSize: 29 + semantic.titleDelta, bold: true, color: "FFFFFF", valign: "top", fit: "shrink" });
  text(slide, data.subtitle, { x: 7.75, y: 3.72 + semantic.gapDelta, w: 4.85, h: 1.0, fontSize: 14, color: "E2E8F0", valign: "top", fit: "shrink" });
  if (data.callout) {
    rect(slide, 7.75, 4.92, 4.75, 0.65, "FFFFFF", { transparency: 88, shape: SHAPE.roundRect, line: { color: "FFFFFF", transparency: 100 } });
    addIcon(slide, "sparkles", 11.88, 5.08, 0.3, t.accent2);
    text(slide, data.callout, { x: 8.0, y: 4.98, w: 3.7, h: 0.49, fontSize: 10.5, color: "FFFFFF" });
  }
  addIdentity(slide, data, t, {
    x: 7.75,
    y: data.callout ? 5.86 : 5.35,
    w: 4.75,
    color: "FFFFFF",
    muted: "CBD5E1",
  });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderEditorialSplit(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "light");
  slide.background = { color: t.bg };
  addPhoto(slide, assets[0], { x: 0, y: 0, w: 5.15, h: H }, { theme, focal: data.focalPoint, overlay: { color: theme.primary, transparency: 78 } });
  rect(slide, 5.15, 0, 0.15, H, t.accent);
  addHeader(slide, data, t, { x: 6.0, w: 6.7, titleY: 0.8, titleH: 1.05, titleSize: 25, subtitleY: 1.95 });
  const items = data.items.slice(0, 4);
  items.forEach((item, i) => {
    const y = 2.82 + i * 0.92;
    addIcon(slide, item.icon, 11.78, y, 0.55, "FFFFFF", i % 2 ? t.accent2 : t.accent);
    text(slide, item.title, { x: 8.25, y: y - 0.02, w: 3.25, h: 0.28, fontSize: 12, bold: true, color: t.text });
    text(slide, item.body, { x: 6.0, y: y + 0.28, w: 5.5, h: 0.48, fontSize: 11.5, color: t.muted, valign: "top" });
  });
  if (data.callout) {
    rect(slide, 6.0, 6.45, 6.7, 0.45, t.surface2, { shape: SHAPE.roundRect, line: { color: t.surface2, transparency: 100 } });
    text(slide, data.callout, { x: 6.25, y: 6.49, w: 6.1, h: 0.35, fontSize: 9.2, bold: true, color: t.text });
  }
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderCards(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.65, w: 7.1, titleSize: 25 });
  const items = data.items.slice(0, 6);
  const cols = items.length === 4 ? 2 : items.length <= 3 ? items.length : 3;
  const rows = Math.ceil(items.length / cols);
  const gap = 0.22;
  const areaX = 0.55; const areaY = 2.52; const areaW = 12.23; const areaH = 4.15;
  const cw = (areaW - gap * (cols - 1)) / cols;
  const ch = (areaH - gap * (rows - 1)) / rows;
  const surfaceText = data.mode === "accent" ? theme.text : t.text;
  const surfaceMuted = data.mode === "accent" ? theme.muted : t.muted;
  items.forEach((item, i) => {
    const row = Math.floor(i / cols); const col = i % cols;
    const x = areaX + col * (cw + gap); const y = areaY + row * (ch + gap);
    card(slide, { x, y, w: cw, h: ch }, t, { fill: data.mode === "dark" ? t.surface : theme.surface, line: data.mode === "dark" ? t.surface2 : "DCE4EE" });
    addIcon(slide, item.icon, x + cw - 0.78, y + 0.35, 0.5, "FFFFFF", i % 3 === 0 ? t.accent : i % 3 === 1 ? t.accent2 : theme.secondary);
    if (item.value) text(slide, item.value, { x: x + 0.28, y: y + 0.28, w: 1.3, h: 0.35, fontSize: 15, bold: true, color: t.accent, align: "left" });
    text(slide, item.title, { x: x + 0.3, y: y + 0.85, w: cw - 0.6, h: 0.38, fontSize: 13, bold: true, color: surfaceText });
    text(slide, item.body, { x: x + 0.3, y: y + 1.32, w: cw - 0.6, h: ch - 1.58, fontSize: rows > 1 ? 10.5 : 12, color: surfaceMuted, valign: "top" });
  });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderMetrics(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.7, w: 7.0, titleSize: 25 });
  const items = data.items.slice(0, 5);
  const surfaceText = data.mode === "accent" ? theme.text : t.text;
  const surfaceMuted = data.mode === "accent" ? theme.muted : t.muted;
  const w = (12.15 - (items.length - 1) * 0.22) / items.length;
  items.forEach((item, i) => {
    const x = 0.58 + i * (w + 0.22);
    card(slide, { x, y: 2.72, w, h: 3.3 }, t, { fill: t.surface, line: t.surface2 });
    addIcon(slide, item.icon, x + w - 0.72, 3.08, 0.42, "FFFFFF", i % 2 ? t.accent2 : t.accent);
    text(slide, item.value || String(i + 1), { x: x + 0.25, y: 3.0, w: w - 1.15, h: 0.65, fontSize: 25, bold: true, color: surfaceText, align: "left" });
    text(slide, item.title, { x: x + 0.25, y: 3.92, w: w - 0.5, h: 0.45, fontSize: 12, bold: true, color: surfaceText });
    text(slide, item.body, { x: x + 0.25, y: 4.48, w: w - 0.5, h: 0.84, fontSize: items.length > 4 ? 10 : 11.5, color: surfaceMuted, valign: "top" });
    rect(slide, x + 0.25, 5.58, w - 0.5, 0.08, t.surface2, { shape: SHAPE.roundRect });
    rect(slide, x + 0.25, 5.58, (w - 0.5) * clamp(0.45 + i * 0.1, 0.2, 0.9), 0.08, i % 2 ? t.accent2 : t.accent, { shape: SHAPE.roundRect });
  });
  if (data.callout) text(slide, data.callout, { x: 1.2, y: 6.33, w: 10.9, h: 0.4, fontSize: 10, bold: true, color: t.accent, align: "center" });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderTimeline(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.4, w: 7.35, titleSize: 25 });
  const items = data.items.slice(0, 4);
  const left = 0.85; const right = 12.48; const yLine = 4.05;
  line(slide, left, yLine, right - left, 0, data.mode === "accent" ? "FFFFFF" : t.surface2, 1.8, "solid", 20);
  const step = (right - left) / Math.max(1, items.length - 1);
  items.forEach((item, i) => {
    const x = left + i * step;
    circle(slide, x - 0.22, yLine - 0.22, 0.44, i % 2 ? t.accent2 : t.accent, { line: { color: "FFFFFF", width: 2 } });
    const above = i % 2 === 0;
    const cardY = above ? 2.48 : 4.45;
    const cardH = 1.4;
    card(slide, { x: clamp(x - 1.12, 0.35, 10.75), y: cardY, w: 2.25, h: cardH }, t, { fill: data.mode === "accent" ? "FFFFFF" : t.surface, shadow: false, line: data.mode === "accent" ? "FFFFFF" : t.surface2 });
    const copyX = clamp(x - 0.95, 0.5, 10.9);
    text(slide, item.value || `۰${i + 1}`, { x: copyX, y: cardY + 0.12, w: 1.85, h: 0.24, fontSize: 9.5, bold: true, color: t.accent, align: "center" });
    text(slide, item.title, { x: copyX, y: cardY + 0.41, w: 1.85, h: 0.34, fontSize: 10.5, bold: true, color: data.mode === "accent" ? theme.text : t.text, align: "center" });
    text(slide, item.body, { x: copyX, y: cardY + 0.79, w: 1.85, h: 0.48, fontSize: 10.5, color: data.mode === "accent" ? theme.muted : t.muted, valign: "top", align: "center" });
    line(slide, x, above ? cardY + cardH : yLine + 0.22, 0, above ? yLine - (cardY + cardH) - 0.22 : cardY - yLine - 0.22, data.mode === "accent" ? "FFFFFF" : t.surface2, 0.8, "dash", 25);
  });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderComparison(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "dark");
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.0, w: 7.75, titleSize: 24, subtitleY: 1.82 });
  const boxes = [
    { x: 0.62, title: data.leftTitle, items: data.leftItems, accent: "94A3B8", fill: "172033" },
    { x: 6.86, title: data.rightTitle, items: data.rightItems, accent: t.accent2, fill: "132D32" },
  ];
  boxes.forEach((group, groupIndex) => {
    card(slide, { x: group.x, y: 2.62, w: 5.85, h: 3.95 }, t, { fill: group.fill, line: groupIndex ? t.accent2 : "475569", shadow: false });
    text(slide, group.title, { x: group.x + 0.35, y: 2.9, w: 5.15, h: 0.4, fontSize: 15, bold: true, color: groupIndex ? t.accent2 : "CBD5E1" });
    group.items.slice(0, 4).forEach((item, i) => {
      const y = 3.53 + i * 0.82;
      addIcon(slide, item.icon, group.x + 5.02, y, 0.4, groupIndex ? t.accent2 : "CBD5E1");
      text(slide, item.title, { x: group.x + 1.65, y: y - 0.04, w: 3.15, h: 0.28, fontSize: 10.5, bold: true, color: "FFFFFF" });
      text(slide, item.body, { x: group.x + 0.4, y: y + 0.26, w: 4.4, h: 0.4, fontSize: 10.5, color: "B8C3D4", valign: "top" });
    });
  });
  circle(slide, 6.37, 4.12, 0.58, t.accent, { shadow: dropShadow() });
  text(slide, "↔", { x: 6.37, y: 4.08, w: 0.58, h: 0.5, fontSize: 20, bold: true, color: "FFFFFF", align: "center" });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function chartType(type) {
  if (type === "line") return PPTX.ChartType.line;
  if (type === "doughnut") return PPTX.ChartType.doughnut;
  if (type === "area") return PPTX.ChartType.area;
  return PPTX.ChartType.bar;
}

function addChart(slide, chart, theme, box, dark = false) {
  const colors = [theme.accent, theme.accent2, theme.secondary];
  const data = chart.series.map((series) => ({ name: series.name, labels: chart.labels, values: series.values }));
  slide.addChart(chartType(chart.type), data, {
    x: box.x, y: box.y, w: box.w, h: box.h,
    showTitle: false,
    showLegend: chart.series.length > 1,
    legendPos: "b",
    legendFontFace: CONFIG.font,
    legendFontSize: 8,
    showValue: chart.type !== "line" && chart.type !== "area",
    showCatName: false,
    showPercent: chart.type === "doughnut",
    catAxisLabelFontFace: CONFIG.font,
    catAxisLabelFontSize: 8,
    valAxisLabelFontFace: CONFIG.font,
    valAxisLabelFontSize: 8,
    catAxisLabelColor: dark ? "CBD5E1" : theme.muted,
    valAxisLabelColor: dark ? "94A3B8" : theme.muted,
    chartColors: colors,
    showValue: chart.type === "bar",
    showCategoryName: false,
    showLeaderLines: false,
    showBorder: false,
    showGridLines: chart.type !== "doughnut",
    gridLine: { color: dark ? "334155" : "E2E8F0", transparency: 20 },
    showCatName: false,
    showSerName: false,
    showTitle: false,
    showLegendKey: false,
    showValAxisTitle: false,
    showCatAxisTitle: false,
    showValAxis: chart.type !== "doughnut",
    showCatAxis: chart.type !== "doughnut",
    border: { color: dark ? "1E293B" : "FFFFFF", transparency: 100 },
  });
}

function renderChartFocus(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.2, w: 7.55, titleSize: 24 });
  card(slide, { x: 0.55, y: 2.55, w: 8.25, h: 4.0 }, t, { fill: t.surface, line: t.surface2 });
  if (data.chart?.title) text(slide, data.chart.title, { x: 0.95, y: 2.83, w: 7.45, h: 0.35, fontSize: 11, bold: true, color: t.text });
  addChart(slide, data.chart, theme, { x: 0.78, y: 3.25, w: 7.7, h: 2.85 }, data.mode === "dark");
  data.items.slice(0, 3).forEach((item, i) => {
    const y = 2.65 + i * 1.25;
    card(slide, { x: 9.1, y, w: 3.65, h: 1.02 }, t, { fill: t.surface, line: t.surface2, shadow: false });
    addIcon(slide, item.icon, 11.96, y + 0.27, 0.42, "FFFFFF", i % 2 ? t.accent2 : t.accent);
    text(slide, item.title, { x: 9.42, y: y + 0.12, w: 2.25, h: 0.28, fontSize: 10.5, bold: true, color: t.text });
    text(slide, item.body, { x: 9.42, y: y + 0.44, w: 2.35, h: 0.43, fontSize: 10.2, color: t.muted, valign: "top" });
  });
  if (data.callout) {
    rect(slide, 9.1, 6.18, 3.65, 0.38, t.accent, { shape: SHAPE.roundRect });
    text(slide, data.callout, { x: 9.25, y: 6.19, w: 3.35, h: 0.34, fontSize: 8.2, bold: true, color: "FFFFFF", align: "center" });
  }
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderMatrix(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.1, w: 7.65, titleSize: 24 });
  const mx = 1.1, my = 2.65, mw = 11.1, mh = 3.95;
  rect(slide, mx, my, mw, mh, data.mode === "accent" ? "FFFFFF" : t.surface, { shape: SHAPE.roundRect, line: { color: data.mode === "accent" ? "FFFFFF" : t.surface2, transparency: 25 }, shadow: dropShadow() });
  line(slide, mx + mw / 2, my + 0.3, 0, mh - 0.6, data.mode === "accent" ? theme.secondary : t.surface2, 1, "dash", 30);
  line(slide, mx + 0.3, my + mh / 2, mw - 0.6, 0, data.mode === "accent" ? theme.secondary : t.surface2, 1, "dash", 30);
  const positions = [
    { x: mx + mw / 2 + 0.25, y: my + 0.25 },
    { x: mx + 0.25, y: my + 0.25 },
    { x: mx + mw / 2 + 0.25, y: my + mh / 2 + 0.15 },
    { x: mx + 0.25, y: my + mh / 2 + 0.15 },
  ];
  data.items.slice(0, 4).forEach((item, i) => {
    const p = positions[i];
    const boxW = mw / 2 - 0.5; const boxH = mh / 2 - 0.45;
    addIcon(slide, item.icon, p.x + boxW - 0.55, p.y + 0.12, 0.37, "FFFFFF", i === 0 ? t.accent : i === 1 ? t.accent2 : i === 2 ? theme.secondary : theme.primary);
    if (item.value) text(slide, item.value, { x: p.x + 0.1, y: p.y + 0.1, w: 1.3, h: 0.25, fontSize: 8.5, bold: true, color: t.accent, align: "left" });
    text(slide, item.title, { x: p.x + 0.1, y: p.y + 0.48, w: boxW - 0.35, h: 0.32, fontSize: 11, bold: true, color: data.mode === "accent" ? theme.text : t.text });
    text(slide, item.body, { x: p.x + 0.1, y: p.y + 0.88, w: boxW - 0.35, h: 0.55, fontSize: 10.5, color: data.mode === "accent" ? theme.muted : t.muted, valign: "top" });
  });
  text(slide, `${data.xAxisLabel}  ←`, { x: mx + 3.75, y: my + mh + 0.08, w: 3.6, h: 0.3, fontSize: 10, color: data.mode === "accent" ? "FFFFFF" : t.muted, align: "center" });
  text(slide, data.yAxisLabel, { x: mx - 0.5, y: my + 1.45, w: 1.25, h: 0.3, fontSize: 10, color: data.mode === "accent" ? "FFFFFF" : t.muted, rotate: 270, align: "center" });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderProcess(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "dark");
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.1, w: 7.65, titleSize: 24 });
  const items = data.items.slice(0, 4);
  const gap = 0.16; const startX = 0.58; const y = 3.05; const totalW = 12.18;
  const w = (totalW - gap * (items.length - 1)) / items.length;
  items.forEach((item, i) => {
    const x = startX + i * (w + gap);
    card(slide, { x, y, w, h: 2.72 }, t, { fill: i % 2 ? "17283A" : "182035", line: i % 2 ? t.accent2 : t.accent, shadow: false });
    text(slide, item.value || String(i + 1).padStart(2, "0"), { x: x + 0.18, y: y + 0.2, w: 0.75, h: 0.35, fontSize: 12, bold: true, color: i % 2 ? t.accent2 : t.accent, align: "left" });
    addIcon(slide, item.icon, x + w - 0.62, y + 0.22, 0.38, i % 2 ? t.accent2 : t.accent);
    text(slide, item.title, { x: x + 0.22, y: y + 0.9, w: w - 0.44, h: 0.45, fontSize: 11, bold: true, color: "FFFFFF" });
    text(slide, item.body, { x: x + 0.22, y: y + 1.45, w: w - 0.44, h: 0.82, fontSize: 10.5, color: "B9C4D6", valign: "top" });
    if (i < items.length - 1) text(slide, data.reading_direction === "ltr" ? "→" : "←", { x: x + w - 0.02, y: y + 1.08, w: gap + 0.12, h: 0.38, fontSize: 15, bold: true, color: t.accent, align: "center" });
  });
  if (data.callout) text(slide, data.callout, { x: 2.0, y: 6.15, w: 9.3, h: 0.4, fontSize: 10.2, bold: true, color: t.accent2, align: "center" });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderMosaic(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "dark");
  slide.background = { color: t.bg };
  addPhoto(slide, assets[0], { x: 0, y: 0, w: 4.25, h: 4.35 }, { theme, focal: "center" });
  addPhoto(slide, assets[1], { x: 0, y: 4.48, w: 4.25, h: 3.02 }, { theme, focal: "center" });
  addPhoto(slide, assets[2], { x: 4.38, y: 0, w: 3.25, h: H }, { theme, focal: "center" });
  rect(slide, 7.63, 0, 5.7, H, t.bg);
  addHeader(slide, data, t, { x: 8.1, w: 4.75, titleY: 0.82, titleH: 1.3, titleSize: 23, subtitleY: 2.2, subtitleH: 0.8 });
  data.items.slice(0, 3).forEach((item, i) => {
    const y = 3.25 + i * 1.03;
    addIcon(slide, item.icon, 12.03, y, 0.48, "FFFFFF", i === 0 ? t.accent : i === 1 ? t.accent2 : theme.secondary);
    text(slide, item.title, { x: 9.7, y: y - 0.02, w: 2.05, h: 0.3, fontSize: 11.5, bold: true, color: "FFFFFF" });
    text(slide, item.body, { x: 8.1, y: y + 0.3, w: 3.65, h: 0.46, fontSize: 10.5, color: "B8C3D4", valign: "top" });
  });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderQuote(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "photo");
  addPhoto(slide, assets[0], { x: 0, y: 0, w: W, h: H }, { theme, focal: data.focalPoint, overlay: { color: "06101C", transparency: 25 } });
  rect(slide, 0, 0, 7.25, H, "07111F", { transparency: 18 });
  text(slide, "“", { x: 0.85, y: 0.65, w: 1.2, h: 1.2, fontSize: 54, bold: true, color: t.accent2, align: "left" });
  text(slide, data.quote || data.title, { x: 0.95, y: 1.55, w: 5.85, h: 2.35, fontSize: 25, bold: true, color: "FFFFFF", valign: "top" });
  if (data.quoteSource) text(slide, data.quoteSource, { x: 1.0, y: 4.2, w: 4.8, h: 0.35, fontSize: 10.5, bold: true, color: t.accent2, align: "left" });
  text(slide, data.subtitle, { x: 0.95, y: 5.02, w: 5.7, h: 0.8, fontSize: 12, color: "D8E0EB", valign: "top" });
  text(slide, data.kicker, { x: 8.0, y: 0.65, w: 4.7, h: 0.3, fontSize: 9, bold: true, color: t.accent, align: "right" });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderBigStatement(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  const semantic = semanticStyle(data);
  const hasEquation = Boolean(String(data.equation || "").trim());
  slide.background = { color: t.bg };
  circle(slide, -0.85, -1.0, 3.5, t.accent2, { transparency: 70 });
  circle(slide, 10.8, 5.1, 3.3, t.accent, { transparency: 68 });
  text(slide, data.kicker, { x: 8.0, y: hasEquation ? 0.43 : 0.65, w: 4.55, h: 0.35, fontSize: 10, bold: true, color: t.accent });
  const hasBody = Boolean(String(data.body || "").trim());
  if (hasEquation) {
    text(slide, data.title, { x: 1.05, y: 0.82, w: 11.2, h: 1.22, fontSize: 29, bold: true, color: t.text, align: "center", valign: "mid", fit: "shrink" });
    line(slide, 5.72, 2.08, 1.9, 0, t.accent, 3);
    if (data.subtitle) text(slide, data.subtitle, { x: 2.25, y: 2.28 + semantic.gapDelta, w: 8.85, h: 0.48, fontSize: 14, color: t.muted, align: "center", valign: "top", fit: "shrink" });
    if (hasBody) text(slide, data.body, { x: 1.85, y: 2.88 + semantic.gapDelta, w: 9.65, h: 0.92, fontSize: 13, color: t.text, align: "center", valign: "top", fit: "shrink", lineSpacingMultiple: 1.08 });
    const { label, expression: rawExpression } = equationParts(data);
    const expression = linearizeEquation(rawExpression);
    const equationY = 4.02 + semantic.gapDelta;
    const equationTextColor = data.mode === "accent" ? theme.text : t.text;
    const equationLabelColor = data.mode === "accent" ? theme.muted : t.muted;
    rect(slide, 2.0, equationY, 9.35, 1.28, t.surface, { shape: SHAPE.roundRect, line: { color: t.accent, width: 1.6, transparency: 8 }, shadow: dropShadow() });
    if (label) text(slide, label, { x: 2.35, y: equationY + 0.12, w: 8.65, h: 0.26, fontSize: 10.5, bold: true, color: equationLabelColor, align: "center" });
    text(slide, expression, {
      x: 2.35,
      y: equationY + (label ? 0.43 : 0.24),
      w: 8.65,
      h: label ? 0.63 : 0.82,
      fontFace: "Cambria Math",
      lang: "en-US",
      fontSize: expression.length > 72 ? 22 : 27,
      bold: true,
      color: equationTextColor,
      align: "center",
      valign: "mid",
      rtlMode: false,
      bidiSafe: false,
      fit: "shrink",
    });
    if (data.callout) {
      rect(slide, 3.2, 5.63, 6.95, 0.58, t.surface, { shape: SHAPE.roundRect, line: { color: t.accent, width: 1.1, transparency: 15 } });
      text(slide, data.callout, { x: 3.48, y: 5.68, w: 6.4, h: 0.46, fontSize: 10.5, bold: true, color: data.mode === "accent" ? theme.text : t.text, align: "center", fit: "shrink" });
    }
  } else {
    const titleSize = (hasBody ? 30 : 34) + semantic.titleDelta;
    const bodySize = semantic.hierarchy === "content" ? 14 : 12.8;
    text(slide, data.title, { x: 1.1, y: hasBody ? 1.22 : 1.45, w: 11.0, h: hasBody ? 1.75 : 2.45, fontSize: titleSize, bold: true, color: t.text, align: "center", valign: "mid", fit: "shrink" });
    line(slide, 5.7, hasBody ? 3.22 : 4.32, 1.9, 0, t.accent, 3);
    text(slide, data.subtitle, { x: 2.35, y: (hasBody ? 3.5 : 4.7) + semantic.gapDelta, w: 8.65, h: hasBody ? 0.62 : 0.95, fontSize: 14, color: t.muted, align: "center", valign: "top", fit: "shrink" });
    if (hasBody) text(slide, data.body, { x: 2.0, y: 4.25 + semantic.gapDelta, w: 9.35, h: 1.45, fontSize: bodySize, color: t.text, align: "center", valign: "top", fit: "shrink", lineSpacingMultiple: 1.08 });
    if (data.callout) {
      rect(slide, 3.2, 5.92, 6.95, 0.58, t.surface, { shape: SHAPE.roundRect, line: { color: t.accent, width: 1.2, transparency: 15 } });
      text(slide, data.callout, { x: 3.48, y: 5.97, w: 6.4, h: 0.46, fontSize: 10.5, bold: true, color: data.mode === "accent" ? theme.text : t.text, align: "center", fit: "shrink" });
    }
  }
  if (index === 0) addIdentity(slide, data, t, { x: 7.2, y: 6.34, w: 5.1 });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderCaseStudy(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "light");
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.0, w: 7.7, titleSize: 24 });
  const groups = [
    { x: 0.6, asset: assets[0], label: data.leftTitle || "قبل", item: data.items[0], color: "64748B" },
    { x: 6.86, asset: assets[1], label: data.rightTitle || "بعد", item: data.items[1], color: t.accent },
  ];
  groups.forEach((g) => {
    card(slide, { x: g.x, y: 2.58, w: 5.85, h: 3.92 }, t, { fill: t.surface, line: t.surface2 });
    addPhoto(slide, g.asset, { x: g.x + 0.22, y: 2.8, w: 5.41, h: 1.9 }, { theme, focal: "center" });
    rect(slide, g.x + 4.52, 2.98, 0.85, 0.35, g.color, { shape: SHAPE.roundRect });
    text(slide, g.label, { x: g.x + 4.55, y: 3.0, w: 0.78, h: 0.3, fontSize: 8, bold: true, color: "FFFFFF", align: "center" });
    text(slide, g.item?.title || g.label, { x: g.x + 0.35, y: 4.95, w: 5.1, h: 0.38, fontSize: 12.5, bold: true, color: t.text });
    text(slide, g.item?.body || "", { x: g.x + 0.35, y: 5.43, w: 5.1, h: 0.7, fontSize: 8.8, color: t.muted, valign: "top" });
  });
  if (data.items[2]) {
    rect(slide, 5.91, 2.12, 1.5, 0.58, t.accent, { shape: SHAPE.roundRect, shadow: dropShadow() });
    text(slide, data.items[2].value || data.items[2].title, { x: 6.02, y: 2.2, w: 1.28, h: 0.34, fontSize: 10, bold: true, color: "FFFFFF", align: "center" });
  }
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderTable(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.1, w: 7.65, titleSize: 24 });
  const items = data.items.slice(0, 6);
  const x = 0.7, y = 2.62, w = 11.95, headerH = 0.54, rowH = 0.58;
  rect(slide, x, y, w, headerH, t.accent, { shape: SHAPE.roundRect, shadow: dropShadow() });
  text(slide, "موضوع", { x: x + 8.1, y: y + 0.06, w: 3.45, h: 0.4, fontSize: 10, bold: true, color: "FFFFFF" });
  text(slide, "نشانه / مقدار", { x: x + 5.35, y: y + 0.06, w: 2.35, h: 0.4, fontSize: 10, bold: true, color: "FFFFFF", align: "center" });
  text(slide, "توضیح", { x: x + 0.35, y: y + 0.06, w: 4.55, h: 0.4, fontSize: 10, bold: true, color: "FFFFFF" });
  items.forEach((item, i) => {
    const yy = y + headerH + i * rowH;
    rect(slide, x, yy, w, rowH, i % 2 ? t.surface2 : t.surface, { line: { color: t.surface2, transparency: 60 } });
    addIcon(slide, item.icon, x + 11.42, yy + 0.12, 0.32, t.accent);
    text(slide, item.title, { x: x + 8.08, y: yy + 0.06, w: 3.12, h: 0.45, fontSize: 10.5, bold: true, color: t.text });
    text(slide, item.value, { x: x + 5.3, y: yy + 0.06, w: 2.4, h: 0.45, fontSize: 10.5, bold: true, color: t.accent, align: "center" });
    text(slide, item.body, { x: x + 0.32, y: yy + 0.06, w: 4.6, h: 0.45, fontSize: 10, color: t.muted });
  });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderFunnel(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, data.mode);
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.2, w: 7.55, titleSize: 24 });
  const items = data.items.slice(0, 5);
  items.forEach((item, i) => {
    const width = 8.6 - i * 1.15;
    const x = 6.3 - width / 2;
    const y = 2.65 + i * 0.7;
    const colors = [theme.primary, t.accent, t.accent2, theme.primary, t.accent];
    const color = colors[i % colors.length];
    slide.addShape(SHAPE.trapezoid, { x, y, w: width, h: 0.58, fill: { color, transparency: i * 4 }, line: { color, transparency: 100 } });
    const labelColor = contrastText(color);
    text(slide, item.title, { x: x + 1.2, y: y + 0.07, w: width - 2.4, h: 0.4, fontSize: 10.5, bold: true, color: labelColor, align: "center" });
    if (item.value) text(slide, item.value, { x: x + width - 1.0, y: y + 0.08, w: 0.7, h: 0.36, fontSize: 9.2, bold: true, color: labelColor, align: "center" });
  });
  if (data.callout) text(slide, data.callout, { x: 2.2, y: 6.35, w: 8.9, h: 0.4, fontSize: 10, bold: true, color: data.mode === "dark" ? t.accent2 : t.text, align: "center" });
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderDashboard(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "light");
  slide.background = { color: t.bg };
  addHeader(slide, data, t, { x: 5.2, w: 7.55, titleSize: 23.5, subtitleY: 1.72 });
  const kpis = data.items.slice(0, 4);
  kpis.forEach((item, i) => {
    const x = 0.58 + i * 3.08;
    card(slide, { x, y: 2.35, w: 2.88, h: 1.2 }, t, { fill: t.surface, line: t.surface2 });
    addIcon(slide, item.icon, x + 2.2, 2.68, 0.38, "FFFFFF", i % 2 ? t.accent2 : t.accent);
    text(slide, item.value || item.title, { x: x + 0.22, y: 2.55, w: 1.65, h: 0.35, fontSize: 13, bold: true, color: t.text, align: "left" });
    text(slide, item.title, { x: x + 0.22, y: 2.95, w: 1.85, h: 0.23, fontSize: 8.5, bold: true, color: t.muted, align: "left" });
    text(slide, item.body, { x: x + 0.22, y: 3.17, w: 2.25, h: 0.3, fontSize: 9.5, color: t.muted, align: "left" });
  });
  card(slide, { x: 0.58, y: 3.82, w: 8.25, h: 2.72 }, t, { fill: t.surface, line: t.surface2 });
  if (data.chart?.title) text(slide, data.chart.title, { x: 0.9, y: 4.03, w: 7.6, h: 0.3, fontSize: 10, bold: true, color: t.text });
  addChart(slide, data.chart, theme, { x: 0.82, y: 4.38, w: 7.65, h: 1.9 }, false);
  card(slide, { x: 9.08, y: 3.82, w: 3.67, h: 2.72 }, t, { fill: theme.primary, line: theme.primary, shadow: false });
  if (data.callout) {
    text(slide, data.callout, { x: 9.42, y: 4.18, w: 2.95, h: 0.95, fontSize: 15, bold: true, color: "FFFFFF", valign: "top" });
    line(slide, 9.42, 5.38, 1.1, 0, t.accent, 3);
  }
  if (data.footnote) text(slide, data.footnote, { x: 9.42, y: 5.65, w: 2.95, h: 0.46, fontSize: 10, color: "CBD5E1", valign: "top" });
  addFooter(slide, index, total, t, assets, "");
}

function renderClosing(slide, data, theme, assets, index, total) {
  const t = modeTheme(theme, "dark");
  slide.background = { color: t.bg };
  circle(slide, 0.12, 5.55, 1.65, t.accent2, { transparency: 75 });
  circle(slide, 11.55, 0.12, 1.55, t.accent, { transparency: 72 });
  addHeader(slide, data, t, { x: 5.0, w: 7.7, titleY: 0.75, titleH: 1.05, titleSize: 27, subtitleY: 1.9 });
  const items = data.items.slice(0, 4);
  const w = (12.0 - (items.length - 1) * 0.25) / items.length;
  items.forEach((item, i) => {
    const x = 0.65 + i * (w + 0.25);
    card(slide, { x, y: 3.05, w, h: 2.55 }, t, { fill: "151F31", line: i % 2 ? t.accent2 : t.accent, shadow: false });
    text(slide, item.value || String(i + 1), { x: x + 0.22, y: 3.25, w: 0.7, h: 0.42, fontSize: 16, bold: true, color: i % 2 ? t.accent2 : t.accent, align: "left" });
    addIcon(slide, item.icon, x + w - 0.65, 3.28, 0.38, i % 2 ? t.accent2 : t.accent);
    text(slide, item.title, { x: x + 0.25, y: 4.0, w: w - 0.5, h: 0.38, fontSize: 12, bold: true, color: "FFFFFF" });
    text(slide, item.body, { x: x + 0.25, y: 4.52, w: w - 0.5, h: 0.7, fontSize: 10.5, color: "B8C3D4", valign: "top" });
  });
  if (data.callout) {
    rect(slide, 3.15, 6.15, 7.05, 0.52, t.accent, { shape: SHAPE.roundRect, shadow: dropShadow() });
    text(slide, data.callout, { x: 3.4, y: 6.18, w: 6.55, h: 0.42, fontSize: 10.5, bold: true, color: "FFFFFF", align: "center" });
  }
  addFooter(slide, index, total, t, assets, data.footnote);
}

function renderFallback(slide, data, theme, assets, index, total) {
  renderBigStatement(slide, data, theme, assets, index, total);
}

function horizontalScale(slide, factor) {
  if (factor === 1) return slide;
  const scaledMethods = new Set(["addText", "addShape", "addImage", "addChart"]);
  return new Proxy(slide, {
    get(target, property) {
      const value = target[property];
      if (typeof value !== "function") return value;
      if (!scaledMethods.has(property)) return value.bind(target);
      return (...args) => {
        const optionIndex = args.length - 1;
        const options = args[optionIndex];
        if (options && typeof options === "object" && !Array.isArray(options)) {
          const scaledWidth = Number.isFinite(options.w) ? options.w * factor : undefined;
          const scaledX = Number.isFinite(options.x) ? options.x * factor : undefined;
          args[optionIndex] = {
            ...options,
            ...(scaledX !== undefined
              ? { x: Math.max(0, Math.min(scaledX, 10 - (scaledWidth || 0))) }
              : {}),
            ...(scaledWidth !== undefined ? { w: Math.min(10, scaledWidth) } : {}),
          };
        }
        return value.apply(target, args);
      };
    },
  });
}

export async function buildPowerPoint(deck, assetsBySlide, outputPath, options = {}) {
  const pptx = new pptxgen();
  PPTX = pptx;
  SHAPE = pptx.ShapeType;
  const standard = String(options.aspect_ratio || "wide") === "standard";
  pptx.layout = standard ? "LAYOUT_4x3" : "LAYOUT_WIDE";
  pptx.rtlMode = true;
  pptx.author = "SourceLens";
  pptx.company = "Local generator";
  pptx.subject = deck.subtitle;
  pptx.title = deck.title;
  pptx.lang = "fa-IR";
  pptx.theme = { headFontFace: CONFIG.font, bodyFontFace: CONFIG.font, lang: "fa-IR" };
  pptx.defineSlideMaster({
    title: "BLANK_MASTER",
    background: { color: deck.theme.background },
    objects: [],
  });

  const total = deck.slides.length;
  deck.slides.forEach((data, index) => {
    const rawSlide = pptx.addSlide("BLANK_MASTER");
    const slide = horizontalScale(rawSlide, standard ? 10 / W : 1);
    const assets = assetsBySlide[index] || [];
    const renderData = index === 0
      ? { ...data, presenterName: deck.presenterName, organization: deck.organization }
      : data;
    switch (renderData.layout) {
      case "cover_cinematic": renderCover(slide, renderData, deck.theme, assets, index, total); break;
      case "editorial_split": renderEditorialSplit(slide, renderData, deck.theme, assets, index, total); break;
      case "cards_icon": renderCards(slide, renderData, deck.theme, assets, index, total); break;
      case "metrics_dashboard": renderMetrics(slide, renderData, deck.theme, assets, index, total); break;
      case "timeline_story": renderTimeline(slide, renderData, deck.theme, assets, index, total); break;
      case "comparison_dual": renderComparison(slide, renderData, deck.theme, assets, index, total); break;
      case "chart_focus": renderChartFocus(slide, renderData, deck.theme, assets, index, total); break;
      case "matrix_2x2": renderMatrix(slide, renderData, deck.theme, assets, index, total); break;
      case "process_flow": renderProcess(slide, renderData, deck.theme, assets, index, total); break;
      case "photo_mosaic": renderMosaic(slide, renderData, deck.theme, assets, index, total); break;
      case "quote_photo": renderQuote(slide, renderData, deck.theme, assets, index, total); break;
      case "big_statement": renderBigStatement(slide, renderData, deck.theme, assets, index, total); break;
      case "case_study": renderCaseStudy(slide, renderData, deck.theme, assets, index, total); break;
      case "table_clean": renderTable(slide, renderData, deck.theme, assets, index, total); break;
      case "funnel": renderFunnel(slide, renderData, deck.theme, assets, index, total); break;
      case "dashboard": renderDashboard(slide, renderData, deck.theme, assets, index, total); break;
      case "closing_actions": renderClosing(slide, renderData, deck.theme, assets, index, total); break;
      default: renderFallback(slide, renderData, deck.theme, assets, index, total);
    }
    const sourceLines = options.show_citations === false
      ? []
      : (data.citations || []).map((value) => `- [${value}]`);
    sourceLines.push(...assets.map((asset) => {
      const source = asset?.sourceUrl || asset?.source_url;
      return source ? `- Image: ${source}` : "";
    }).filter(Boolean));
    const notes = options.include_speaker_notes === false
      ? ""
      : [
          data.speakerNotes,
          data.footnote,
          sourceLines.length ? `[Sources]\n${sourceLines.join("\n")}` : "",
        ].filter(Boolean).join("\n\n");
    if (notes) rawSlide.addNotes(notes);
  });

  // compression=false keeps the embedded image bytes unchanged; images are cropped, never stretched.
  await pptx.writeFile({ fileName: outputPath, compression: false });
  return outputPath;
}
