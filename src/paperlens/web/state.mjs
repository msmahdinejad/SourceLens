export const SESSION_KEY = "paperlens.sessions.v2";
export const WORKSPACE_KEY = "paperlens.workspace.v1";
export const LOCALE_KEY = "paperlens.locale.v1";

export const VIEW_TITLES = {
  chat: ["Research workspace", "Source chat"],
  workspaces: ["Workspace library", "Workspaces"],
  history: ["Research library", "History and outputs"],
  documents: ["Sources", "Sources and processing"],
  vector: ["Knowledge base", "Vector knowledge base"],
  traces: ["Observability", "Request flow"],
  providers: ["Provider connections", "AI and OCR providers"],
  settings: ["Runtime architecture", "Architecture and settings"],
};
export const VIEW_NAMES = new Set(Object.keys(VIEW_TITLES));

export function createAppState({requestedLocale = null, storage = globalThis.localStorage} = {}) {
  const savedLocale = storage?.getItem(LOCALE_KEY);
  return {
    locale: requestedLocale === "en" || requestedLocale === "fa"
      ? requestedLocale
      : (savedLocale === "en" ? "en" : "fa"),
    workspaces: [],
    activeWorkspaceId: storage?.getItem(WORKSPACE_KEY) || "default",
    documents: [], selectedDocumentIds: new Set(),
    sessions: [], activeSessionId: null,
    evidence: [], traces: [], activeTraceId: null,
    processTimer: null, documentsLoading: false, workspaceRevision: 0, rebuildTimer: null,
    recorder: null, recordingStream: null, audioChunks: [], promptImage: null,
    studioArtifacts: [], studioCatalog: [], studioTimer: null,
    studioStreams: new Map(), studioConnection: "connecting", studioNextCursor: null,
    activeStudioArtifactId: null, studioMutationRevision: 0,
    providers: [], modelRoutes: [], modelOrder: [], providerRuntime: {}, editingProvider: null,
    integrations: [], integrationRuntime: {}, webSearchSession: null, fonts: [],
    activeRequests: new Map(),
  };
}
