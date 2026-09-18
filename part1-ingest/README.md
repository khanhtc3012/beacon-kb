# part1-ingest

Daily job that scrapes a public help center into clean Markdown and syncs it to an OpenAI vector store, uploading only what changed. A support bot answers from that store.

Status: scaffold only. Sections marked TBD are filled in as each step lands.

## Setup

```bash
cd part1-ingest            # from the repo root
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env        # then set OPENAI_API_KEY
```

Windows (PowerShell):

```powershell
cd part1-ingest
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.sample .env   # then set OPENAI_API_KEY
```

## Run locally

```bash
python main.py             # runs once, exits 0 on success
pytest
```

Docker: TBD (step 8).

## How it works

1. **Scrape.** Articles come from the Zendesk Help Center API, not HTML crawling. Drafts are skipped. TBD: counts.
2. **Markdown.** Headings, code blocks, lists and tables are kept. Relative links are rewritten to absolute so they still work outside the site. Each file starts with an `Article URL:` line so the bot can cite it. TBD.
3. **Chunking.** TBD (step 4): size, overlap and why.
4. **Delta.** TBD (step 6): content hash per article, stored as file attributes in the vector store; reports `added / updated / skipped`.

## Daily job

TBD (step 9): platform, schedule, link to logs (`docs/last_run.log`).

## Sample answer

TBD (step 5): `docs/screenshot.png`.

## Cut / left for later

TBD.
