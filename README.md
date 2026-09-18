# beacon-kb

Two independent pieces of work in one repo.

| Folder | What | Start here |
|---|---|---|
| [`part1-ingest/`](part1-ingest/) | Daily job that scrapes a public help center to Markdown and syncs it to an OpenAI vector store, uploading only what changed. Has the `Dockerfile`. | [`part1-ingest/README.md`](part1-ingest/README.md) |
| [`part2-plan/`](part2-plan/) | Planning document for building a clone of a web management portal for digital signage. No code. | [`part2-plan/PLAN.md`](part2-plan/PLAN.md) |

Quick start for the job:

```bash
cd part1-ingest
cp .env.sample .env   # set OPENAI_API_KEY
```

Then follow `part1-ingest/README.md`.
