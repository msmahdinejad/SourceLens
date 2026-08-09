# Security policy

## Reporting a vulnerability

Do not publish a suspected vulnerability in a public issue. Use the repository's private security-reporting channel or contact the maintainer privately. Include the affected version, reproduction steps, impact, and any suggested mitigation.

## Credentials and private data

- Store credentials only in `.env` during local development or in a deployment secret manager.
- `.env.example` contains variable names and non-secret placeholders only.
- Application status endpoints report counts and labels, never credential values.
- Error and trace paths must redact configured secrets.
- Podcast artifacts never store Gemini credentials or Live API session-resumption handles; only secret-safe credential slot numbers are observable.
- `data/workspaces/` can contain private originals, OCR/VLM text, page images, chats, and generated artifacts.
- `data/chroma/`, `data/cache/`, `data/traces/`, and `outputs/` can contain derived private text.
- Uploaded filenames are not trusted as filesystem paths; storage uses generated workspace and document IDs.

If a key is committed accidentally, deleting the file is insufficient. Revoke or rotate the key immediately and remove it from repository history according to the hosting provider's guidance.

## Deployment boundary

The default server is intended for trusted local use. Before exposing it to other users or the public internet, add and verify:

- authentication and workspace authorization;
- tenant-isolated storage and collections;
- HTTPS and a deployment secret manager;
- reverse-proxy request, upload, and rate limits;
- malware scanning for uploaded files;
- a durable production task queue and worker supervision;
- backups, retention rules, and deletion workflows;
- provider data-processing agreements appropriate for the uploaded content.

OCR.Space and configured model providers receive rendered pages or prompt images only when the extraction policy determines they are needed. Operators remain responsible for provider privacy terms and should use local fallbacks for sensitive sources.

## ChromaDB advisory boundary

The dependency audit reports `PYSEC-2026-311` / `CVE-2026-45829` against ChromaDB 1.5.9. The
published exploit targets ChromaDB's unauthenticated server endpoint for creating a collection
with an attacker-controlled remote model. No patched ChromaDB release is listed by the advisory
for the pinned runtime.

SourceLens uses an embedded `PersistentClient`; it does not start `chroma run`, mount Chroma's `/api/v2` application, accept remote embedding-model configuration, or expose its index directory as an upload target. Keep that boundary intact. Do not expose a separate Chroma server, and do not place untrusted index files under `data/chroma/`. CI ignores only `PYSEC-2026-311` after asserting this boundary; every other Python advisory still fails the build. Upgrade as soon as the upstream project publishes a fixed release.

## Supported versions

Security fixes target the latest published 2.x release and the default branch.
