import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import process from "node:process";
import { configure } from "./config.mjs";
import { LAYOUTS } from "./layouts.mjs";
import { normalizeDeck } from "./normalize.mjs";
import { buildPowerPoint } from "./render.mjs";

const CONTRACT_VERSION = "paperlens.visual-engine.v1";
const ENGINE = {
  name: "sourcelens-deck-renderer",
  version: "5.1.0",
};

function event(type, stage, progress, message, details = {}) {
  process.stdout.write(`${JSON.stringify({ type, stage, progress, message, details })}\n`);
}

function argumentsFrom(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--request" || value === "--output" || value === "--result") {
      result[value.slice(2)] = argv[index + 1];
      index += 1;
    }
  }
  for (const required of ["request", "output", "result"]) {
    if (!result[required]) throw new Error(`Missing required argument --${required}`);
  }
  return result;
}

function contained(root, target) {
  const relative = path.relative(root, target);
  return relative && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function validateRequest(request) {
  if (request.contract_version !== CONTRACT_VERSION) {
    throw new Error(`Unsupported contract version: ${request.contract_version || "missing"}`);
  }
  if (request.artifact_type !== "slide_deck") {
    throw new Error(`Unsupported artifact type: ${request.artifact_type || "missing"}`);
  }
  if (!request.plan || typeof request.plan !== "object" || Array.isArray(request.plan)) {
    throw new Error("The request plan must be an object");
  }
  if (!Array.isArray(request.plan.slides) || request.plan.slides.length < 2) {
    throw new Error("The presentation plan must contain at least two slides");
  }
}

function imageDimensions(buffer) {
  if (
    buffer.length >= 24
    && buffer[0] === 0x89
    && buffer.toString("ascii", 1, 4) === "PNG"
  ) {
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  }
  if (buffer.length > 4 && buffer[0] === 0xff && buffer[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < buffer.length) {
      if (buffer[offset] !== 0xff) {
        offset += 1;
        continue;
      }
      const marker = buffer[offset + 1];
      if ([
        0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
        0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf,
      ].includes(marker)) {
        return {
          height: buffer.readUInt16BE(offset + 5),
          width: buffer.readUInt16BE(offset + 7),
        };
      }
      const length = buffer.readUInt16BE(offset + 2);
      if (!length || length < 2) break;
      offset += length + 2;
    }
  }
  return { width: 1600, height: 900 };
}

function imageDataUri(file, buffer) {
  const extension = path.extname(file).toLowerCase();
  const mediaType = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
  }[extension] || "image/png";
  return `data:${mediaType};base64,${buffer.toString("base64")}`;
}

async function slideAssets(slide) {
  const raw = Array.isArray(slide?.resolved_assets)
    ? slide.resolved_assets
    : Array.isArray(slide?.resolvedAssets) ? slide.resolvedAssets : [];
  const assets = [];
  for (const item of raw) {
    const file = path.resolve(String(item?.path || ""));
    if (!file || !fsSync.existsSync(file) || !fsSync.statSync(file).isFile()) continue;
    const buffer = await fs.readFile(file);
    const dimensions = imageDimensions(buffer);
    assets.push({
      path: file,
      data: imageDataUri(file, buffer),
      ...dimensions,
      id: String(item.id || path.basename(file)),
      credit: String(item.credit || item.creator || ""),
      sourceUrl: String(item.source_url || item.sourceUrl || ""),
    });
  }
  return assets;
}

function preparePlan(plan, options) {
  const copy = structuredClone(plan);
  if (options.include_charts === false) {
    for (const slide of copy.slides) {
      slide.chart = null;
      if (["chart_focus", "dashboard"].includes(slide.layout)) slide.layout = "cards_icon";
    }
  }
  return copy;
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  const requestPath = path.resolve(args.request);
  const outputRoot = path.resolve(args.output);
  const resultPath = path.resolve(args.result);
  if (!contained(outputRoot, resultPath)) {
    throw new Error("--result must be a file contained by --output");
  }
  const request = JSON.parse(await fs.readFile(requestPath, "utf8"));
  validateRequest(request);
  const options = request.options && typeof request.options === "object" ? request.options : {};
  configure(options);
  await fs.mkdir(outputRoot, { recursive: true });
  event("progress", "normalizing", 8, "Validating and repairing the slide plan");
  const sourcePlan = preparePlan(request.plan, options);
  const normalized = normalizeDeck(sourcePlan, {
    topic: String(sourcePlan.title || "Presentation"),
    audience: String(options.audience || "research audience"),
    count: sourcePlan.slides.length,
    presenterName: String(options.presenter_name || options.presenterName || ""),
    organization: String(options.organization || ""),
  });
  const deck = normalized.deck;
  const warnings = [...normalized.warnings];
  const assetsBySlide = await Promise.all(sourcePlan.slides.map(slideAssets));
  const requiredMediaCounts = {
    cover_cinematic: 1,
    editorial_split: 1,
    photo_mosaic: 3,
    quote_photo: 1,
    case_study: 2,
  };
  sourcePlan.slides.forEach((slide, index) => {
    if (!(slide.media_required ?? slide.mediaRequired)) return;
    const needed = requiredMediaCounts[slide.layout] || 1;
    if ((assetsBySlide[index] || []).length < needed) {
      const received = (assetsBySlide[index] || []).length;
      slide.layout = "big_statement";
      slide.media_required = false;
      slide.mediaRequired = false;
      slide.image_queries = [];
      slide.imageQueries = [];
      deck.slides[index].layout = "big_statement";
      assetsBySlide[index] = [];
      warnings.push(
        `Slide ${index + 1} reflowed to big_statement because verified API media was incomplete (${received}/${needed}).`,
      );
    }
  });
  event("diagnostic", "assets_ready", 28, "Resolved slide media received from SourceLens", {
    asset_count: assetsBySlide.flat().length,
  });
  const presentationName = "presentation.pptx";
  const presentationPath = path.join(outputRoot, presentationName);
  event("progress", "rendering", 42, "Rendering editable PowerPoint shapes and charts");
  await buildPowerPoint(deck, assetsBySlide, presentationPath, options);
  const planName = "deck-plan.normalized.json";
  await fs.writeFile(
    path.join(outputRoot, planName),
    JSON.stringify(deck, null, 2),
    "utf8",
  );
  const layoutDistribution = {};
  for (const slide of deck.slides) {
    layoutDistribution[slide.layout] = (layoutDistribution[slide.layout] || 0) + 1;
  }
  const credits = assetsBySlide
    .flat()
    .filter((asset) => asset.sourceUrl || asset.credit)
    .map((asset) => ({ source_url: asset.sourceUrl, credit: asset.credit }));
  const manifest = {
    contract_version: CONTRACT_VERSION,
    status: "ready",
    engine: ENGINE,
    outputs: { pptx_filename: presentationName },
    previews: [],
    diagnostics: [planName],
    metadata: {
      slide_count: deck.slides.length,
      layout_distribution: layoutDistribution,
      supported_layouts: LAYOUTS,
      editable: true,
      editable_charts: true,
      font_family: String(options.font_family || "Aptos"),
      aspect_ratio: String(options.aspect_ratio || "wide"),
      canvas_inches: String(options.aspect_ratio || "wide") === "standard"
        ? { width: 10, height: 7.5 }
        : { width: 13.333, height: 7.5 },
      media_credits: credits,
    },
    warnings,
  };
  await fs.writeFile(resultPath, JSON.stringify(manifest, null, 2), "utf8");
  event("progress", "completed", 100, "PowerPoint rendering completed", {
    slide_count: deck.slides.length,
    output: presentationName,
  });
}

main().catch(async (error) => {
  event("diagnostic", "failed", 100, error.message, { name: error.name });
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
