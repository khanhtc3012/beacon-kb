# part1-ingest

Scrapes a public help center into Markdown and keeps an OpenAI vector store in sync with it. A daily job uploads only what changed.

## Setup

```bash
cd part1-ingest            # from the repo root
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env        # set OPENAI_API_KEY; the other settings are optional
```

Windows (PowerShell):

```powershell
cd part1-ingest
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.sample .env
```

## Run

```bash
python -m app.scrape       # scrape only: writes out/<id>-<slug>.md
python main.py             # sync to the vector store; runs once, exits 0 when everything is in sync
pytest
```

Docker (only `OPENAI_API_KEY` is required):

```bash
docker build -t beacon-kb .
docker run --rm -e OPENAI_API_KEY=sk-... beacon-kb
```

## Chunking

Static chunks of 800 tokens with 200 tokens of overlap. The median article is about 800 tokens (409 articles, about 450k tokens in total), so a chunk is roughly one short article or one section. A 25% overlap keeps a step that falls on a boundary in both chunks. The API default overlap of 400 would give about 1,000 chunks instead of about 841 and mostly repeat text. The API does not report chunk counts, so the logged number is an estimate.

## What a run does

Each file is uploaded with its `article_id` and a hash of its text and chunk settings. A run compares those with the store: not there is **added**, hash changed is **updated** (the new file is uploaded before the old one is deleted), same hash is **skipped**, and an article gone from the help center is **removed**. Removal is off when `ARTICLE_LIMIT` is set. The exit code is 1 if any article failed.

```
[sync] added=30 updated=0 skipped=0 removed=0 failed=0 duration=25s
[embedded] files=30 est_chunks=~114 (max=800 overlap=200)
```