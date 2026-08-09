import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import process from "node:process";

import { CONFIG, configure } from "./config.js";
import { muxAudioVersions, writeAudioCredits } from "./music.js";
import { directVideoPlan } from "./video-director.js";
import { probeVideoQuality, renderSceneOverlays, renderVideo, videoDimensions, writeVideoContentFiles } from "./video-render.js";
import { cleanText, slugify } from "./utils.js";

const CONTRACT_VERSION = "paperlens.visual-engine.v1";
const ENGINE = {
  name: "sourcelens-video-renderer",
  version: "7.0.4-paperlens",
};

function event(type, stage, progress, message, details = {}) {
  process.stdout.write(`${JSON.stringify({ type, stage, progress, message, details })}\n`);
}

function argumentsFrom(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (["--request", "--output", "--result"].includes(value)) {
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
  if (request.artifact_type !== "video_overview") {
    throw new Error(`Unsupported artifact type: ${request.artifact_type || "missing"}`);
  }
  if (!request.plan || typeof request.plan !== "object" || Array.isArray(request.plan)) {
    throw new Error("The request plan must be an object");
  }
  if (!Array.isArray(request.plan.scenes) || request.plan.scenes.length < 1) {
    throw new Error("The video plan must contain at least one scene");
  }
}

function camelVisual(raw = {}) {
  return {
    ...raw,
    hierarchy: raw.hierarchy ?? raw.visual_hierarchy ?? "balanced",
    spacing: raw.spacing ?? "balanced",
    emphasis: raw.emphasis ?? raw.text_emphasis ?? "balanced",
    transitionDuration: raw.transitionDuration ?? raw.transition_duration,
    decorativeSeed: raw.decorativeSeed ?? raw.decorative_seed,
    typing: raw.typing ? {
      ...raw.typing,
      cursorHold: raw.typing.cursorHold ?? raw.typing.cursor_hold,
      cursorBlink: raw.typing.cursorBlink ?? raw.typing.cursor_blink,
      initialDelay: raw.typing.initialDelay ?? raw.typing.initial_delay,
    } : undefined,
  };
}

function camelScene(raw = {}) {
  return {
    ...raw,
    onScreenText: raw.onScreenText ?? raw.on_screen_text ?? "",
    mediaQuery: raw.mediaQuery ?? raw.media_query ?? raw.image_query ?? "",
    mediaQueries: raw.mediaQueries ?? raw.media_queries ?? raw.image_queries ?? [],
    mediaPreference: raw.mediaPreference ?? raw.media_preference ?? "video",
    visualIntent: raw.visualIntent ?? raw.visual_intent ?? raw.visual?.intent ?? "footage",
    focalPoint: raw.focalPoint ?? raw.focal_point ?? "center",
    resolvedMedia: raw.resolvedMedia ?? raw.resolved_media ?? null,
    visual: camelVisual(raw.visual || {}),
  };
}

function preparePlan(plan, options, artifactId) {
  const source = {
    ...structuredClone(plan),
    scenes: plan.scenes.map(camelScene),
  };
  const directed = directVideoPlan(source, {
    style: String(options.video_style || options.style || "auto").replaceAll("_", "-"),
    energy: String(options.pace || options.energy || "auto"),
    seed: String(options.seed || artifactId || "paperlens"),
    mediaMode: String(options.media_mode || "auto"),
    transitionStyle: String(options.transition_style || "auto"),
  });
  directed.scenes = directed.scenes.map((scene, index) => ({
    ...scene,
    resolvedMedia: source.scenes[index]?.resolvedMedia || null,
    citations: source.scenes[index]?.citations || [],
    showCitations: options.show_citations !== false,
    presenter: index === 0 || index === directed.scenes.length - 1 ? {
      name: cleanText(options.presenter_name || ""),
      organization: cleanText(options.organization || ""),
    } : null,
  }));
  directed.presenter = {
    name: cleanText(options.presenter_name || ""),
    organization: cleanText(options.organization || ""),
  };
  return directed;
}

function durationSeconds(options) {
  const explicit = Number(options.duration_seconds || 0);
  const legacy = Number(options.duration_minutes || 0) * 60;
  const requested = explicit > 0 ? explicit : legacy > 0 ? legacy : 180;
  return Math.max(30, Math.min(1_200, Number.isFinite(requested) ? requested : 180));
}

function localMedia(scene, index) {
  const raw = scene.resolvedMedia;
  if (!raw || typeof raw !== "object") return null;
  const kind = ["video", "photo", "card"].includes(raw.kind) ? raw.kind : "card";
  const file = raw.path ? path.resolve(String(raw.path)) : "";
  if (kind !== "card" && (!file || !fsSync.existsSync(file) || !fsSync.statSync(file).isFile())) {
    return null;
  }
  return {
    kind,
    path: kind === "card" ? "" : file,
    width: Number(raw.width || 1920),
    height: Number(raw.height || 1080),
    duration: Number(raw.duration || 0),
    id: String(raw.id || `resolved-${index + 1}`),
    selectedQuery: String(raw.selected_query || raw.selectedQuery || ""),
    fallbackReason: String(raw.fallback_reason || raw.fallbackReason || ""),
    credit: String(raw.credit || raw.creator || ""),
    sourceUrl: String(raw.source_url || raw.sourceUrl || ""),
    licenseUrl: String(raw.license_url || raw.licenseUrl || ""),
    cacheHit: Boolean(raw.cache_hit ?? raw.cacheHit),
    byteCount: Number(raw.byte_count ?? raw.byteCount ?? 0),
    attempts: Number(raw.attempts ?? 0),
  };
}

async function resolveMedia(plan) {
  const provided = plan.scenes.map(localMedia);
  if (provided.every(Boolean)) return provided;
  const resolved = [...provided];
  for (let index = 0; index < plan.scenes.length; index += 1) {
    if (provided[index]) continue;
    const scene = plan.scenes[index];
    const requiresApiMedia = Boolean(scene.mediaRequired ?? scene.media_required);
    if (requiresApiMedia) {
      throw new Error(
        `Scene ${index + 1} requires verified API media prepared by SourceLens, but none was provided`,
      );
    }
    resolved[index] = {
      kind: "card",
      path: "",
      id: `card-${index + 1}`,
      selectedQuery: "",
      fallbackReason: "no-external-media-required",
      credit: "",
      sourceUrl: "",
      licenseUrl: "",
      cacheHit: false,
      byteCount: 0,
      attempts: 0,
    };
  }
  return resolved;
}

function relativeFile(root, file) {
  if (!file) return "";
  const resolved = path.resolve(file);
  if (!contained(root, resolved)) throw new Error(`Generated file escapes the output root: ${resolved}`);
  return path.relative(root, resolved).replaceAll("\\", "/");
}

async function addIfFile(collection, root, file) {
  if (file && fsSync.existsSync(file) && fsSync.statSync(file).isFile()) {
    collection.push(relativeFile(root, file));
  }
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  const requestPath = path.resolve(args.request);
  const outputRoot = path.resolve(args.output);
  const resultPath = path.resolve(args.result);
  const resultIsContained = contained(outputRoot, resultPath);
  const resultIsSibling = path.dirname(resultPath) === path.dirname(outputRoot);
  if (!resultIsContained && !resultIsSibling) {
    throw new Error("--result must be inside --output or beside it");
  }
  const request = JSON.parse(await fs.readFile(requestPath, "utf8"));
  validateRequest(request);
  const options = request.options && typeof request.options === "object" ? request.options : {};
  configure(options, outputRoot);
  await Promise.all([
    fs.mkdir(outputRoot, { recursive: true }),
    fs.mkdir(CONFIG.cacheDir, { recursive: true }),
  ]);

  event("progress", "directing", 8, "Applying SourceLens visual direction");
  const plan = preparePlan(request.plan, options, request.artifact_id);
  const diagnosticsDir = path.join(outputRoot, "diagnostics");
  await fs.mkdir(diagnosticsDir, { recursive: true });
  const planPath = path.join(diagnosticsDir, "video-plan.json");
  await fs.writeFile(planPath, `${JSON.stringify(plan, null, 2)}\n`, "utf8");

  const primaryName = "video-overview.mp4";
  const primaryPath = path.join(outputRoot, primaryName);
  if (options.dry_run === true) {
    await fs.writeFile(primaryPath, Buffer.alloc(0));
    const response = {
      contract_version: CONTRACT_VERSION,
      status: "ready",
      engine: ENGINE,
      outputs: { video_filename: primaryName },
      previews: [],
      diagnostics: [relativeFile(outputRoot, planPath)],
      metadata: {
        scene_count: plan.scenes.length,
        duration_seconds: durationSeconds(options),
        direction: plan.direction,
        dry_run: true,
      },
      warnings: [],
    };
    await fs.writeFile(resultPath, `${JSON.stringify(response, null, 2)}\n`, "utf8");
    event("progress", "completed", 100, "SourceLens dry run completed", { scene_count: plan.scenes.length });
    return;
  }

  const duration = durationSeconds(options);
  const quality = videoDimensions(options);
  const fps = [24, 25, 30, 50, 60].includes(Number(options.fps)) ? Number(options.fps) : 30;
  const mediaStarted = performance.now();
  event("progress", "resolving_media", 18, "Resolving scene media with video-first fallbacks");
  const media = await resolveMedia(plan);
  const mediaCounts = media.reduce((counts, item) => {
    counts[item.kind] = (counts[item.kind] || 0) + 1;
    return counts;
  }, {});
  const mediaMetrics = {
    duration_ms: Math.round(performance.now() - mediaStarted),
    cache_hit: media.every((item) => Boolean(item.cacheHit ?? item.cache_hit)),
    byte_count: media.reduce((sum, item) => sum + Number(item.byteCount ?? item.byte_count ?? 0), 0),
    attempt: media.reduce((sum, item) => sum + Number(item.attempts || 0), 0),
  };
  event("progress", "resolving_media", 33, "Scene media resolved", mediaMetrics);

  const renderOptions = {
    duration,
    fps,
    overlayWorkers: options.overlay_workers ?? process.env.SOURCELENS_OVERLAY_WORKERS,
    renderWorkers: options.render_workers ?? process.env.SOURCELENS_RENDER_WORKERS,
    ...quality,
  };
  event("progress", "rendering_overlays", 34, "Rendering RTL layouts and Persian typewriter overlays");
  const overlays = await renderSceneOverlays(plan, media, outputRoot, renderOptions);
  event("progress", "rendering_overlays", 51, "RTL overlays rendered", overlays.metrics);
  const silentPath = path.join(outputRoot, `${slugify(plan.title || "video")}-silent.mp4`);
  event("progress", "rendering_video", 52, "Compositing cinematic motion and transitions");
  const timing = await renderVideo(media, overlays, silentPath, plan, renderOptions);
  event("progress", "rendering_video", 77, "Scene video and transitions rendered", {
    duration_ms: timing.metrics.segment_wall_ms + timing.metrics.composition_ms,
    ...timing.metrics,
  });

  const requestedMusic = String(options.music || "off").toLowerCase();
  event("progress", "mixing_audio", 78, "Mixing SourceLens-prepared narration and music");
  const preparedMusic = String(options.music_audio || "");
  const provenance = options.music_provenance && typeof options.music_provenance === "object"
    ? options.music_provenance
    : {};
  const music = preparedMusic && fsSync.existsSync(preparedMusic)
    ? {
        status: "ready",
        provider: "sourcelens-prepared",
        outputPath: path.resolve(preparedMusic),
        selected: {
          title: String(provenance.title || ""),
          artist: String(provenance.artist || ""),
          sourceUrl: String(provenance.source_url || ""),
          licenseUrl: String(provenance.license_url || ""),
          contentIdStatus: "unknown",
          shareAlike: /sharealike|by-sa|nc-sa/i.test(String(provenance.license_url || "")),
        },
      }
    : {
        status: requestedMusic === "off" ? "disabled" : "unavailable",
        provider: "sourcelens-prepared",
        reason: requestedMusic === "off" ? "" : "No licensed music was prepared by SourceLens.",
      };
  if (requestedMusic === "required" && music.status !== "ready") {
    throw new Error(`Required background music is unavailable: ${music.reason || music.status}`);
  }
  const audio = await muxAudioVersions(silentPath, music, outputRoot, {
    voiceoverAudio: String(options.voiceover_audio || ""),
    voiceoverSourceDuration: Number(options.voiceover_source_duration_seconds || 0),
    duration: timing.duration,
  });
  const audioCreditsPath = await writeAudioCredits(music, outputRoot);
  const sidecars = await writeVideoContentFiles(plan, media, outputRoot, timing, { music, ...audio });
  const selectedVideo = audio.finalVideoPath || audio.musicVideoPath || silentPath;
  if (path.resolve(selectedVideo) !== path.resolve(primaryPath)) await fs.copyFile(selectedVideo, primaryPath);
  const qualityGate = probeVideoQuality(primaryPath, {
    width: quality.width,
    height: quality.height,
    duration: timing.duration,
    expectAudio: Boolean(audio.finalVideoPath || audio.musicVideoPath),
  });

  const outputs = {
    video_filename: primaryName,
    timeline_filename: relativeFile(outputRoot, sidecars.timelinePath),
  };
  if (options.include_captions !== false) outputs.captions_filename = relativeFile(outputRoot, sidecars.srtPath);
  if (String(options.voiceover_audio || "") && fsSync.existsSync(String(options.voiceover_audio))) {
    const narrationName = `narration${path.extname(String(options.voiceover_audio)) || ".wav"}`;
    const narrationPath = path.join(outputRoot, narrationName);
    if (path.resolve(String(options.voiceover_audio)) !== narrationPath) await fs.copyFile(String(options.voiceover_audio), narrationPath);
    outputs.narration_filename = narrationName;
  }
  const diagnostics = [relativeFile(outputRoot, planPath)];
  for (const file of [sidecars.voiceoverPath, sidecars.contentPath, sidecars.creditsPath, audioCreditsPath]) {
    await addIfFile(diagnostics, outputRoot, file);
  }
  const credits = media.filter((item) => item.credit || item.sourceUrl).map((item) => ({
    kind: item.kind,
    credit: item.credit || "",
    source_url: item.sourceUrl || "",
  }));
  const warnings = [];
  if (music.status !== "ready" && requestedMusic !== "off") warnings.push(`Background music: ${music.reason || music.status}`);
  media.forEach((item, index) => {
    if (item.kind === "card" && item.fallbackReason && !["slides-mode", "motion-graphics-requested"].includes(item.fallbackReason)) {
      warnings.push(`Scene ${index + 1}: ${item.fallbackReason}`);
    }
  });
  const response = {
    contract_version: CONTRACT_VERSION,
    status: "ready",
    engine: ENGINE,
    outputs,
    previews: [],
    diagnostics: [...new Set(diagnostics)],
    metadata: {
      scene_count: plan.scenes.length,
      duration_seconds: timing.duration,
      resolution: `${quality.width}x${quality.height}`,
      fps,
      transition_mode: timing.transitionMode,
      direction: plan.direction,
      media_distribution: mediaCounts,
      media_credits: credits,
      font_family: CONFIG.font,
      music_status: music.status,
      typewriter_scene_count: overlays.filter((item) => item.typewriter).length,
      narration_fit: audio.voiceoverFit,
      quality_gate: qualityGate,
      stage_metrics: {
        media: mediaMetrics,
        overlays: overlays.metrics,
        video: timing.metrics,
      },
    },
    warnings,
  };
  await fs.writeFile(resultPath, `${JSON.stringify(response, null, 2)}\n`, "utf8");
  event("progress", "completed", 100, "SourceLens video rendering completed", {
    scene_count: plan.scenes.length,
    duration_seconds: timing.duration,
    media_distribution: mediaCounts,
  });
}

main().catch((error) => {
  event("diagnostic", "failed", 100, error.message, { name: error.name });
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
