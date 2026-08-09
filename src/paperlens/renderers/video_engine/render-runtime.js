import os from "node:os";

export async function boundedMap(values, concurrency, worker) {
  const items = Array.from(values);
  const results = new Array(items.length);
  let cursor = 0;
  const limit = Math.max(1, Math.min(items.length || 1, Math.floor(Number(concurrency) || 1)));
  async function consume() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: limit }, consume));
  return results;
}

export function recommendedRenderWorkers({
  cpuCount = os.cpus().length,
  memoryBytes = os.totalmem(),
} = {}) {
  const cpuBound = Math.max(1, Math.floor(Number(cpuCount || 1) / 4));
  const memoryBound = Math.max(1, Math.floor(Number(memoryBytes || 0) / (3 * 1024 ** 3)));
  return Math.max(1, Math.min(3, cpuBound, memoryBound));
}

export function configuredWorkers(value, fallback) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) ? Math.max(1, Math.min(4, parsed)) : fallback;
}
