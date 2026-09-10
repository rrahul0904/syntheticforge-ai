# Development and release workflow

SyntheticForge is designed to be runnable locally without Vercel or another hosted frontend.

## One-time setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make install-dev
```

Windows users can activate `.venv\\Scripts\\activate` and use the equivalent Python commands from the Makefile.

## Run the application

```bash
make run
```

Open `http://127.0.0.1:8000` for the UI and `http://127.0.0.1:8000/docs` for OpenAPI documentation.

## Required verification before merging

```bash
make verify
```

This runs the automated test suite, Python compilation, browser JavaScript syntax validation, the real SQLite source-to-target roundtrip, and a CLI smoke test.

## End-to-end demonstration

```bash
make demo
```

The hospitality demo generates a multi-table relational dataset, validates it, and writes the packaged result locally.

## Performance benchmark

```bash
make benchmark
```

The benchmark exercises the million-row streaming path with bounded batches.

## Credential-gated certification

External connector and AI-provider smoke tests intentionally read credentials from environment variables and never from committed files:

```bash
make connectors-smoke
make ai-smoke
```

See `EXTERNAL_VERIFICATION.md` for the supported environment variables and expected connection JSON.

## Git workflow

For a new repository created from this source tree:

```bash
git init
git add .
git commit -m "feat: initial SyntheticForge AI release"
gh repo create <owner>/syntheticforge-ai --public --source=. --remote=origin --push
```

For this repository, use short-lived branches for feature work and require CI to pass before merging to `main`.
