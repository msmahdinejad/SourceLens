# Third-party licenses and notices

SourceLens-authored code is licensed under MIT. This file records direct runtime dependencies,
redistributed assets, and release boundaries; it does not replace each upstream license.
Transitive packages retain the license metadata installed with them and remain pinned in
`uv.lock` or the renderer `package-lock.json` files.

## PDF stack

SourceLens does not import, lock, or distribute PyMuPDF/MuPDF. Embedded PDF text and geometry use
pdfplumber/pdfminer.six under MIT. Page rasterization uses pypdfium2 under Apache-2.0 or
BSD-3-Clause and PDFium under its BSD-style license. pypdfium2 wheels include build-specific
PDFium dependency notices; binary redistributors must retain those notices.

- pdfplumber: <https://github.com/jsvine/pdfplumber>
- pdfminer.six: <https://github.com/pdfminer/pdfminer.six>
- pypdfium2 licensing: <https://pypdfium2.readthedocs.io/en/stable/readme.html#licensing>

## Direct Python runtime dependencies

| Dependency | License |
| --- | --- |
| ChromaDB | Apache-2.0 |
| FastAPI | MIT |
| FontTools | MIT |
| Google Gen AI SDK | Apache-2.0 |
| HTTPX | BSD-3-Clause |
| latex2mathml | MIT |
| markdown-it-py | MIT |
| Matplotlib | PSF-based Matplotlib License |
| NumPy | BSD-3-Clause and bundled component licenses |
| OpenAI Python SDK | Apache-2.0 |
| pdfplumber / pdfminer.six | MIT |
| Pillow | MIT-CMU |
| Pydantic / pydantic-settings | MIT |
| pypdfium2 | Apache-2.0 or BSD-3-Clause; bundled PDFium notices |
| Rich | MIT |
| Sentence Transformers | Apache-2.0 |
| Typer | MIT |
| Uvicorn | BSD-3-Clause |
| python-multipart | Apache-2.0 |

Optional `ocr`, `voice`, and `dev` dependencies retain their upstream licenses. Run the release
license inventory against the fully resolved environment before publishing a binary bundle.

On Linux, PyTorch 2.13 resolves `cuda-toolkit` 13.0.3.0 plus selected CUDA components. NVIDIA's
2,512-byte `cuda-toolkit` wheel is a payload-free meta-package and publishes no license metadata;
the release audit therefore recognizes only that exact locked version and SHA-256
`d693caaa261214ddd7dbb60d68e71cbed884e68c2be7509778f3051da0b91c3f` as subject to the
[NVIDIA CUDA Toolkit EULA](https://docs.nvidia.com/cuda/archive/13.0.1/eula/index.html). The audit
continues to reject every other package or version with missing license metadata. CUDA components
are fetched by the package installer on supported Linux systems and are not bundled in the
SourceLens source archive.

## Node renderer dependencies

| Dependency | License | Use |
| --- | --- | --- |
| PptxGenJS | MIT | Editable PowerPoint generation |
| JSZip | MIT or GPL-3.0-or-later; SourceLens uses the MIT option | PptxGenJS dependency |
| pako | MIT and Zlib | PptxGenJS compression dependency |
| `@resvg/resvg-js` 2.6.2 | MPL-2.0 | SVG overlay rasterization |
| local `image-size` compatibility stub | MIT | Fail-closed unused PptxGenJS hook |

## Publicly redistributed font

- **Vazirmatn** is distributed under SIL Open Font License 1.1.
- Its full license is retained at `src/paperlens/assets/fonts/Vazirmatn/OFL.txt`.

## User-installed fonts

Users may install TTF or OTF families they are licensed to use through Settings. Those files remain
under ignored `data/fonts/` and are never copied into public builds. SourceLens does not grant
redistribution rights for user-provided assets.

## Demo papers and generated media

SourceLens does not redistribute demo PDFs. An explicit demo request downloads two EV-charging
publications from MDPI and the European Commission Joint Research Centre and verifies pinned
SHA-256 values. Both are CC BY 4.0; the manifest retains title, authors, year, venue, source URL,
and checksum. License details are recorded in `docs/demo/source-research.md`. Article and
third-party figure credits
remain the user's responsibility when republishing source material.

Pexels and Jamendo assets retain their provider and item-specific licenses. SourceLens stores
provenance and skips media without acceptable license metadata; generated artifacts do not transfer
ownership of source media.

The published demo narration was generated with locally configured Piper voices. SourceLens does
not bundle the Piper executable, voice models, or training datasets; users must obtain them under
their respective licenses. Published artifacts contain no API key, local model path, or credential.

## Retrieval benchmark data

The public retrieval fixture derives selected structured facts from 13 pinned Wikidata
main-namespace entity revisions under CC0-1.0. Revision URLs and source-response SHA-256 values
are retained in `fixtures/retrieval_wikidata/provenance.json`. No Wikipedia narrative or media is
copied into the fixture.
