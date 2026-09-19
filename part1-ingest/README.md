# part1-ingest

Scrapes a public help center into clean Markdown, one file per article.

## Setup

```bash
cd part1-ingest            # from the repo root
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env
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
python -m app.scrape       # writes out/<id>-<slug>.md
pytest
```
