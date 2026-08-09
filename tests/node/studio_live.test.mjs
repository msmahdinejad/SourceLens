import assert from "node:assert/strict";
import test from "node:test";

import {createApiClient} from "../../src/paperlens/web/api.mjs";
import {escapeHtml} from "../../src/paperlens/web/rendering.mjs";
import {createAppState, VIEW_NAMES} from "../../src/paperlens/web/state.mjs";

import {
  applyStudioEvent,
  applyStudioMutation,
  applyStudioSnapshot,
  markStudioReconnecting,
  mergeStudioArtifactDetail,
  patchKeyedStudioRows,
  studioStatusCategory,
  isCurrentWorkspaceRequest,
  isCurrentStudioRequest,
} from "../../src/paperlens/web/studio_live.mjs";

class FakeRow {
  constructor(key) {
    this.dataset = {studioKey: key};
    this.parentNode = null;
    this.details = {open: false};
    this.input = {selectionStart: 0, selectionEnd: 0, value: ""};
    this.title = "";
  }

  remove() {
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
  }
}

test("frontend modules expose isolated state, API, and rendering behavior", async () => {
  const storage = new Map([["paperlens.workspace.v1", "research"]]);
  const state = createAppState({
    requestedLocale: "en",
    storage: {getItem: (key) => storage.get(key) || null},
  });
  const requests = [];
  const api = createApiClient({
    activeRequests: state.activeRequests,
    fetchImpl: async (path, options) => {
      requests.push({path, options});
      return {
        ok: true,
        headers: {get: () => "application/json"},
        json: async () => ({saved: true}),
      };
    },
  });

  assert.equal(state.activeWorkspaceId, "research");
  assert.equal(state.locale, "en");
  assert.equal(VIEW_NAMES.has("studio"), false);
  assert.equal(escapeHtml('<source id="1">'), "&lt;source id=&quot;1&quot;&gt;");
  assert.deepEqual(await api("/api/demo", {method: "POST", body: {enabled: true}}), {
    saved: true,
  });
  assert.equal(requests[0].options.body, '{"enabled":true}');
  assert.equal(state.activeRequests.size, 0);
});

test("a terminal chat HTTP failure releases the composer and shows a recoverable error", async () => {
  const source = (await import("node:fs/promises").then(({readFile}) =>
    readFile(new URL("../../src/paperlens/web/app.js", import.meta.url), "utf8")
  )).replaceAll("\r\n", "\n");
  const chatFlow = source.match(
    /async function submitQuestion\(value\)[\s\S]*?\n}\n\nfunction setChatPending\(pending\)[\s\S]*?\n}/,
  )?.[0] || "";
  assert.notEqual(chatFlow, "");

  const session = {id: "chat-1", title: "", turns: [], loaded: true};
  const appState = {
    promptImage: null,
    selectedDocumentIds: new Set(),
    activeWorkspaceId: "research",
    evidence: [],
  };
  const form = {
    attributes: new Map(),
    classList: {toggle: (_name, enabled) => { form.pending = enabled; }},
    setAttribute: (name, value) => form.attributes.set(name, value),
    pending: false,
  };
  const send = {disabled: false};
  const stop = {hidden: true};
  const question = {value: "Why did the request fail?"};
  const messages = {innerHTML: "", scrollTop: 0, scrollHeight: 0};
  const elements = new Map([
    ["#chat-form", form],
    ["#chat-form .send-button", send],
    ["#question-input", question],
    ["#messages", messages],
  ]);
  const activeRequests = new Map();
  const api = createApiClient({
    activeRequests,
    onActivity: () => { stop.hidden = activeRequests.size === 0; },
    fetchImpl: async () => ({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      headers: {get: () => "text/event-stream"},
      text: () => new Promise(() => {}),
    }),
  });
  const submitQuestion = new Function(
    "state", "currentSession", "$", "api", "renderHistory", "renderTurn", "t",
    "saveSessions", "restoreSession", "loadTraces", "clearPromptImage",
    `${chatFlow}\nreturn submitQuestion;`,
  )(
    appState,
    () => session,
    (selector) => elements.get(selector),
    api,
    () => {},
    (turn) => `<article>${turn.content}</article>`,
    (key) => ({"chat.thinking": "Thinking", "chat.failed": "Request failed"}[key] || key),
    () => {},
    () => { messages.innerHTML = session.turns.map((turn) => turn.content).join("\n"); },
    () => {},
    () => {},
  );

  let timeoutId;
  const result = await Promise.race([
    submitQuestion(question.value).then(() => "settled"),
    new Promise((resolve) => {
      timeoutId = setTimeout(() => resolve("timed-out"), 100);
    }),
  ]);
  clearTimeout(timeoutId);

  assert.equal(result, "settled");
  assert.equal(form.attributes.get("aria-busy"), "false");
  assert.equal(form.pending, false);
  assert.equal(send.disabled, false);
  assert.equal(activeRequests.size, 0);
  assert.equal(stop.hidden, true);
  assert.match(messages.innerHTML, /Request failed.*502 Bad Gateway/);
});

class FakeContainer {
  constructor(rows) {
    this.children = rows;
    this.scrollTop = 0;
    rows.forEach((row) => { row.parentNode = this; });
  }

  insertBefore(row, reference) {
    const current = this.children.indexOf(row);
    if (current >= 0) this.children.splice(current, 1);
    const index = reference ? this.children.indexOf(reference) : this.children.length;
    this.children.splice(index < 0 ? this.children.length : index, 0, row);
    row.parentNode = this;
  }
}

test("keyed Studio updates preserve scroll, focus selection, and open details", () => {
  const first = new FakeRow("a");
  const focused = new FakeRow("b");
  focused.details.open = true;
  focused.input.value = "selected text";
  focused.input.selectionStart = 2;
  focused.input.selectionEnd = 8;
  const container = new FakeContainer([first, focused]);
  container.scrollTop = 240;

  patchKeyedStudioRows(
    container,
    [{artifact_id: "a", title: "A2"}, {artifact_id: "b", title: "B2"}],
    (item) => new FakeRow(item.artifact_id),
    (row, item) => { row.title = item.title; },
  );

  assert.equal(container.children[1], focused);
  assert.equal(container.scrollTop, 240);
  assert.equal(focused.details.open, true);
  assert.deepEqual(
    [focused.input.selectionStart, focused.input.selectionEnd],
    [2, 8],
  );
});

test("a transient Studio request failure keeps the last snapshot while reconnecting", () => {
  const state = {studioArtifacts: [], studioConnection: "connecting"};
  applyStudioSnapshot(state, {items: [{artifact_id: "a", status: "generating"}]});
  markStudioReconnecting(state);

  assert.equal(state.studioArtifacts.length, 1);
  assert.equal(state.studioArtifacts[0].status, "generating");
  assert.equal(state.studioConnection, "reconnecting");
});

test("Studio events update live progress and survive detail snapshots", () => {
  const state = {
    studioArtifacts: [{artifact_id: "a", progress: 82, live_events: []}],
    studioConnection: "connected",
  };

  applyStudioEvent(state, "a", {
    sequence: 7,
    stage: "media_search",
    message: "Scene 3 media ready",
    details: {artifact_progress: 86},
  });
  applyStudioSnapshot(state, {
    items: [{artifact_id: "a", progress: 86, stage: "media_search"}],
  });

  assert.equal(state.studioArtifacts[0].progress, 86);
  assert.equal(state.studioArtifacts[0].live_events.length, 1);
  assert.equal(state.studioArtifacts[0].live_events[0].sequence, 7);
});

test("Studio terminal events update card status without a page refresh", () => {
  const state = {
    studioArtifacts: [{artifact_id: "a", status: "generating", progress: 0}],
  };

  applyStudioEvent(state, "a", {
    sequence: 8,
    stage: "completion",
    status: "completed",
    message: "Video ready",
    details: {artifact_progress: 100, artifact_status: "ready"},
  });

  assert.equal(state.studioArtifacts[0].progress, 100);
  assert.equal(state.studioArtifacts[0].status, "ready");
});

test("opening an artifact hydrates persisted event history", () => {
  const oldEvent = {sequence: 2, message: "Old local event"};
  const persistedEvent = {sequence: 9, message: "Persisted event"};
  const state = {
    studioArtifacts: [{artifact_id: "a", live_events: [oldEvent]}],
  };

  const merged = mergeStudioArtifactDetail(state, {
    artifact_id: "a",
    status: "generating",
    live_events: [persistedEvent],
  });

  assert.deepEqual(merged.live_events, [oldEvent, persistedEvent]);
  assert.deepEqual(state.studioArtifacts[0].live_events, [oldEvent, persistedEvent]);
});

test("a stale detail response cannot erase a newer live Studio event", () => {
  const state = {
    studioArtifacts: [{artifact_id: "a", live_events: [{sequence: 9, message: "Live"}]}],
  };

  const merged = mergeStudioArtifactDetail(state, {
    artifact_id: "a",
    live_events: Array.from({length: 8}, (_, index) => ({
      sequence: index + 1,
      message: `Persisted ${index + 1}`,
    })),
  });

  assert.deepEqual(merged.live_events.map((event) => event.sequence), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
});

test("stale Studio snapshots and events cannot regress terminal state", () => {
  const state = {
    studioArtifacts: [{
      artifact_id: "a", status: "ready", stage: "ready", progress: 100, revision: 9,
      live_events: [{sequence: 9, message: "Ready"}],
    }],
  };

  applyStudioSnapshot(state, {
    items: [{artifact_id: "a", status: "generating", stage: "rendering", progress: 40, revision: 8}],
  });
  mergeStudioArtifactDetail(state, {
    artifact_id: "a", status: "generating", stage: "rendering", progress: 0, revision: 8,
    live_events: [{sequence: 8, message: "Old render"}],
  });
  applyStudioEvent(state, "a", {
    sequence: 10,
    stage: "rendering",
    message: "Late old event",
    details: {artifact_progress: 80, artifact_status: "generating", artifact_revision: 8},
  });

  assert.equal(state.studioArtifacts[0].status, "ready");
  assert.equal(state.studioArtifacts[0].stage, "ready");
  assert.equal(state.studioArtifacts[0].progress, 100);
  assert.equal(state.studioArtifacts[0].revision, 9);
  assert.deepEqual(state.studioArtifacts[0].live_events.map((event) => event.sequence), [8, 9, 10]);
});

test("a newer Studio revision can intentionally reset a failed artifact for retry", () => {
  const state = {
    studioArtifacts: [{artifact_id: "a", status: "failed", progress: 82, revision: 9}],
  };

  applyStudioSnapshot(state, {
    items: [{artifact_id: "a", status: "queued", stage: "retry_queued", progress: 0, revision: 10}],
  });

  assert.equal(state.studioArtifacts[0].status, "queued");
  assert.equal(state.studioArtifacts[0].progress, 0);
  assert.equal(state.studioArtifacts[0].revision, 10);
});

test("a stale list snapshot cannot remove a locally created active artifact", () => {
  const state = {
    studioArtifacts: [{artifact_id: "new", status: "queued", progress: 0, revision: 1}],
  };

  applyStudioSnapshot(state, {items: []});

  assert.equal(state.studioArtifacts.length, 1);
  assert.equal(state.studioArtifacts[0].artifact_id, "new");
});

test("workspace request guards reject responses from a previous selection", () => {
  const state = {activeWorkspaceId: "b", workspaceRevision: 4, studioMutationRevision: 2};
  assert.equal(isCurrentWorkspaceRequest(state, "b", 4), true);
  assert.equal(isCurrentWorkspaceRequest(state, "a", 4), false);
  assert.equal(isCurrentWorkspaceRequest(state, "b", 3), false);
  assert.equal(isCurrentStudioRequest(state, "b", 4, 2), true);
  assert.equal(isCurrentStudioRequest(state, "b", 4, 1), false);
});

test("a deferred Studio mutation cannot contaminate a newly selected workspace", () => {
  const state = {
    activeWorkspaceId: "b", workspaceRevision: 5, studioMutationRevision: 0,
    studioArtifacts: [{artifact_id: "b-artifact", workspace_id: "b"}],
  };

  const applied = applyStudioMutation(
    state,
    "a",
    4,
    {artifact_id: "a-artifact", workspace_id: "a", status: "queued"},
  );

  assert.equal(applied, null);
  assert.deepEqual(state.studioArtifacts.map((item) => item.artifact_id), ["b-artifact"]);
  assert.equal(state.studioMutationRevision, 0);
});

test("out-of-order Studio mutations cannot regress a newer artifact revision", () => {
  const state = {
    activeWorkspaceId: "a",
    workspaceRevision: 4,
    studioMutationRevision: 0,
    studioArtifacts: [{
      artifact_id: "artifact", status: "queued", stage: "audio_queued",
      progress: 58, revision: 10, live_events: [{sequence: 10}],
    }],
  };

  const applied = applyStudioMutation(state, "a", 4, {
    artifact_id: "artifact", status: "script_ready", stage: "script_ready",
    progress: 55, revision: 9, live_events: [{sequence: 9}],
  });

  assert.equal(applied.status, "queued");
  assert.equal(applied.stage, "audio_queued");
  assert.equal(applied.progress, 58);
  assert.equal(applied.revision, 10);
  assert.deepEqual(applied.live_events.map((event) => event.sequence), [9, 10]);
});

test("a newer mutation revision can reset a failed artifact for retry", () => {
  const state = {
    activeWorkspaceId: "a",
    workspaceRevision: 4,
    studioMutationRevision: 0,
    studioArtifacts: [{
      artifact_id: "artifact", status: "failed", stage: "failed",
      progress: 82, revision: 10,
    }],
  };

  const applied = applyStudioMutation(state, "a", 4, {
    artifact_id: "artifact", status: "queued", stage: "retry_queued",
    progress: 0, revision: 11,
  });

  assert.equal(applied.status, "queued");
  assert.equal(applied.progress, 0);
  assert.equal(applied.revision, 11);
});

test("new Studio artifacts immediately start lifecycle streaming", async () => {
  const source = (await import("node:fs/promises").then(({readFile}) =>
    readFile(new URL("../../src/paperlens/web/app.js", import.meta.url), "utf8")
  )).replaceAll("\r\n", "\n");
  const creationFlow = source.match(/async function generateStudioArtifact\(\)[\s\S]*?\n}\n\nfunction quizMarkup/)?.[0] || "";
  const replacementFlow = source.match(/function replaceStudioArtifact\(item, workspaceId, workspaceRevision\)[\s\S]*?\n}/)?.[0] || "";

  assert.match(creationFlow, /replaceStudioArtifact\(artifact, workspaceId, workspaceRevision\)/);
  assert.match(replacementFlow, /applyStudioMutation\(state, workspaceId, workspaceRevision, item\)/);
  assert.match(replacementFlow, /syncStudioStreams\(\)/);
  assert.match(source, /\/events\?tail=true/);
});

test("the document loads the current live Studio application bundle", async () => {
  const source = await import("node:fs/promises").then(({readFile}) =>
    readFile(new URL("../../src/paperlens/web/index.html", import.meta.url), "utf8")
  );

  assert.match(source, /app\.js\?v=20260808-live-studio-1/);
});

test("Studio history distinguishes terminal and review lifecycle states", () => {
  assert.equal(studioStatusCategory("ready"), "ready");
  assert.equal(studioStatusCategory("failed"), "failed");
  assert.equal(studioStatusCategory("cancelled"), "cancelled");
  assert.equal(studioStatusCategory("interrupted"), "interrupted");
  assert.equal(studioStatusCategory("script_ready"), "review");
  assert.equal(studioStatusCategory("generating"), "preparing");
});

test("switching workspaces closes old Studio streams and dialogs", async () => {
  const source = (await import("node:fs/promises").then(({readFile}) =>
    readFile(new URL("../../src/paperlens/web/app.js", import.meta.url), "utf8")
  )).replaceAll("\r\n", "\n");
  const selectionFlow = source.match(/async function selectWorkspace\([\s\S]*?\n}\n\nasync function createWorkspace/)?.[0] || "";

  assert.match(selectionFlow, /state\.studioStreams\.forEach\(source => source\.close\(\)\)/);
  assert.match(selectionFlow, /state\.studioStreams\.clear\(\)/);
  assert.match(selectionFlow, /state\.activeStudioArtifactId = null/);
  assert.match(selectionFlow, /\$\("#studio-dialog"\)\?\.open/);
  const loadingFlow = source.match(/async function loadStudioArtifacts\(\)[\s\S]*?\n}\n\nasync function loadStudioArtifactDetail/)?.[0] || "";
  assert.match(loadingFlow, /isCurrentStudioRequest\(state, workspaceId, revision, mutationRevision\)/);
});
