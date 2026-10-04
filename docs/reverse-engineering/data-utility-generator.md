# Data Utility Generator — Reverse-Engineering Dossier

Status: research complete enough to start an isolated implementation slice; runtime parity is **not** claimed.

Source issue: #3
Primary Reddit source: https://www.reddit.com/r/saasbuild/s/0OehcW304Z
Resolved live product: https://data-utility-generator-coral.vercel.app/
Research date: 2026-10-04

## Evidence model

Use these labels throughout:

- **OBSERVED** — directly available from the source surface or public artifact.
- **DOCUMENTED** — author/vendor explicitly states the behavior.
- **CORROBORATED** — repeated across independent or same-author launch surfaces.
- **INFERRED** — reasonable architectural/product inference, not source fact.
- **UNKNOWN** — not yet verified.

## Source truth

The primary r/saasbuild post asks for UI feedback and links to the live Data Utility Generator. At research time its Reddit JSON reported zero comments. No comments are attributed to that exact post.

Same-author launch/crossposts describe these capabilities:

- test-data generation to CSV, XLSX and Parquet
- JSON / CSV / Parquet conversion
- nested JSON generation
- SQL CREATE TABLE and INSERT generation
- SQL oriented around PostgreSQL, MySQL, SQL Server, SQLite and Oracle
- data profiling
- data masking
- synthetic data generation
- screenshot/table image to structured data
- data formatting
- data comparison
- no-login browser utilities
- no server-side file storage claimed for browser utilities

Treat these as DOCUMENTED unless independently runtime-tested.

## Feedback evidence

Useful feedback from the author's other public launch threads:

1. No-login is valued because account creation adds friction for quick data tasks.
2. Batch conversion of multiple files/folders was requested.
3. JSON-to-CSV trust depends on explicit behavior for nested arrays, missing keys and mixed types.
4. Masking needs a before/after preview and a warning not to paste production secrets.
5. Users asked whether the screenshot feature truly shares the browser-only privacy boundary.
6. Screenshot-to-Data repeatedly attracts attention and may be the best acquisition hook.
7. The author states screenshot extraction currently works better on clean tables than messy inputs/handwriting.
8. The feature list may be too broad; the author identifies large synthetic test-data generation as the original repeated job and data engineers as the primary user.
9. Edge-case correctness matters more than feature count.

## Comparator map

### Mockaroo

Benchmark for mature schema-driven test generation, formulas, many data types and mock APIs. It is broader and more established on generation, but account/API workflows create more friction than the donor's instant utility model.

### generatedata.com

Benchmark for extensible random-data generation and multiple export formats.

### Gretel

Benchmark for enterprise synthetic-data quality, privacy evaluation and deployment models. SyntheticForge should not copy enterprise marketing claims without equivalent evidence.

### dataeng.tools / ParquetKit

Strong architecture benchmark for browser-only Parquet/CSV/JSON workflows using DuckDB-WASM/Web Workers. Relevant to conversion, profiling, schema diff and large-file responsiveness.

### CSV Tools Online / CodeToolsLab

Benchmark the no-signup, local-processing utility model and fast discovery of single-purpose tools.

## Canonical portfolio decision

Do **not** create a second synthetic-data product.

SyntheticForge AI is the canonical destination because it already owns the deeper system-aware product model: relational generation, schema/DDL/API inputs, profiling, validation, sensitive-field handling, versioning/comparison and multi-format exports.

This donor contributes a missing product layer: **instant utility workflows for the 30-second job**.

## Product thesis

Add a first-class **Utility Mode** to SyntheticForge.

User promise:

> I have a file, sample, table, image or schema and need a safe, correct, usable output now.

Utility Mode should have five lanes:

1. **Convert** — CSV / JSON / JSONL / Parquet / XLSX; batch conversion; explicit flattening policy.
2. **Inspect** — row preview, schema, null/distinct/type profile, schema diff, PII hints.
3. **Generate** — quick deterministic fake data using the existing SyntheticForge core.
4. **Protect** — masking/tokenization/synthetic replacement with before/after preview.
5. **Extract** — screenshot/table image to structured data with confidence and correction UI.

## Architecture

Keep the existing FastAPI/server control plane for deep SyntheticForge workflows.

For Utility Mode conversion/profiling, introduce an isolated client-side execution path:

```text
Browser UI
   |
   v
Web Worker
   |
   +--> DuckDB-WASM / local parsers
   |       +--> CSV
   |       +--> JSON / JSONL
   |       +--> Parquet
   |
   +--> preview/profile/schema
   +--> local export
```

Raw file bytes must not be sent to the SyntheticForge server for paths labeled browser-local.

Deep generation can reuse the existing server-side deterministic engine, but the UI must clearly distinguish local-only utilities from server-processed operations.

Screenshot extraction remains UNKNOWN until an implementation proves its privacy boundary. Do not claim local-only extraction if an image leaves the device.

## Phase A — Convert + Inspect

This is the first implementation slice.

### Required behavior

- drag/drop or file picker
- CSV, JSON, JSONL and Parquet input
- multiple-file batch queue
- preview first rows
- inferred schema
- null / distinct / type profile
- conversion to CSV, JSON/JSONL and Parquet
- explicit nested JSON strategy
- visible handling of missing keys
- visible handling of mixed types
- deterministic column ordering
- error diagnostics rather than silent coercion
- worker execution so processing does not freeze the UI
- no login
- no raw-input persistence

### Acceptance tests

Fixtures must cover:

- nested objects
- nested arrays
- empty arrays
- heterogeneous arrays
- missing keys
- mixed numeric/string columns
- null-only columns
- duplicate column names where source format allows them
- quoted CSV delimiters/newlines
- malformed JSON
- malformed CSV rows
- empty files
- large enough files to exercise worker behavior

### Privacy test

Browser automation must fail the build if raw fixture bytes appear in outbound requests during local Convert + Inspect workflows.

## Phase B — Quick Generate

Reuse existing SyntheticForge domain services.

Flow:

```text
schema/sample -> fields -> row count + seed -> generate -> validate -> preview -> export
```

Exports: CSV, JSON/JSONL, Parquet and dialect-aware SQL.

Provide a deterministic replay receipt containing seed, schema hash, row count, format and generator version.

## Phase C — Protect + Compare

- sensitive-field classification
- masking/synthetic replacement preview
- explicit transform list
- schema diff
- row/key comparison where bounded and safe
- production-secret warning
- no accidental echo of sensitive source values into logs

## Phase D — Screenshot to Structured Data

- image import and region selection
- table/schema extraction
- per-cell or per-column confidence
- correction UI
- clean-table fixture suite
- messy-table fixture suite tracked separately
- handwriting remains experimental until a defined quality threshold is reached

## Verification gates

Follow the project roadmap:

Source -> Understanding -> Evidence -> Reconstruction Model -> Specification -> Slices -> Implementation -> Runtime Verification -> Product UAT -> Tracker Truth

A capability is not complete because code exists.

Required evidence for Phase A:

1. isolated branch
2. unit tests for conversion semantics
3. adversarial fixture suite
4. browser privacy/network guard
5. large-file responsiveness evidence
6. keyboard/accessibility checks
7. exact-head CI
8. local runtime/browser UAT
9. only then update completion/tracker state

## Current gaps

- Live product behavior is not independently browser-certified in this dossier because the public page did not expose a usable text DOM through the current research surface.
- Screenshot privacy is author-claimed but not independently verified.
- No external repeat-usage metrics establish the donor's true product wedge.
- SyntheticForge does not yet provide the donor-style instant browser-only utility lane.
- Batch conversion, local Parquet conversion and screenshot extraction are not claimed implemented in SyntheticForge by this dossier.

## Next action

Implement Phase A on `reverse/data-utility-generator` without changing existing deep generation behavior. Keep the new route isolated until privacy, conversion-semantics and browser tests are green.
