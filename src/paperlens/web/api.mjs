function isFormData(value) {
  return typeof FormData !== "undefined" && value instanceof FormData;
}

export function createApiClient({
  activeRequests,
  fetchImpl = globalThis.fetch,
  onActivity = () => {},
  cancelledMessage = () => "The request was cancelled.",
}) {
  let sequence = 0;
  return async function api(path, options = {}) {
    const {trackRequest, requestKey, ...requestOptions} = options;
    const request = {...requestOptions};
    const method = String(request.method || "GET").toUpperCase();
    const shouldTrack = trackRequest ?? method !== "GET";
    const key = requestKey || `${method}:${path}:${++sequence}`;
    let controller = null;
    if (shouldTrack && !request.signal) {
      controller = new AbortController();
      request.signal = controller.signal;
      activeRequests.set(key, controller);
      onActivity();
    }
    if (request.body && !isFormData(request.body) && typeof request.body !== "string") {
      request.body = JSON.stringify(request.body);
    }
    request.headers = {...request.headers};
    if (request.body && !isFormData(request.body)) request.headers["Content-Type"] = "application/json";
    try {
      const response = await fetchImpl(path, request);
      const contentType = response.headers.get("content-type") || "";
      if (!response.ok && !contentType.includes("application/json")) {
        const status = [response.status, response.statusText].filter(Boolean).join(" ");
        throw new Error(status || "Request failed");
      }
      const payload = contentType.includes("application/json")
        ? await response.json()
        : await response.text();
      if (!response.ok) {
        const detail = payload?.detail;
        const status = [response.status, response.statusText].filter(Boolean).join(" ");
        throw new Error(
          typeof detail === "string" ? detail : JSON.stringify(detail || payload) || status,
        );
      }
      return payload;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error(cancelledMessage());
      throw error;
    } finally {
      if (controller) {
        activeRequests.delete(key);
        onActivity();
      }
    }
  };
}
