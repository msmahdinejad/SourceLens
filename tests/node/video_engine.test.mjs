import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ENGINE = path.join(ROOT, "src/paperlens/renderers/video_engine");
const engineModule = (name) => pathToFileURL(path.join(ENGINE, name)).href;

test("render work uses a deterministic bounded pool", async () => {
  const { boundedMap, recommendedRenderWorkers } = await import(engineModule("render-runtime.js"));
  let active = 0;
  let peak = 0;
  const result = await boundedMap([1, 2, 3, 4, 5, 6], 3, async (value) => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 30));
    active -= 1;
    return value * 2;
  });

  assert.deepEqual(result, [2, 4, 6, 8, 10, 12]);
  assert.equal(peak, 3);
  assert.ok(recommendedRenderWorkers({ cpuCount: 16, memoryBytes: 32 * 1024 ** 3 }) <= 4);
  assert.ok(recommendedRenderWorkers({ cpuCount: 2, memoryBytes: 2 * 1024 ** 3 }) >= 1);
});

test("director is deterministic and preserves structured scene data", async () => {
  const { directVideoPlan } = await import(engineModule("video-director.js"));
  const plan = {
    title: "Persian AI workflow",
    scenes: [
      { title: "شروع", narration: "آغاز روایت" },
      {
        title: "فرایند",
        narration: "سه مرحله",
        visualIntent: "process",
        visual: { steps: ["دریافت", "تحلیل", "نتیجه"] },
      },
      { title: "پایان", narration: "جمع‌بندی" },
    ],
  };
  const first = directVideoPlan(plan, { seed: "stable", energy: "balanced" });
  const second = directVideoPlan(plan, { seed: "stable", energy: "balanced" });
  assert.deepEqual(first, second);
  assert.equal(first.scenes[0].visual.layout, "hero-cinematic");
  assert.equal(first.scenes.at(-1).visual.layout, "closing-statement");
  assert.deepEqual(first.scenes[1].visual.steps, ["دریافت", "تحلیل", "نتیجه"]);
});

test("landscape Persian overlays keep RTL copy and presenter metadata inside the frame", async () => {
  const {renderSceneOverlays} = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-safe-area-"));
  try {
    await renderSceneOverlays(
      {
        duration_seconds: 30,
        theme: {},
        scenes: [{
          title: "معرفی هوش مصنوعی",
          onScreenText: "هوش مصنوعی و کنترل سامانه‌های پیچیده",
          duration_seconds: 30,
          presenter: {name: "محمدصالح مهدی نژاد", organization: "دانشگاه اصفهان"},
          visual: {layout: "hero-cinematic", typing: {enabled: false}},
        }],
      },
      [{kind: "card"}],
      folder,
      {width: 1920, height: 1080, fps: 30, overlayWorkers: 1},
    );
    const svg = await fs.readFile(path.join(folder, "overlay-svg", "overlay-01.svg"), "utf8");
    assert.match(svg, /<text x="1655"[^>]+text-anchor="end"[^>]*>/);
    assert.match(svg, /<text x="1830"[^>]+text-anchor="end"[^>]*>/);
  } finally {
    await fs.rm(folder, {recursive: true, force: true});
  }
});

test("video overlays bound long metadata and reserve the lower safe area", async () => {
  const {renderSceneOverlays} = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-layout-bounds-"));
  try {
    const longToken = "A".repeat(160);
    await renderSceneOverlays(
      {
        duration_seconds: 30,
        theme: {},
        scenes: [{
          title: "A useful grounded title",
          onScreenText: "A concise audience-facing explanation that remains above the footer",
          duration_seconds: 30,
          presenter: {name: longToken, organization: longToken},
          visual: {layout: "lower-third", typing: {enabled: false}},
        }],
      },
      [{kind: "card"}],
      folder,
      {width: 1920, height: 1080, fps: 30, overlayWorkers: 1},
    );
    const svg = await fs.readFile(path.join(folder, "overlay-svg", "overlay-01.svg"), "utf8");
    assert.doesNotMatch(svg, new RegExp(longToken));
    assert.match(svg, /<text x="1680" y="865"[^>]*>/);
    assert.doesNotMatch(svg, /<text x="1680" y="1010"/);
    assert.match(svg, /direction="ltr"/);
  } finally {
    await fs.rm(folder, {recursive: true, force: true});
  }
});

test("citation metadata stays clear of landscape and square progress controls", async () => {
  const {renderSceneOverlays} = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-citation-footer-"));
  try {
    const scene = {
      title: "Grounded conclusion",
      onScreenText: "A concise evidence-based conclusion",
      duration_seconds: 30,
      presenter: {name: "Presenter", organization: "University"},
      citations: ["Document 1 · page 4", "Document 2 · page 7"],
      showCitations: true,
      visual: {layout: "minimal-type", typing: {enabled: false}},
    };
    const landscape = path.join(folder, "landscape");
    const square = path.join(folder, "square");
    await renderSceneOverlays(
      {duration_seconds: 30, theme: {}, scenes: [scene]},
      [{kind: "card"}], landscape,
      {width: 1920, height: 1080, fps: 30, overlayWorkers: 1},
    );
    await renderSceneOverlays(
      {duration_seconds: 30, theme: {}, scenes: [scene]},
      [{kind: "card"}], square,
      {width: 1080, height: 1080, fps: 30, overlayWorkers: 1},
    );
    const landscapeSvg = await fs.readFile(path.join(landscape, "overlay-svg", "overlay-01.svg"), "utf8");
    const squareSvg = await fs.readFile(path.join(square, "overlay-svg", "overlay-01.svg"), "utf8");
    assert.match(landscapeSvg, /<text x="1830" y="958"[^>]+font-size="17"/);
    assert.match(landscapeSvg, /<circle cx="1810" cy="1008" r="25"/);
    assert.match(squareSvg, /<text x="999" y="959"[^>]+font-size="18"/);
    assert.match(squareSvg, /<text x="81" y="1013"[^>]+font-size="20"/);
  } finally {
    await fs.rm(folder, {recursive: true, force: true});
  }
});

test("responsive static and typewriter text constrain wide glyphs to the safe area", async () => {
  const {renderSceneOverlays} = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-responsive-text-bounds-"));
  try {
    const wideTitle = "W".repeat(42);
    const wideMetadata = "W".repeat(120);
    await renderSceneOverlays(
      {
        duration_seconds: 30,
        theme: {},
        scenes: [
          {
            title: wideTitle,
            onScreenText: "Safe body",
            duration_seconds: 15,
            presenter: {name: wideMetadata, organization: wideMetadata},
            visual: {layout: "hero-cinematic", emphasis: "title", typing: {enabled: false}},
          },
          {
            title: wideTitle,
            onScreenText: "Safe body",
            duration_seconds: 15,
            visual: {layout: "closing-statement", emphasis: "title", typing: {enabled: true}},
          },
        ],
      },
      [{kind: "card"}, {kind: "card"}],
      folder,
      {width: 720, height: 1280, fps: 24, overlayWorkers: 1},
    );
    const svg = await fs.readFile(path.join(folder, "overlay-svg", "overlay-01.svg"), "utf8");
    const ass = await fs.readFile(path.join(folder, "overlays", "typewriter-02.ass"), "utf8");
    assert.match(svg, /<text x="666" y="652"[^>]+textLength="612"[^>]*>/);
    assert.match(svg, /<text x="666" y="1146"[^>]+textLength="612"[^>]*>/);
    assert.match(ass, /\\an9\\pos\(666,394\)/);
    assert.match(ass, /\\fscx(?:[1-9]|[1-9][0-9])\b/);
  } finally {
    await fs.rm(folder, {recursive: true, force: true});
  }
});

test("structural video labels split long unbroken tokens inside their cards", async () => {
  const {renderSceneOverlays} = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-structural-labels-"));
  try {
    const longToken = "W".repeat(90);
    await renderSceneOverlays(
      {
        duration_seconds: 30,
        theme: {},
        scenes: [
          {
            title: "Process",
            onScreenText: "Three grounded stages",
            duration_seconds: 15,
            visual: {
              layout: "process",
              steps: [longToken, "Validation", "Delivery"],
              typing: {enabled: false},
            },
          },
          {
            title: "Comparison",
            onScreenText: "Two grounded alternatives",
            duration_seconds: 15,
            visual: {
              layout: "comparison",
              comparison: [longToken, "Verified alternative"],
              typing: {enabled: false},
            },
          },
        ],
      },
      [{kind: "card"}, {kind: "card"}],
      folder,
      {width: 1920, height: 1080, fps: 30, overlayWorkers: 1},
    );
    const processSvg = await fs.readFile(path.join(folder, "overlay-svg", "overlay-01.svg"), "utf8");
    const comparisonSvg = await fs.readFile(path.join(folder, "overlay-svg", "overlay-02.svg"), "utf8");
    assert.doesNotMatch(processSvg, new RegExp(longToken));
    assert.match(processSvg, /<text x="390"[^>]+text-anchor="middle"[^>]*>/);
    assert.doesNotMatch(comparisonSvg, new RegExp(longToken));
    assert.match(comparisonSvg, /<text x="530"[^>]+textLength="600"[^>]+text-anchor="middle"[^>]*>/);
  } finally {
    await fs.rm(folder, {recursive: true, force: true});
  }
});

test("closing statement compacts three-line titles before the rule and body", async () => {
  const {renderSceneOverlays} = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-closing-layout-"));
  try {
    await renderSceneOverlays(
      {
        duration_seconds: 30,
        theme: {},
        scenes: [false, true].map((typing) => ({
          title: "W".repeat(90),
          onScreenText: "A concise closing point that must remain visually separate from the title.",
          duration_seconds: 15,
          visual: {layout: "closing-statement", typing: {enabled: typing}},
        })),
      },
      [{kind: "card"}, {kind: "card"}],
      folder,
      {width: 1920, height: 1080, fps: 30, overlayWorkers: 1},
    );
    const svg = await fs.readFile(path.join(folder, "overlay-svg", "overlay-01.svg"), "utf8");
    assert.match(svg, /<text x="960" y="500"[^>]+textLength="1740"[^>]+font-size="68"[^>]*>/);
    assert.match(svg, /<rect x="760" y="710" width="400"/);
    assert.match(svg, /<text x="960" y="795"[^>]+font-size="34"[^>]*>/);
    assert.match(svg, /<text x="960" y="844"[^>]+font-size="34"[^>]*>/);
    assert.doesNotMatch(svg, /<text x="960" y="585"[^>]+font-size="80"/);
    const typedSvg = await fs.readFile(path.join(folder, "overlay-svg", "overlay-02.svg"), "utf8");
    const typedAss = await fs.readFile(path.join(folder, "overlays", "typewriter-02.ass"), "utf8");
    assert.match(typedSvg, /<rect x="760" y="710" width="400"/);
    assert.match(typedSvg, /<text x="960" y="795"[^>]+font-size="34"[^>]*>/);
    assert.match(typedAss, /Style: Typewriter,Vazirmatn,68,/);
    assert.match(typedAss, /\\an8\\pos\(960,451\)/);
  } finally {
    await fs.rm(folder, {recursive: true, force: true});
  }
});

test("dense landscape layouts reserve vertical space for body and metadata", async () => {
  const {renderSceneOverlays} = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-dense-vertical-"));
  try {
    const denseTitle = "W".repeat(90);
    const denseBody = "Grounded explanation ".repeat(12);
    const layouts = [
      "hero-cinematic", "minimal-type", "glass-card", "spotlight", "editorial", "quote",
    ];
    const scenes = layouts.map((layout) => ({
      title: denseTitle,
      onScreenText: denseBody,
      duration_seconds: 10,
      presenter: {name: "Presenter", organization: "University"},
      visual: {layout, typing: {enabled: false}},
    }));
    await renderSceneOverlays(
      {
        duration_seconds: 30,
        theme: {},
        scenes: [
          ...scenes,
          ...scenes.map((scene) => ({
            ...scene,
            visual: {...scene.visual, typing: {enabled: true}},
          })),
        ],
      },
      [...layouts, ...layouts].map(() => ({kind: "card"})),
      folder,
      {width: 1920, height: 1080, fps: 30, overlayWorkers: 1},
    );
    const hero = await fs.readFile(path.join(folder, "overlay-svg", "overlay-01.svg"), "utf8");
    const minimal = await fs.readFile(path.join(folder, "overlay-svg", "overlay-02.svg"), "utf8");
    const glass = await fs.readFile(path.join(folder, "overlay-svg", "overlay-03.svg"), "utf8");
    const spotlight = await fs.readFile(path.join(folder, "overlay-svg", "overlay-04.svg"), "utf8");
    const editorial = await fs.readFile(path.join(folder, "overlay-svg", "overlay-05.svg"), "utf8");
    const quote = await fs.readFile(path.join(folder, "overlay-svg", "overlay-06.svg"), "utf8");
    assert.match(hero, /<text x="1655" y="530"[^>]+font-size="78"/);
    assert.match(hero, /<text x="1655" y="820"[^>]+font-size="32"/);
    assert.match(hero, /<text x="1655" y="865"[^>]+font-size="32"/);
    assert.match(minimal, /<text x="1685" y="440"[^>]+font-size="68"/);
    assert.match(minimal, /<text x="1630" y="742"[^>]+font-size="34"/);
    assert.match(glass, /<text x="1625" y="490"[^>]+font-size="50"/);
    assert.match(glass, /<rect x="1170" y="695"/);
    assert.match(spotlight, /<text x="960" y="500"[^>]+font-size="66"/);
    assert.match(spotlight, /<text x="960" y="770"[^>]+font-size="31"/);
    assert.match(editorial, /<text x="1605" y="525"[^>]+font-size="64"/);
    assert.match(editorial, /<text x="1605" y="800"[^>]+font-size="32"/);
    assert.match(quote, /<text x="1600" y="500"[^>]+font-size="66"/);
    assert.match(quote, /<text x="1600" y="790"[^>]+font-size="31"/);
    const typed = await Promise.all(layouts.map((_, index) =>
      fs.readFile(path.join(folder, "overlays", `typewriter-${String(index + 7).padStart(2, "0")}.ass`), "utf8")
    ));
    const expected = [
      [78, 1655, 474], [68, 1685, 391], [50, 1625, 454],
      [66, 960, 452], [64, 1660, 479], [66, 1600, 452],
    ];
    typed.forEach((ass, index) => {
      const [size, x, y] = expected[index];
      assert.match(ass, new RegExp(`Style: Typewriter,Vazirmatn,${size},`));
      assert.match(ass, new RegExp(`\\\\pos\\(${x},${y}\\)`));
    });
  } finally {
    await fs.rm(folder, {recursive: true, force: true});
  }
});

test("director honors safe LLM hierarchy and spacing decisions", async () => {
  const { directVideoPlan } = await import(engineModule("video-director.js"));
  const directed = directVideoPlan({
    title: "Semantic direction",
    scenes: [
      { title: "Opening", narration: "Open" },
      {
        title: "Core idea",
        narration: "Explain the central subject",
        visual: { hierarchy: "subject", spacing: "airy", emphasis: "title" },
      },
      { title: "Closing", narration: "Close" },
    ],
  }, { seed: "semantic-direction" });

  assert.equal(directed.scenes[1].visual.hierarchy, "subject");
  assert.equal(directed.scenes[1].visual.spacing, "airy");
  assert.equal(directed.scenes[1].visual.emphasis, "title");
  assert.equal(directed.scenes[1].visual.layout, "spotlight");
});

test("video dimensions are native for landscape portrait and square", async () => {
  const { videoDimensions } = await import(engineModule("video-render.js"));
  assert.deepEqual(videoDimensions({ resolution: "1080p", aspect_ratio: "landscape" }), {
    width: 1920, height: 1080, preset: "medium", crf: 21,
  });
  assert.deepEqual(videoDimensions({ resolution: "1080p", aspect_ratio: "portrait" }), {
    width: 1080, height: 1920, preset: "medium", crf: 21,
  });
  assert.deepEqual(videoDimensions({ resolution: "1080p", aspect_ratio: "square" }), {
    width: 1080, height: 1080, preset: "medium", crf: 21,
  });
});

test("director renders only authored numeric charts and reflows unsupported data scenes", async () => {
  const { directVideoPlan } = await import(engineModule("video-director.js"));
  const grounded = directVideoPlan({
    title: "Measured evidence",
    scenes: [
      { title: "Start", narration: "Start" },
      {
        title: "Result", narration: "Measured result", visualIntent: "data",
        chart: {
          type: "bar", title: "Evaluation", labels: ["Baseline", "Proposed"],
          values: [71, 86], citations: ["S4"], unit: "score",
        },
      },
      { title: "Close", narration: "Close" },
    ],
  }, { seed: "grounded-chart" });
  assert.equal(grounded.scenes[1].visual.layout, "data-bars");
  assert.deepEqual(grounded.scenes[1].visual.chart.values, [71, 86]);
  assert.equal(grounded.scenes[1].visual.chart.illustrative, false);

  const unsupported = directVideoPlan({
    title: "No numeric evidence",
    scenes: [
      { title: "Start", narration: "Start" },
      { title: "Result", narration: "No numbers", visualIntent: "data" },
      { title: "Close", narration: "Close" },
    ],
  }, { seed: "no-fake-chart" });
  assert.notEqual(unsupported.scenes[1].visual.layout, "data-bars");
  assert.equal(unsupported.scenes[1].visual.chart, null);
});

test("director does not invent process, comparison, or timeline labels", async () => {
  const { directVideoPlan } = await import(engineModule("video-director.js"));
  for (const intent of ["process", "comparison", "timeline"]) {
    const directed = directVideoPlan({
      title: `Missing ${intent} evidence`,
      scenes: [
        { title: "Start", narration: "Start" },
        { title: "Insufficient structure", narration: "One statement", visualIntent: intent },
        { title: "Close", narration: "Close" },
      ],
    }, { seed: intent });
    assert.notEqual(directed.scenes[1].visual.layout, intent);
    assert.equal(directed.scenes[1].visual.steps, null);
    assert.equal(directed.scenes[1].visual.comparison, null);
    assert.equal(directed.scenes[1].visual.timeline, null);
  }
});

test("timing supports SourceLens 2-20 minute videos and preserves exact duration", async () => {
  const { computeVideoTiming } = await import(engineModule("video-render.js"));
  const plan = {
    scenes: Array.from({ length: 24 }, () => ({
      visual: { transitionDuration: 0.52 },
    })),
  };
  const timing = computeVideoTiming(plan, { duration: 1_200 });
  assert.equal(timing.duration, 1_200);
  assert.equal(timing.sceneWindows.length, 24);
  assert.equal(timing.sceneWindows.at(-1).nominalEndSeconds, 1_200);
});

test("timing preserves the agent-selected relative duration of each scene", async () => {
  const { computeVideoTiming } = await import(engineModule("video-render.js"));
  const timing = computeVideoTiming({
    scenes: [
      { duration_seconds: 5, visual: { transitionDuration: 0.5 } },
      { duration_seconds: 15, visual: { transitionDuration: 0.5 } },
      { duration_seconds: 10, visual: {} },
    ],
  }, { duration: 30 });

  assert.equal(timing.duration, 30);
  assert.deepEqual(
    timing.sceneWindows.map((window) => window.nominalEndSeconds),
    [5, 20, 30],
  );
  assert.deepEqual(timing.segmentDurations, [5.5, 15.5, 10]);
});

test("Persian typewriter counts grapheme clusters instead of code points", async () => {
  const { buildTypewriterSchedule } = await import(engineModule("video-render.js"));
  const text = "یادگیریِ عمیق";
  const frames = buildTypewriterSchedule(text, { cps: 8, variance: 0 }, 5, "seed");
  const visible = frames.map((frame) => frame.visibleGraphemes);
  assert.equal(visible.at(-1), [...new Intl.Segmenter("fa", { granularity: "grapheme" }).segment(text)].length);
  assert.ok(Math.abs(frames.reduce((sum, frame) => sum + frame.duration, 0) - 5) < 0.001);
});

test("Jamendo scoring prefers narration-safe instrumental music", async () => {
  const { buildMusicBrief, scoreJamendoTrack } = await import(engineModule("music.js"));
  const brief = buildMusicBrief({ direction: { energy: "balanced", profile: "education" }, scenes: [] }, 180);
  const calm = scoreJamendoTrack({
    id: "safe",
    name: "Focused Ambient",
    artist_name: "Artist",
    duration: 240,
    audiodownload_allowed: true,
    musicinfo: { tags: { genres: ["ambient"], vartags: ["instrumental", "calm"] } },
    license_ccurl: "https://creativecommons.org/licenses/by/4.0/",
  }, brief);
  const vocal = scoreJamendoTrack({
    id: "vocal",
    name: "Epic Vocal Song",
    artist_name: "Artist",
    duration: 240,
    audiodownload_allowed: true,
    musicinfo: { tags: { genres: ["rock"], vartags: ["vocal", "dramatic"] } },
    license_ccurl: "https://creativecommons.org/licenses/by/4.0/",
  }, brief);
  assert.ok(calm.score > vocal.score);
  assert.equal(calm.instrumental, true);
});

test("voiceover timing never changes speaking speed", async () => {
  const { voiceoverFitPolicy } = await import(engineModule("music.js"));
  const fit = voiceoverFitPolicy({ sourceDuration: 72, targetDuration: 60 });

  assert.equal(fit.tempoRatio, 1);
  assert.equal(fit.speedChanged, false);
  assert.equal(fit.overflowSeconds, 12);
});

test("entrypoint implements paperlens.visual-engine.v1 dry-run contract", async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-video-"));
  const output = path.join(folder, "output");
  const requestPath = path.join(folder, "request.json");
  const resultPath = path.join(folder, "result.json");
  await fs.writeFile(requestPath, JSON.stringify({
    contract_version: "paperlens.visual-engine.v1",
    artifact_type: "video_overview",
    artifact_id: "artifact-video-1",
    workspace_id: "workspace",
    language: "fa",
    plan: {
      title: "آزمون موتور ویدیو",
      scenes: [
        { title: "شروع", narration: "این یک شروع کوتاه است." },
        { title: "پایان", narration: "این یک پایان کوتاه است." },
      ],
    },
    options: { duration_seconds: 30, dry_run: true, music: "off" },
  }), "utf8");
  const run = spawnSync(process.execPath, [path.join(ENGINE, "entrypoint.mjs"), "--request", requestPath, "--output", output, "--result", resultPath], { encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr);
  const result = JSON.parse(await fs.readFile(resultPath, "utf8"));
  assert.equal(result.contract_version, "paperlens.visual-engine.v1");
  assert.equal(result.status, "ready");
  assert.equal(result.engine.name, "sourcelens-video-renderer");
  assert.equal(result.engine.name, "sourcelens-video-renderer");
  assert.equal(result.metadata.scene_count, 2);
  assert.equal(result.metadata.duration_seconds, 30);
  assert.ok(result.diagnostics.includes("diagnostics/video-plan.json"));
});

test("entrypoint renders an exact native portrait MP4 with quality gate", { timeout: 180_000 }, async (context) => {
  const ffmpeg = spawnSync("ffmpeg", ["-version"], { stdio: "ignore" });
  if (ffmpeg.status !== 0) {
    context.skip("FFmpeg is unavailable");
    return;
  }
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "paperlens-video-portrait-"));
  const output = path.join(folder, "output");
  const requestPath = path.join(folder, "request.json");
  const resultPath = path.join(folder, "result.json");
  try {
    await fs.writeFile(requestPath, JSON.stringify({
      contract_version: "paperlens.visual-engine.v1",
      artifact_type: "video_overview",
      artifact_id: "artifact-video-portrait",
      workspace_id: "workspace",
      language: "fa",
      plan: {
        title: "آزمون ویدیوی عمودی",
        scenes: [
          {
            title: "شروع حرفه‌ای",
            on_screen_text: "چیدمان واقعی و خوانا برای قاب عمودی",
            narration: "شروع",
            duration_seconds: 12,
            resolved_media: { kind: "card" },
            visual: { hierarchy: "title", spacing: "airy", emphasis: "title" },
          },
          {
            title: "جمع‌بندی دقیق",
            on_screen_text: "بدون کشیدگی و بدون برش متن",
            narration: "پایان",
            duration_seconds: 18,
            resolved_media: { kind: "card" },
            visual: { hierarchy: "subject", spacing: "balanced", emphasis: "body" },
          },
        ],
      },
      options: {
        duration_seconds: 30,
        aspect_ratio: "portrait",
        resolution: "720p",
        fps: "24",
        media_mode: "motion_graphics",
        music: "off",
        include_captions: true,
        presenter_name: "ارائه‌دهنده آزمون",
        organization: "دانشگاه آزمون",
      },
    }), "utf8");
    const run = spawnSync(process.execPath, [
      path.join(ENGINE, "entrypoint.mjs"),
      "--request", requestPath,
      "--output", output,
      "--result", resultPath,
    ], { encoding: "utf8", timeout: 170_000 });
    assert.equal(run.status, 0, run.stderr);
    const result = JSON.parse(await fs.readFile(resultPath, "utf8"));
    assert.equal(result.metadata.resolution, "720x1280");
    assert.equal(result.metadata.duration_seconds, 30);
    assert.equal(result.metadata.quality_gate.passed, true);
    assert.equal(result.metadata.quality_gate.width, 720);
    assert.equal(result.metadata.quality_gate.height, 1280);
    assert.ok(Math.abs(result.metadata.quality_gate.durationSeconds - 30) <= 0.2);
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
  }
});

test("downloadable video timeline never exposes host filesystem paths", async () => {
  const { writeVideoContentFiles } = await import(engineModule("video-render.js"));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-public-timeline-"));
  try {
    const privateRoot = path.join("C:\\", "Users", "researcher", "private-workspace");
    const files = await writeVideoContentFiles(
      {
        title: "Public timeline",
        direction: {},
        scenes: [{
          title: "A grounded scene",
          narration: "A cited explanation.",
          duration_seconds: 30,
          visual: {},
        }],
      },
      [{
        kind: "video",
        path: path.join(privateRoot, "media", "scene-01.mp4"),
        credit: "Creator",
        sourceUrl: "https://www.pexels.com/video/1/",
      }],
      folder,
      {
        duration: 30,
        fps: 30,
        width: 1920,
        height: 1080,
        sceneWindows: [{
          startSeconds: 0,
          endSeconds: 30,
          nominalStartSeconds: 0,
          nominalEndSeconds: 30,
          transitionIn: 0,
          transitionOut: 0,
        }],
      },
      {
        silentVideoPath: path.join(privateRoot, "render", "silent.mp4"),
        musicVideoPath: "",
        finalVideoPath: "",
        music: { diagnosticsPath: path.join(privateRoot, "music", "plan.json") },
      },
    );
    const timeline = await fs.readFile(files.timelinePath, "utf8");
    assert.doesNotMatch(timeline, /private-workspace|C:\\\\Users/);
    assert.match(timeline, /"mediaPath": "scene-01\.mp4"/);
    assert.match(timeline, /"silentVideo": "silent\.mp4"/);
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
  }
});
