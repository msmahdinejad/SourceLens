# Charge While You Shop — bilingual product demo

[فارسی](README.fa.md) · [English evaluation PDF](outputs/SourceLens-demo-evaluation-en.pdf) ·
[English evaluation PowerPoint](outputs/SourceLens-demo-evaluation-en.pptx) ·
[Persian evaluation PDF](outputs/SourceLens-demo-evaluation-fa.pdf)

This is a captured end-to-end SourceLens run. The application processed **7 sources** into
**236 chunks**, then generated all **13 Studio formats** independently in English and Persian:
**26 outputs** in total. Output content was not rewritten after export. The evaluation reports were
assembled from the captured interface and exported artifacts.

![Seven ready sources in the demo workspace](assets/02-sources-ready-en.png)

## The question

Can a supermarket or shopping-centre car park become a convenient, reliable, interoperable, and
grid-aware EV charging stop? The subject connects a familiar driver journey—arrive, plug in, shop,
check status, leave—with consumer preferences, public charging standards, retail strategy, and
power-grid constraints.

The workspace contains two CC BY 4.0 studies and five curated web sources added through SourceLens
Search. Corporate statements remain labeled as company claims rather than measured outcomes.
Provenance and reuse notes are documented in [source-research.md](source-research.md).

![Search results reviewed before import](assets/04-web-search-en.png)

## English outputs

The [machine-readable index](outputs/en/index.json) records the title, type, size, and SHA-256 hash
of every English artifact.

| # | Studio format | Download |
| ---: | --- | --- |
| 1 | Audio overview | [MP3](outputs/en/charge-while-you-shop-audio-overview.mp3) · [transcript](outputs/en/charge-while-you-shop-audio-overview-transcript.txt) |
| 2 | Report | [Markdown](outputs/en/charge-while-you-shop-report.md) |
| 3 | Concept map | [JSON](outputs/en/charge-while-you-shop-concept-map.json) |
| 4 | Flashcards | [JSON](outputs/en/charge-while-you-shop-flashcards.json) |
| 5 | Quiz | [JSON](outputs/en/charge-while-you-shop-quiz.json) |
| 6 | Infographic | [JSON](outputs/en/charge-while-you-shop-infographic.json) |
| 7 | Data table | [JSON](outputs/en/charge-while-you-shop-data-table.json) |
| 8 | Chart | [JSON](outputs/en/charge-while-you-shop-chart.json) |
| 9 | Timeline | [JSON](outputs/en/charge-while-you-shop-timeline.json) |
| 10 | Source comparison | [Markdown](outputs/en/charge-while-you-shop-comparison.md) |
| 11 | Research-gap analysis | [Markdown](outputs/en/charge-while-you-shop-research-gap.md) |
| 12 | Editable slide deck | **[Download PowerPoint](https://github.com/msmahdinejad/SourceLens/raw/main/docs/demo/outputs/en/charge-while-you-shop-slide-deck.pptx)** · [manifest](outputs/en/charge-while-you-shop-slide-deck-manifest.json) |
| 13 | 60-second video overview | **[Download MP4](https://github.com/msmahdinejad/SourceLens/raw/main/docs/demo/outputs/en/charge-while-you-shop-video-overview.mp4)** · [captions](outputs/en/charge-while-you-shop-video-overview-captions.srt) · [timeline](outputs/en/charge-while-you-shop-video-overview-timeline.json) · [manifest](outputs/en/charge-while-you-shop-video-overview-manifest.json) · [music credits](outputs/en/audio-credits.txt) |

The complete Persian set is listed in the [Persian demo page](README.fa.md) and indexed in
[outputs/fa/index.json](outputs/fa/index.json).

## What the run exercised

### Grounded workspace and search

![A PDF moving through ingestion](assets/01-source-ingestion-en.png)

The run covered PDF extraction, chunking, indexing, review-before-import web search, selected-source
scope, citations, and evidence inspection.

### Structured research and study tools

![A source-grounded concept map](assets/06-concept-map-en.png)

![Source-grounded flashcards](assets/08-flashcards-en.png)

![A structured evidence report](assets/10-report-en.png)

Reports, maps, cards, quizzes, tables, charts, timelines, comparisons, and research gaps were
created from the same seven-source boundary.

### PowerPoint, audio, and video

![Completed editable slide deck in Studio](assets/13-slide-deck-studio-en.png)

![Completed video overview in Studio](assets/15-video-studio-en.png)

The English and Persian decks are editable. Both videos are 60-second 1920×1080 H.264/AAC files
with captions, distinct verified media, natural-speed narration, safe text margins, timelines, and
provenance manifests. The audio overviews include their generated transcripts.
The Jamendo tracks are CC BY-NC 3.0 and the demo videos are for non-commercial use; exact track,
artist, source, and license details are retained in the linked credit files.

### Persian workflow

The Persian run used the same source boundary. Its RTL conversation and Studio workflows retained
source selection, citations, evidence inspection, live progress, and downloadable artifacts.

## Evaluation reports

The concise release report is available in both languages as PDF and editable PowerPoint:

- [English PDF](outputs/SourceLens-demo-evaluation-en.pdf) ·
  [English PowerPoint](outputs/SourceLens-demo-evaluation-en.pptx)
- [Persian PDF](outputs/SourceLens-demo-evaluation-fa.pdf) ·
  [Persian PowerPoint](outputs/SourceLens-demo-evaluation-fa.pptx)

The reports show representative captures rather than embedding the complete deck or video. Direct
links inside each report point to the full application-generated files. The Persian visual outputs
and report use Kalameh typography.

## Result

All 26 Studio artifacts reached a downloadable ready state through the application workflows. The
release validation checks both language indexes, file hashes, output formats, slide structure,
video duration/codecs, font selection, and public-manifest privacy.
