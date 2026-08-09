import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const defaultFixture = path.join(root, "tests", "fixtures", "visual_performance", "video_60s.json");
const engine = path.join(root, "src", "paperlens", "renderers", "video_engine", "entrypoint.mjs");

function option(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function runEngine(requestPath, outputPath, resultPath) {
  return new Promise((resolve, reject) => {
    const started = performance.now();
    const events = [];
    let stdout = "";
    let stderr = "";
    const child = spawn(process.execPath, [
      engine,
      "--request", requestPath,
      "--output", outputPath,
      "--result", resultPath,
    ], { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      let newline = stdout.indexOf("\n");
      while (newline >= 0) {
        const line = stdout.slice(0, newline).trim();
        stdout = stdout.slice(newline + 1);
        try {
          const event = JSON.parse(line);
          if (event.type === "progress") events.push({ ...event, elapsed_seconds: (performance.now() - started) / 1000 });
        } catch {
          // Renderer diagnostics may also use stdout; only contract events are timed.
        }
        newline = stdout.indexOf("\n");
      }
    });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(`Renderer exited ${code}: ${stderr.slice(-3000)}`));
      else resolve({ elapsed_seconds: (performance.now() - started) / 1000, events });
    });
  });
}

function stageDurations(events, elapsedSeconds) {
  const durations = {};
  for (let index = 0; index < events.length; index += 1) {
    const current = events[index];
    const end = events[index + 1]?.elapsed_seconds ?? elapsedSeconds;
    durations[current.stage] = Number(((durations[current.stage] || 0) + end - current.elapsed_seconds).toFixed(3));
  }
  return durations;
}

async function main() {
  if (spawnSync("ffmpeg", ["-version"], { stdio: "ignore", windowsHide: true }).status !== 0) {
    throw new Error("FFmpeg is required for the visual benchmark");
  }
  const fixture = path.resolve(option("--fixture", defaultFixture));
  const runs = Math.max(1, Number.parseInt(option("--runs", "3"), 10));
  const maximumMedian = Number.parseFloat(option("--max-median", "0"));
  const requestedRoot = option("--output-root");
  const outputRoot = requestedRoot
    ? path.resolve(requestedRoot)
    : await fs.mkdtemp(path.join(os.tmpdir(), "sourcelens-visual-benchmark-"));
  await fs.mkdir(outputRoot, { recursive: true });
  const records = [];
  for (let index = 0; index < runs; index += 1) {
    const runRoot = path.join(outputRoot, `run-${index + 1}`);
    const outputPath = path.join(runRoot, "output");
    const resultPath = path.join(runRoot, "result.json");
    await fs.mkdir(runRoot, { recursive: true });
    const requestPath = path.join(runRoot, "request.json");
    await fs.copyFile(fixture, requestPath);
    const timing = await runEngine(requestPath, outputPath, resultPath);
    const result = JSON.parse(await fs.readFile(resultPath, "utf8"));
    const videoPath = path.join(outputPath, result.outputs.video_filename);
    const stat = await fs.stat(videoPath);
    records.push({
      run: index + 1,
      elapsed_seconds: Number(timing.elapsed_seconds.toFixed(3)),
      output_bytes: stat.size,
      stage_seconds: stageDurations(timing.events, timing.elapsed_seconds),
      renderer_metrics: result.metadata.stage_metrics || {},
      quality_gate: result.metadata.quality_gate,
      sample: videoPath,
    });
  }
  const summary = {
    fixture,
    runs: records,
    median_seconds: Number(median(records.map((item) => item.elapsed_seconds)).toFixed(3)),
    output_root: outputRoot,
  };
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  if (maximumMedian > 0 && summary.median_seconds > maximumMedian) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
