# Persian output fonts

Public packages ship Vazirmatn so slide and video renderers have one deterministic Persian
font. User-installed families stay outside Git and release builds.

- **Vazirmatn**: nine TTF weights. `Vazirmatn/OFL.txt` contains the upstream
  SIL Open Font License.

Packaged fonts are read-only in Font Manager. User-installed TTF/OTF files remain
independently manageable in ignored `data/fonts/`. See `THIRD_PARTY_LICENSES.md`.
