import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import { CONFIG } from "./config.js";
import { cleanText, fetchWithRetry, hash } from "./utils.js";

function readImageDimensions(buffer) {
  if (buffer.length >= 24 && buffer[0] === 0x89 && buffer.toString("ascii", 1, 4) === "PNG") {
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  }
  if (buffer.length > 4 && buffer[0] === 0xff && buffer[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < buffer.length) {
      if (buffer[offset] !== 0xff) { offset += 1; continue; }
      const marker = buffer[offset + 1];
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
        return { height: buffer.readUInt16BE(offset + 5), width: buffer.readUInt16BE(offset + 7) };
      }
      const length = buffer.readUInt16BE(offset + 2);
      if (!length || length < 2) break;
      offset += length + 2;
    }
  }
  return { width: 1920, height: 1080 };
}

async function searchPexelsPhotos(query) {
  const url = new URL("https://api.pexels.com/v1/search");
  url.searchParams.set("query", query);
  url.searchParams.set("orientation", "landscape");
  url.searchParams.set("size", "large");
  url.searchParams.set("per_page", "30");
  const response = await fetchWithRetry(url.toString(), { headers: { Authorization: CONFIG.pexelsApiKey } }, `Pexels photo: ${query}`, 3);
  if (!response.ok) throw new Error(`Pexels Photo API (${response.status}): ${(await response.text()).slice(0, 400)}`);
  return (await response.json()).photos || [];
}

async function searchPexelsVideos(query) {
  const url = new URL("https://api.pexels.com/videos/search");
  url.searchParams.set("query", query);
  url.searchParams.set("orientation", "landscape");
  url.searchParams.set("size", "medium");
  url.searchParams.set("per_page", "30");
  const response = await fetchWithRetry(url.toString(), { headers: { Authorization: CONFIG.pexelsApiKey } }, `Pexels video: ${query}`, 3);
  if (!response.ok) throw new Error(`Pexels Video API (${response.status}): ${(await response.text()).slice(0, 400)}`);
  return (await response.json()).videos || [];
}

function queryWords(value) {
  return new Set(cleanText(value).toLowerCase().split(/[^a-z0-9]+/).filter((word) => word.length > 2));
}
function lexicalScore(query, text) {
  const wanted = queryWords(query);
  const available = queryWords(text);
  if (!wanted.size) return 0;
  let matched = 0;
  for (const word of wanted) if (available.has(word)) matched += 1;
  return matched / wanted.size;
}

function choosePhoto(photos, usedIds, query) {
  const source = photos.filter((item) => !usedIds.has(`photo:${item.id}`));
  const candidates = source.length ? source : photos;
  return candidates.map((photo, index) => {
    const ratio = Number(photo.width || 1) / Number(photo.height || 1);
    const ratioScore = 1 / (1 + Math.abs(Math.log(ratio / (16 / 9))));
    const pixels = Number(photo.width || 0) * Number(photo.height || 0);
    const resolutionScore = Math.min(1, pixels / 8_000_000);
    const relevance = lexicalScore(query, `${photo.alt || ""} ${photo.url || ""}`);
    const rankScore = 1 / (1 + index * 0.08);
    return { photo, score: ratioScore * 0.42 + resolutionScore * 0.23 + relevance * 0.2 + rankScore * 0.15 };
  }).sort((a, b) => b.score - a.score)[0]?.photo || null;
}

function bestVideoFile(video, targetWidth = 1920) {
  const files = (video.video_files || []).filter((file) => String(file.file_type || "").toLowerCase().includes("mp4") && file.link);
  return files.map((file) => {
    const width = Number(file.width || 0);
    const height = Number(file.height || 0);
    const landscape = width > height ? 1 : 0;
    const enough = width >= targetWidth ? 1 : width >= 1280 ? 0.92 : width >= 960 ? 0.62 : 0.25;
    const closeness = width ? 1 / (1 + Math.abs(width - targetWidth) / targetWidth) : 0.1;
    const quality = String(file.quality || "").toLowerCase() === "hd" ? 0.25 : 0;
    return { file, score: landscape * 2 + enough + closeness + quality };
  }).sort((a, b) => b.score - a.score)[0]?.file || null;
}

function chooseVideo(videos, usedIds, targetWidth, query, requiredDuration) {
  const source = videos.filter((item) => !usedIds.has(`video:${item.id}`));
  const candidates = source.length ? source : videos;
  return candidates.map((video, index) => {
    const file = bestVideoFile(video, targetWidth);
    const ratio = Number(video.width || file?.width || 1) / Number(video.height || file?.height || 1);
    const ratioScore = 1 / (1 + Math.abs(Math.log(ratio / (16 / 9))));
    const duration = Number(video.duration || 0);
    const durationScore = duration >= requiredDuration + 0.6 ? 1 : duration >= Math.max(3, requiredDuration * 0.65) ? 0.76 : 0.35;
    const relevance = lexicalScore(query, `${video.url || ""} ${video.user?.name || ""}`);
    const rankScore = 1 / (1 + index * 0.07);
    return { video, file, score: file ? ratioScore * 0.33 + durationScore * 0.34 + relevance * 0.13 + rankScore * 0.2 : -1 };
  }).filter((item) => item.file).sort((a, b) => b.score - a.score)[0] || null;
}

async function downloadPhoto(photo, selectedQuery) {
  const sourceUrl = photo.src?.original || photo.src?.large2x || photo.src?.landscape;
  if (!sourceUrl) throw new Error("آدرس عکس Pexels موجود نیست.");
  const key = hash(`photo|${photo.id}|${sourceUrl}`).slice(0, 20);
  const cachedPath = path.join(CONFIG.cacheDir, `pexels-photo-${key}.jpg`);
  if (!fsSync.existsSync(cachedPath)) {
    const response = await fetchWithRetry(sourceUrl, { headers: { Accept: "image/*,*/*" } }, `دانلود عکس ${photo.id}`, 3);
    if (!response.ok) throw new Error(`دانلود عکس ${photo.id}: HTTP ${response.status}`);
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length > CONFIG.maxImageBytes) throw new Error(`حجم عکس ${photo.id} بیش از حد مجاز است.`);
    await fs.writeFile(cachedPath, buffer);
  }
  const dimensions = readImageDimensions(await fs.readFile(cachedPath));
  return {
    kind: "photo", path: cachedPath, ...dimensions, id: String(photo.id), selectedQuery,
    credit: `${photo.photographer || "Pexels creator"} / Pexels`, sourceUrl: photo.url || "",
  };
}

async function downloadVideo(video, file, selectedQuery) {
  const sourceUrl = file.link;
  const key = hash(`video|${video.id}|${sourceUrl}`).slice(0, 20);
  const cachedPath = path.join(CONFIG.cacheDir, `pexels-video-${key}.mp4`);
  if (!fsSync.existsSync(cachedPath)) {
    const response = await fetchWithRetry(sourceUrl, { headers: { Accept: "video/mp4,video/*,*/*" } }, `دانلود ویدیو ${video.id}`, 3);
    if (!response.ok) throw new Error(`دانلود ویدیو ${video.id}: HTTP ${response.status}`);
    const announced = Number(response.headers.get("content-length") || 0);
    if (announced && announced > CONFIG.maxVideoBytes) throw new Error(`حجم ویدیو ${video.id} بیش از حد مجاز است.`);
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length > CONFIG.maxVideoBytes) throw new Error(`حجم ویدیو ${video.id} بیش از حد مجاز است.`);
    await fs.writeFile(cachedPath, buffer);
  }
  return {
    kind: "video", path: cachedPath, width: Number(file.width || video.width || 1920), height: Number(file.height || video.height || 1080),
    duration: Number(video.duration || 0), fps: Number(file.fps || 0), id: String(video.id), selectedQuery,
    credit: `${video.user?.name || "Pexels creator"} / Pexels`, sourceUrl: video.url || "",
  };
}

async function demoPhoto(index) {
  const files = ["team.jpg", "ecommerce.jpg", "cart.jpg"];
  const filePath = path.join(CONFIG.demoDir, files[index % files.length]);
  const dimensions = readImageDimensions(await fs.readFile(filePath));
  return { kind: "photo", path: filePath, ...dimensions, id: `demo-${index}`, selectedQuery: "demo", credit: "Pexels / demo asset", sourceUrl: "" };
}

export function mediaQueryCandidates(scene, index) {
  const values = [
    ...(Array.isArray(scene.mediaQueries) ? scene.mediaQueries : []), scene.mediaQuery, scene.imageQuery,
  ];
  return values.map(cleanText).filter(Boolean).filter((value) => !/[\u0600-\u06FF]/.test(value))
    .filter((value) => (value.match(/[A-Za-z][A-Za-z-]+/g) || []).length >= 2)
    .filter((value, position, array) => array.indexOf(value) === position).slice(0, 8);
}

async function writeDiagnostics(entries) {
  try {
    await fs.mkdir(CONFIG.cacheDir, { recursive: true });
    await fs.writeFile(path.join(CONFIG.cacheDir, "pexels-diagnostics.json"), JSON.stringify({ generatedAt: new Date().toISOString(), entries }, null, 2), "utf8");
  } catch {}
}

export async function resolveVideoMedia(plan, { demo = false, mode = "auto", targetWidth = 1920, sceneDuration = 6 } = {}) {
  const output = [];
  const usedIds = new Set();
  const videoCache = new Map();
  const photoCache = new Map();
  const diagnostics = [];

  function requiredSceneDuration(scene) {
    const requested = Number(scene?.durationSeconds ?? scene?.duration_seconds ?? 0);
    return Number.isFinite(requested) && requested > 0 ? requested : sceneDuration;
  }

  async function findVideo(scene, index) {
    for (const query of mediaQueryCandidates(scene, index)) {
      try {
        if (!videoCache.has(query)) videoCache.set(query, await searchPexelsVideos(query));
        const videos = videoCache.get(query) || [];
        diagnostics.push({ scene: index + 1, type: "video-search", query, results: videos.length });
        const selected = chooseVideo(
          videos,
          usedIds,
          targetWidth,
          query,
          requiredSceneDuration(scene),
        );
        if (!selected) continue;
        try {
          const asset = await downloadVideo(selected.video, selected.file, query);
          usedIds.add(`video:${selected.video.id}`);
          diagnostics.push({ scene: index + 1, type: "video-selected", query, id: selected.video.id, width: asset.width, height: asset.height, duration: asset.duration });
          return asset;
        } catch (error) {
          diagnostics.push({ scene: index + 1, type: "video-download-error", query, id: selected.video.id, error: error.message });
        }
      } catch (error) {
        diagnostics.push({ scene: index + 1, type: "video-search-error", query, error: error.message });
        console.log(`هشدار ویدیوی صحنه ${index + 1} برای «${query}»: ${error.message}`);
      }
    }
    return null;
  }

  async function findPhoto(scene, index) {
    for (const query of mediaQueryCandidates(scene, index)) {
      try {
        if (!photoCache.has(query)) photoCache.set(query, await searchPexelsPhotos(query));
        const photos = photoCache.get(query) || [];
        diagnostics.push({ scene: index + 1, type: "photo-search", query, results: photos.length });
        const photo = choosePhoto(photos, usedIds, query);
        if (!photo) continue;
        try {
          const asset = await downloadPhoto(photo, query);
          usedIds.add(`photo:${photo.id}`);
          diagnostics.push({ scene: index + 1, type: "photo-selected", query, id: photo.id, width: asset.width, height: asset.height });
          return asset;
        } catch (error) {
          diagnostics.push({ scene: index + 1, type: "photo-download-error", query, id: photo.id, error: error.message });
        }
      } catch (error) {
        diagnostics.push({ scene: index + 1, type: "photo-search-error", query, error: error.message });
        console.log(`هشدار عکس صحنه ${index + 1} برای «${query}»: ${error.message}`);
      }
    }
    return null;
  }

  for (let index = 0; index < plan.scenes.length; index += 1) {
    const scene = plan.scenes[index];
    if (mode === "slides") {
      output.push({ kind: "card", path: "", id: `card-${index + 1}`, selectedQuery: "", fallbackReason: "slides-mode", credit: "", sourceUrl: "" });
      continue;
    }
    if (demo) {
      output.push({ ...(await demoPhoto(index)), fallbackReason: "demo-mode" });
      continue;
    }
    if (!CONFIG.pexelsApiKey) throw new Error("Verified Pexels media is required but no API key is configured");

    const policy = mode === "photo" ? ["photo", "video"] : ["video", "photo"];
    let asset = null;
    const attempts = [];
    for (const kind of policy) {
      attempts.push(kind);
      if (kind === "video") asset = await findVideo(scene, index);
      else if (kind === "photo") asset = await findPhoto(scene, index);
      if (asset) break;
    }
    if (!asset) {
      throw new Error(
        `Scene ${index + 1} could not resolve verified Pexels media for: ${mediaQueryCandidates(scene, index).join(" | ") || "no valid query"}`,
      );
    }
    asset.fallbackReason = asset.kind === "video" ? "video-match" : asset.kind === "photo" ? "video-unavailable-photo-match" : "no-downloadable-media";
    diagnostics.push({ scene: index + 1, type: "final-media", result: asset.kind, attempts, reason: asset.fallbackReason, selectedQuery: asset.selectedQuery || "" });
    output.push(asset);
  }

  await writeDiagnostics(diagnostics);
  return output;
}
