# Visual output architecture

SourceLens creates editable PowerPoint decks and rendered video overviews through a stable
`paperlens.visual-engine.v1` contract. Python owns source scope, evidence planning, licensed-media
resolution, job state, and output validation. The Node renderers own presentation layout and video
composition.

## Shared rendering boundary

- Adapter: `paperlens.visual_engine_bridge.VisualEngineBridge`
- Slide entry point: `renderers/deck_engine/entrypoint.mjs`
- Video entry point: `renderers/video_engine/entrypoint.mjs`
- Input: a normalized semantic plan plus resolved media and fonts
- Output: the primary artifact, previews, diagnostics, warnings, credits, progress events, and a
  render manifest

Each render starts in an isolated staging directory. The bridge accepts only validated relative
paths and publishes the result after the renderer reports a valid primary output. Provider
credentials stay in the application process and never enter the render contract.

## PowerPoint

The deck engine supports 17 RTL-aware layouts, including covers, editorial splits, process flows,
charts, matrices, comparisons, dashboards, timelines, photo mosaics, case studies, tables, and
closing actions. Text, shapes, icons, metrics, tables, and charts remain editable in PowerPoint.
Images use focal-point-aware cropping, mixed Persian and Latin text receives bidi isolation, and
source references are stored in speaker notes.

## Video

The video engine supports 13 scene layouts, semantic media queries, motion profiles, transitions,
captions, and optional narration-safe music. It renders exact scene windows and composes them with
FFmpeg. A safe concatenation path remains available when transition composition is unsupported.
The output bundle can include MP4, SRT, timeline JSON, media credits, warnings, and diagnostics.

Narration is kept at its natural speed. Scene timing and pauses adapt to the audio; the renderer
does not time-compress speech to force a requested duration.

## Fonts and prerequisites

Vazirmatn ships under SIL OFL 1.1 and provides deterministic Persian rendering. User-installed
TTF and OTF families remain outside release archives and are resolved by exact file path.

The slide renderer requires Node.js 20+ and its locked dependencies. Video rendering additionally
requires FFmpeg.

```powershell
npm ci --prefix src/paperlens/renderers/deck_engine
npm ci --prefix src/paperlens/renderers/video_engine
```

Automated tests cover the rendering contract, path validation, layout families, RTL text, output
duration, captions, licensed-media metadata, cancellation, and artifact lifecycle.
