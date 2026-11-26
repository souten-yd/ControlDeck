# Office structure candidates (Draft)

Stacked on #651. Adds independent, read-only Python standard-library helpers in app.knowledge.office_structure. No ingestion hook or change to existing PDF processing, DB, API, UI, permissions, default, network, or execution capability.

- XLSX: worksheet order and internal relationship, name/state, cell reference/type/style ID, raw value, shared/inline strings, formula text and attributes, merged ranges. Formula cache is distinct from formula; never recompute or treat a missing cache as zero. Cached values may be stale. Dates, formatted values, shared formula reconstruction, chart sheets, charts, external links and logical grid not supported. Do not use as a trusted measurement record without validation.
- PPTX: slide order by presentation relationship, direct shapes and physical table cells with merge attributes. Unsupported top-level shape kinds reported. Groups, images, charts, notes, styles and visual reading order not resolved.
- DOCX: OMML oMath subtree serialized (not byte-exact) and math text tokens, body index and equation index. No LaTeX conversion, evaluation or revision/visibility resolution.
- Limits inherited from #651 plus 400 slides/sheets, 50k cells, duplicate reference/target rejection. No ZIP extraction, macros, formula execution, DTD/entities or external relationship traversal. Not a complete security audit. The parser does not establish source authorization or evidence correctness; do not activate automatically.

## Executed validation

Python isolated pytest: 10 passed in 0.08s, including six new test instances (formula/cache, three rejected targets, PPTX text/table, OMML fraction) and four prior #651 tests. Tested files match Git blobs office_structure.py ed0b5dbdd95621ec22fc44783704d61552882920, test_office_structure.py ffd407dbc2841ec61105c1c0dedddfb3e722125f, base structured_documents.py 71bfbcd7fbf73ec81ea8aa98d7e2f0a4cb7ab518. Also manually exercised generated openpyxl/PPTX files (synthetic, not external gold). Public python-calamine [workbook_properties.xlsx](https://github.com/dimastbk/python-calamine/blob/main/tests/data/workbook_properties.xlsx) fixture transferred from GitHub as base64, decoded and verified Git blob 5586399023daeb4804fe3de6068bf51bc1b91330; source SHA256 2b32d46955fec20c7e515d3f339de94951f39bc0925c6da8049e0dceb2af2df2. Executed final Python module: one sheet Sheet1, one cell A1, numeric raw value 1, no formulas or merges. This is a public library test fixture, not a real technical workbook or evidence of formula coverage on external data.

Actual local TeX distribution Word sample fonts/lm-math/test-word-latinmodern_math.docx, SHA256 489ed42213c1a0e060d3ef6da1f9d84ea4e8ba2b6cbbd5032440d1b8655ba6d7: 11 OMML equations, 156 math text elements; prior direct-run text extraction returned zero non-whitespace paragraphs. Counts are not semantic accuracy.

Existing environment presentation templates, not newly collected independent public documents. No template contents copied to repo.

|File|SHA256|Slides|Supported shape/table candidates|Unsupported top-level elements|Tables|
|---|---|---:|---:|---:|---:|
|Academic_01.pptx|6e3ed1dbef0191cd27cebea73db5d65399788e1469c02cb871df110362d1beda|15|170|105|0|
|Academic_02.pptx|351955549fdc04ad61e0199841e183c1185311babc1fdb88f0ebceb1a6eedee1|10|54|39|0|
|Academic_03.pptx|b5649598104d339981a62eb26e7f9a1feb88b83613329d85e9c6918be128f83f|9|73|13|1|
|Academic_04.pptx|0420822c3ee295086d735b070a52d990e143e12b744b4392a624609d6303a2f2|10|67|24|0|
|Brand_Design_01.pptx|9ed16d444705d01441cef6834d7c8da0a1e4634461a9477b17254699a9073baf|12|146|35|0|

56 slides, 510 candidates, 216 unsupported elements; 70.25% of counted top-level elements supported, not extraction precision/recall. No visual ground truth. Full clone failed DNS, full backend pytest, API, permissions, model, performance and deployment not tested. Next: independent XLSX and technical PPTX corpus, group/chart extraction, formula semantics, PDF table cell reconstruction, evidence validation, then integration with existing document ingestion and block dependency planning. Do not merge.
