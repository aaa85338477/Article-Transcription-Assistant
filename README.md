# Article Transcription Assistant

A Streamlit-based editorial workstation for game-industry content teams.

This project combines article scraping, YouTube transcript ingestion, local research files, optional Obsidian knowledge support, multi-stage drafting, review and rewrite flows, de-AI finalization, highlighted reading output, podcast/script generation, and export/distribution tools in a single UI.

## What This Branch Includes

This branch reflects the current production workflow, including:

- Multi-source ingestion:
  - Web articles
  - YouTube transcripts
  - Local Word / Excel / TXT / image files
- Two working modes:
  - Manual step-by-step workflow
  - Full auto-drive workflow
- Multi-role drafting pipeline
- Reviewer + revision workflow
- Structured article output with:
  - candidate title group
  - opening background lede
  - sectioned body structure
- De-AI stage with three style variants:
  - `Standard`
  - `Community Article`
  - `Natural Conversational`
- Dual final outputs:
  - clean article output
  - highlighted reading version
- Rich copy support for highlighted reading output
- Version timeline for draft/final article history
- Version restore that now restores both:
  - final article body
  - highlighted reading version
- Optional downstream outputs:
  - short-video narration + storyboard script
  - single-host podcast script
  - podcast audio synthesis
  - image search keywords
  - Word export
  - Feishu push
- Prompt management in the UI
- Draft persistence and recovery
- Optional Obsidian-assisted research enrichment

## Typical Use Cases

- Turn overseas game-industry articles into Chinese long-form content
- Turn YouTube commentary into structured Chinese writeups
- Produce analysis, publishing commentary, design breakdowns, and game-news articles
- Generate article + short-video script assets from one source package
- Run a second-pass rewrite for stronger readability or less AI-like phrasing

## Project Layout

```text
.
|-- app.py
|-- podcast_audio.py
|-- podcast_script.py
|-- prompts.json
|-- requirements.txt
|-- launch_article_tool.cmd
|-- tests/
|-- .streamlit/
|-- draft_state.json      # generated at runtime
|-- ai_diagnostics.log    # generated at runtime
`-- runtime_audio/        # generated at runtime
```

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

## Running the App

Standard startup:

```bash
streamlit run app.py
```

Windows convenience launcher:

```bash
launch_article_tool.cmd
```

After startup, configure the sidebar with the model endpoint and API key you want to use.

## Workflow Overview

### 1. Source Intake

You can combine multiple inputs in one run:

- article URLs
- YouTube URLs
- local files such as Word / Excel / TXT / images

The app merges them into one source packet.

### 2. Mode Selection

- `Manual`: confirm each stage yourself
- `Auto-drive`: route the article automatically and generate through to final draft

### 3. Draft Generation

The editor role produces structured output with:

- candidate titles
- article body

### 4. Review and Revision

The reviewer checks:

- factual consistency
- logic quality
- opening context clarity
- heading structure
- title-group continuity

### 5. De-AI Finalization

The current branch supports three rewrite variants:

- `Standard`: balanced and stable
- `Community Article`: better suited for player-community/forum long posts
- `Natural Conversational`: more like an experienced human writer talking the point through

This stage outputs three blocks:

- `Pure Title Group`
- `Pure Final Article`
- `Highlighted Reading Version`

### 6. Distribution Workspace

The final workspace supports:

- final article review
- highlighted reading view
- formatted copy for highlighted output
- version timeline switching
- narration/storyboard generation
- podcast script generation
- podcast audio synthesis
- image keyword generation
- Word export
- Feishu push
- chat-based polishing

## Prompt Configuration

Prompt configuration is stored in `prompts.json`.

Main sections:

- `editors`: editor role prompts
- `reviewer`: reviewer prompt
- `global_instruction`: global writing constraints appended into the drafting flow

You can manage prompts either:

- by editing `prompts.json` directly
- or through the in-app prompt management UI

You can also point to a custom prompt file with the `PROMPTS_FILE` environment variable.

## Optional Integrations

### Obsidian

If an Obsidian vault path is configured, the app can:

- retrieve local notes as background research
- build a compact research brief
- show an influence map that highlights which final paragraphs were clearly informed by local notes

### Podcast / TTS

If podcast features are enabled, the app can:

- generate a single-host podcast script from the current final article
- synthesize audio into `runtime_audio/podcasts/`
- cache TTS fragments in `runtime_audio/tts_cache/`

## Important Runtime Files

These files and folders are runtime artifacts and are usually not meant to be committed:

- `draft_state.json`
- `ai_diagnostics.log`
- `runtime_audio/`
- `.tmp_test_runtime/`

## Dependencies

Main packages used in this project:

- `streamlit`
- `trafilatura`
- `youtube-transcript-api`
- `beautifulsoup4`
- `openai`
- `python-docx`
- `pandas`
- `openpyxl`
- `xlrd`
- `dashscope`
- `pydub`
- `audioop-lts`
- `imageio-ffmpeg`
- `requests`
- `httpx`

## Notes

- This is a Streamlit application, not a CLI-first tool.
- Prompt edits inside the UI are written back to `prompts.json`.
- Working drafts are persisted to `draft_state.json`.
- AI call diagnostics are written to `ai_diagnostics.log`.
- Highlighted reading output is now treated as part of a restorable article version.
- Some downstream features depend on external credentials and model availability.
- If you want image-aware analysis, use a model endpoint that supports visual input.

## Tests

Run the focused regression suite:

```bash
python -m unittest tests.test_prompt_structure tests.test_copy_formatting tests.test_article_versions tests.test_podcast_script
```

Run a quick syntax check:

```bash
python -m py_compile app.py
```
