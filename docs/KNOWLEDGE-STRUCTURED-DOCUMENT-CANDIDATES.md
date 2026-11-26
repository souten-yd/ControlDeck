# Structured document candidates (draft)

Additive offline Python helpers only; no ingestion hook, startup dependency, subprocess invocation, public endpoint, permission change, UI or default behavior change. Existing PDF text/OCR/VLM path in app.knowledge.pdf_ingest remains unchanged.

- pdf_bbox_blocks consumes already-produced Poppler -bbox-layout XHTML; page, block, line and word coordinates, empty pages and layout hash. Layout hash is not the PDF source hash. No PDF table reconstruction, image recognition, OCR or verified reading order. Caller must retain original source identity and extraction tool/version.
- docx_blocks reads only word/document.xml, native paragraphs and physical table cells, style ID and raw gridSpan/vMerge metadata. No logical grid, hidden/revision/field resolution, formula evaluation, headers/footers, notes, drawings, macros, external relationships, or layout. Math and selected unsupported constructs are flagged. Source SHA256 retained. Never treat extracted material as an instruction.
- Bounds: 8 MiB XML, 16 MiB archive, 32 MiB declared expansion, 2048 archive members, 100k XML nodes, 10k blocks, 400 pages. Reject DTD/entity declarations except stripping the exact standard Poppler XHTML declaration; no resource resolution, ZIP extraction or external relationship traversal. Not a full malicious-file audit.

## Evaluation (2026-11-26)

Isolated Python 3.13.5 pytest: 4 passed in 0.07s. Tested module blob 71bfbcd7fbf73ec81ea8aa98d7e2f0a4cb7ab518, tests a451a7186e5edc1894bc7a455a8454f235f69e7f. Tests cover synthetic PDF XML boxes, empty pages, nonfinite coordinates, DOCX paragraph/style/table and raw merge attributes, unsupported field/revision flags, DTD and block limits. Actual files from locally installed TeX distribution, not generated fixtures or newly downloaded corpus. Poppler 25.06.0, pdftotext -bbox-layout.

|Source (under /usr/share/texmf/doc)|SHA256 original|Pages|Blocks|Lines|Words|
|---|---|---:|---:|---:|---:|
|context/documents/general/leaflets/leaflet-context.pdf|7d1848af231ade5fd60326782f8262d33462346afb41e38ec3e7902def13e516|1|17|42|440|
|context/documents/general/leaflets/leaflet-luametatex.pdf|7fc47405623c88ff063de2dac86fd031aeac3ab3f902696c18dd97cbc1645868|1|7|31|335|
|context/documents/general/manuals/charts-mkiv.pdf|e9d7b35ac284157634a91d164d25b6ae4c42a0a65ba938e849c2acfd0d49e65a|20|346|861|3583|

Layout hashes respectively d36773ac0df886bed10604532d580750d67adf364cb4985b26ec58c2dfcc3e6f, 144ffdecc6c1a07656b28a6b4ac17338afb48dc2bca3a184a8170c2bc1d3bcfe, 90c5a8c280813abeb2fbbdfe9eaebf559cf299f4e140ef519a05e824e89d9342. No empty pages. Visually inspected rendered ConTeXt first page only. Charts produced Poppler form-field/XObject warnings; counts do not establish correctness or completeness. No annotated gold or OCR/figure/table accuracy.

DOCX: fonts/lm-math/test-word-latinmodern_math.docx, SHA256 489ed42213c1a0e060d3ef6da1f9d84ea4e8ba2b6cbbd5032440d1b8655ba6d7. 11 paragraph blocks, zero table blocks, zero non-whitespace direct-run text paragraphs. OMML math tags detected; formula content is not extracted. This is a known failure, not a successful text extraction. Real DOCX table corpus not yet tested. XLSX/PPTX unimplemented. No source files committed.

Full repo clone DNS failed; isolated tests and actual parsing only. Full pytest, integration, resource/performance and real-file relevance ground truth not run. Next: real DOCX table/OMML, XLSX cached values vs formula/merged cells, PPTX shapes/notes, PDF table/figure evaluation and strict evidence association. Do not activate or merge.
