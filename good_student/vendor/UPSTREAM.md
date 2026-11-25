# Vendored modules

`coach/` contains unchanged `__init__.py`, `text.py`, `extract.py`, `chapters.py`,
`index.py`, and `questions.py` from:

- Repository: https://github.com/ZeKaiNie/universal-examprep-skill
- Commit: `e01d2f7647b0917172ae8ad5fb788b404e977cae`
- Original directory: `skills/universal-exam-cram-coach/coach`
- License: MIT, copyright 2026 ZeKaiNie, retained in `coach/LICENSE`.

The upstream CLI and workspace pointer are not used. Stable question IDs,
grading tolerance, course isolation, modes and focus provenance are implemented
by this project's adapters. Do not call the upstream numeric grader directly.

The optional local audio and OCR adapters use faster-whisper (MIT) and RapidOCR
(Apache-2.0) through their Python APIs; their source and models are not vendored
into this skill. Runtime dependencies are installed into the selected project.
The supported audio-decoder bound is recorded in pyproject.toml and the skill's
setup helper; av 19 removed an argument used by faster-whisper 1.2.x.
