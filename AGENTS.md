# AGENTS.md

This file is the developer guide for humans and AI agents working on this repository.

## 1. Project Purpose

This project is a local Streamlit production workbench for article creation and editorial workflows.

It is not a single-purpose “text generator”. It supports an end-to-end pipeline:

1. Collect source material from URLs, videos, and uploaded files
2. Generate a draft
3. Review and revise
4. De-AI and finalize
5. Generate a highlighted reading version
6. Produce downstream deliverables such as podcast script/audio, Word export, image keywords, and Feishu cloud docs

The primary use case is high-frequency, single-operator content production, especially for long-form Chinese editorial workflows.

## 2. Architecture Overview

### Main App

- Core entrypoint: [app.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/app.py)
- Framework: `streamlit`
- This is a monolithic app. Most business logic, UI rendering, state transitions, and integrations live in `app.py`.

Do not assume the project is modularized by domain. In most cases, the right way to work in this repo is to make careful, local changes inside `app.py` instead of attempting broad refactors.

### Important Supporting Files

- [prompts.json](E:/AI project/Article-Transcription-Assistant-feature-sandbox/prompts.json): editor/reviewer/system prompt configuration
- [podcast_script.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/podcast_script.py): podcast script generation helpers
- [podcast_audio.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/podcast_audio.py): podcast audio generation helpers
- [requirements.txt](E:/AI project/Article-Transcription-Assistant-feature-sandbox/requirements.txt): runtime dependencies
- [README.md](E:/AI project/Article-Transcription-Assistant-feature-sandbox/README.md): user-facing overview
- [DESIGN.md](E:/AI project/Article-Transcription-Assistant-feature-sandbox/DESIGN.md): UI direction/spec notes

### State Files

These are runtime state files. Handle with care.

- [draft_state.json](E:/AI project/Article-Transcription-Assistant-feature-sandbox/draft_state.json): current draft/session snapshot
- [task_queue_state.json](E:/AI project/Article-Transcription-Assistant-feature-sandbox/task_queue_state.json): task queue, templates, archive state
- [ai_diagnostics.log](E:/AI project/Article-Transcription-Assistant-feature-sandbox/ai_diagnostics.log): diagnostics/log output

### Runtime Output Directories

- `runtime_audio/`
- `.tmp_test_runtime/`

These are runtime/generated artifacts. Do not treat them as source of truth for product logic.

## 3. Primary Workflow Model

The app is organized around a multi-step Streamlit workflow.

### Step 1: Source Intake

- Article URLs
- YouTube URLs
- Uploaded local files
- Optional image assets

Output of this step is effectively a merged source packet.

### Step 2: Draft Generation

- Manual mode or auto-drive mode
- Uses selected writing/editor role

### Step 3: Review

- Reviewer checks facts, logic, framing, structure, and style

### Step 4: Revision

- Review feedback is applied to produce a revised article

### Step 5: De-AI Finalization

Outputs:

- title candidates
- final article
- highlighted reading version

### Step 6: Delivery / Distribution

Includes:

- copy final article
- copy highlighted reading version
- regenerate highlighted version only
- podcast script/audio
- image keyword extraction
- Word export
- Feishu cloud doc publishing
- Feishu group push

## 4. Key Product Capabilities

### Multi-source ingestion

The project merges multiple sources into a shared editorial context:

- web pages
- YouTube transcripts
- uploaded documents
- supporting images

### Enhanced article extraction

Current extraction pipeline is layered:

1. direct fetch
2. `r.jina.ai` fallback
3. `scrapling` enhanced fallback

Dependency note:

- `scrapling[fetchers]>=0.4.7` is required for the enhanced path

### Task queue

The app is no longer single-document only. It supports:

- switching tasks
- cloning tasks
- deleting tasks
- search by title/site
- batch cleanup for completed/failed tasks
- auto-archive of completed tasks
- restoring archived tasks
- task templates

### Highlighted reading version

This is a distinct output mode, not a trivial copy of the main article.

Historically this area has been fragile. Be careful when changing:

- heading level conversion
- fidelity checks against final article
- rebuild/fallback logic
- copy/export formatting

If you modify highlight logic, verify both:

- structural fidelity
- rich-text/highlight preservation

### Feishu publishing

The project supports Feishu cloud doc publishing.

Expected secrets:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_FOLDER_TOKEN` (optional but commonly used)

Important implementation note:

- Feishu doc blocks append must be chunked
- the API has a maximum children count per append request

Do not regress this behavior when editing the Feishu publishing path.

### UI Preview Mode

The project includes a UI preview/testing mode for simulating workflow states without making real external calls.

When touching helpers used by both runtime app code and extracted/unit-test contexts, avoid coupling them too tightly to global UI-only helpers. Prefer direct `st.session_state` reads when the logic must survive AST-extracted test harnesses.

## 5. Testing Strategy

Test suite location:

- [tests](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests)

Important test modules:

- [tests/test_article_extraction.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_article_extraction.py)
- [tests/test_article_versions.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_article_versions.py)
- [tests/test_copy_formatting.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_copy_formatting.py)
- [tests/test_feishu_docs.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_feishu_docs.py)
- [tests/test_podcast_audio.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_podcast_audio.py)
- [tests/test_podcast_script.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_podcast_script.py)
- [tests/test_prompt_structure.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_prompt_structure.py)
- [tests/test_task_queue.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_task_queue.py)
- [tests/test_ui_preview_mode.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests/test_ui_preview_mode.py)

### Common Verification Commands

Run targeted regression checks after most UI/workflow changes:

```powershell
python -m unittest tests/test_ui_preview_mode.py tests/test_prompt_structure.py tests/test_task_queue.py
python -m py_compile app.py
```

Run broader validation when touching extraction, Feishu, or output formatting:

```powershell
python -m unittest tests/test_article_extraction.py tests/test_feishu_docs.py tests/test_copy_formatting.py tests/test_article_versions.py
python -m py_compile app.py podcast_audio.py podcast_script.py
```

## 6. Editing Guidelines

### Prefer local changes over refactors

This repo has a lot of stateful coupling. Favor surgical edits.

### Respect session state

Many features depend on `st.session_state`. Before changing control flow, look for:

- initialization defaults
- restore/reload behavior
- task queue synchronization
- preview-mode behavior

### Be careful with helper extraction tests

Some tests do not import the whole app module at runtime; they AST-extract selected helpers. If a helper suddenly depends on unrelated globals or helper functions, tests may start failing with `NameError`.

### Do not casually change runtime artifact behavior

Avoid needless edits to:

- `draft_state.json`
- `task_queue_state.json`
- runtime audio/cache outputs
- diagnostics logs

### UI theming guidance

UI is currently being pushed toward a more curated visual system.

When editing UI:

- prefer explicit colors over implicit browser defaults
- avoid globally overriding icon font families
- keep Streamlit icon fonts intact
- test in both Chrome and the Codex embedded browser when touching typography

## 7. What to Read First Before Making Changes

If you are new to this repo, read/inspect in this order:

1. [README.md](E:/AI project/Article-Transcription-Assistant-feature-sandbox/README.md)
2. [DESIGN.md](E:/AI project/Article-Transcription-Assistant-feature-sandbox/DESIGN.md)
3. [app.py](E:/AI project/Article-Transcription-Assistant-feature-sandbox/app.py)
4. relevant tests under [tests](E:/AI project/Article-Transcription-Assistant-feature-sandbox/tests)

## 8. Recommended Agent Behavior

If you are an AI coding agent working here:

- assume `app.py` is the operational center
- inspect tests before changing workflow helpers
- avoid sweeping formatting-only rewrites
- verify changes with targeted tests
- preserve user data and runtime state unless explicitly asked otherwise
- treat Feishu, highlighted article logic, task queue, and extraction fallbacks as sensitive areas

## 9. Non-goals

Do not treat this repository as:

- a generic web crawler framework
- a stateless article generator
- a backend service with clean module boundaries

It is a highly stateful local editorial workstation. Optimize for continuity and operator productivity.
