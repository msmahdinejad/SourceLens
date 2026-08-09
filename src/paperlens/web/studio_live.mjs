const SETTLED_STUDIO_STATUSES = new Set([
  "ready", "failed", "cancelled", "interrupted", "script_ready", "plan_ready",
]);
const ACTIVE_STUDIO_STATUSES = new Set(["queued", "gathering", "drafting", "generating"]);

function artifactRevision(item) {
  const value = Number(item?.revision);
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

function mergeArtifactState(existing, incoming) {
  if (!existing) return {...incoming};
  const previousRevision = artifactRevision(existing);
  const incomingRevision = artifactRevision(incoming);
  if (previousRevision > 0 && incomingRevision > 0 && incomingRevision < previousRevision) {
    return {...existing};
  }
  const newerRevision = incomingRevision > previousRevision;
  const incomingStatus = incoming.status || existing.status;
  const terminalRegression = !newerRevision
    && SETTLED_STUDIO_STATUSES.has(String(existing.status || ""))
    && !SETTLED_STUDIO_STATUSES.has(String(incomingStatus || ""));
  const previousProgress = Number(existing.progress);
  const incomingProgress = Number(incoming.progress);
  const progress = newerRevision
    ? (Number.isFinite(incomingProgress) ? incomingProgress : previousProgress)
    : Math.max(
      Number.isFinite(previousProgress) ? previousProgress : 0,
      Number.isFinite(incomingProgress) ? incomingProgress : 0,
    );
  return {
    ...existing,
    ...incoming,
    status: terminalRegression ? existing.status : incomingStatus,
    stage: terminalRegression ? existing.stage : (incoming.stage || existing.stage),
    progress: Math.max(0, Math.min(100, progress)),
    revision: Math.max(previousRevision, incomingRevision),
  };
}

export function applyStudioSnapshot(state, payload) {
  const previous = new Map(
    (state.studioArtifacts || []).map((item) => [String(item.artifact_id), item]),
  );
  const items = Array.isArray(payload) ? payload : (payload?.items || []);
  const mergedItems = items.map((item) => {
    const existing = previous.get(String(item.artifact_id));
    return {
      ...mergeArtifactState(existing, item),
      live_events: existing?.live_events || item.live_events || [],
    };
  });
  const incomingIds = new Set(items.map((item) => String(item.artifact_id)));
  const retainedActive = [...previous.values()].filter(
    (item) => !incomingIds.has(String(item.artifact_id))
      && ACTIVE_STUDIO_STATUSES.has(String(item.status || "")),
  );
  state.studioArtifacts = [...retainedActive, ...mergedItems];
  state.studioNextCursor = Array.isArray(payload) ? null : (payload?.next_cursor ?? null);
  state.studioConnection = "connected";
  return state.studioArtifacts;
}

export function applyStudioEvent(state, artifactId, update) {
  const current = (state.studioArtifacts || []).find(
    (artifact) => String(artifact.artifact_id) === String(artifactId),
  );
  if (!current) return null;
  const status = update.details?.artifact_status || current.status;
  const progress = Number(update.details?.artifact_progress);
  const revision = Number(update.details?.artifact_revision);
  const merged = mergeArtifactState(current, {
    artifact_id: current.artifact_id,
    stage: update.stage || current.stage,
    status,
    progress: Number.isFinite(progress) ? progress : current.progress,
    revision: Number.isFinite(revision) ? revision : current.revision,
  });
  Object.assign(current, merged);
  const events = current.live_events || [];
  current.live_events = [
    ...events.filter((event) => event.sequence !== update.sequence),
    update,
  ].slice(-50);
  state.studioMutationRevision = Number(state.studioMutationRevision || 0) + 1;
  return current;
}

export function mergeStudioArtifactDetail(state, item) {
  const existing = (state.studioArtifacts || []).find(
    (artifact) => String(artifact.artifact_id) === String(item.artifact_id),
  );
  const events = new Map();
  for (const event of [...(item.live_events || []), ...(existing?.live_events || [])]) {
    events.set(Number(event.sequence), event);
  }
  const merged = mergeArtifactState(existing, item);
  merged.live_events = [...events.values()]
    .sort((left, right) => Number(left.sequence) - Number(right.sequence))
    .slice(-50);
  state.studioArtifacts = [
    merged,
    ...(state.studioArtifacts || []).filter(
      (artifact) => String(artifact.artifact_id) !== String(item.artifact_id),
    ),
  ];
  state.studioMutationRevision = Number(state.studioMutationRevision || 0) + 1;
  return merged;
}

export function isCurrentWorkspaceRequest(state, workspaceId, revision) {
  return String(state.activeWorkspaceId || "") === String(workspaceId || "")
    && Number(state.workspaceRevision) === Number(revision);
}

export function isCurrentStudioRequest(state, workspaceId, revision, mutationRevision) {
  return isCurrentWorkspaceRequest(state, workspaceId, revision)
    && Number(state.studioMutationRevision || 0) === Number(mutationRevision || 0);
}

export function applyStudioMutation(state, workspaceId, revision, item) {
  if (!isCurrentWorkspaceRequest(state, workspaceId, revision)) return null;
  const existing = (state.studioArtifacts || []).find(
    (artifact) => String(artifact.artifact_id) === String(item.artifact_id),
  );
  const merged = mergeArtifactState(existing, item);
  const events = new Map();
  for (const event of [...(item.live_events || []), ...(existing?.live_events || [])]) {
    events.set(Number(event.sequence), event);
  }
  merged.live_events = [...events.values()]
    .sort((left, right) => Number(left.sequence) - Number(right.sequence))
    .slice(-50);
  state.studioArtifacts = [
    merged,
    ...(state.studioArtifacts || []).filter(
      (artifact) => String(artifact.artifact_id) !== String(item.artifact_id),
    ),
  ].sort((left, right) => new Date(right.created_at) - new Date(left.created_at));
  state.studioMutationRevision = Number(state.studioMutationRevision || 0) + 1;
  return merged;
}

export function markStudioReconnecting(state) {
  state.studioConnection = "reconnecting";
  return state.studioArtifacts;
}

export function studioStatusCategory(status) {
  const value = String(status || "").toLowerCase();
  if (["ready", "failed", "cancelled", "interrupted"].includes(value)) return value;
  if (["script_ready", "plan_ready"].includes(value)) return "review";
  return "preparing";
}

export function patchKeyedStudioRows(container, items, createRow, updateRow) {
  const scrollTop = container.scrollTop;
  const existing = new Map(
    Array.from(container.children).map((row) => [row.dataset.studioKey, row]),
  );
  items.forEach((item, index) => {
    const key = String(item.artifact_id);
    const row = existing.get(key) || createRow(item);
    row.dataset.studioKey = key;
    updateRow(row, item);
    existing.delete(key);
    if (container.children[index] !== row) {
      container.insertBefore(row, container.children[index] || null);
    }
  });
  existing.forEach((row) => row.remove());
  container.scrollTop = scrollTop;
}
