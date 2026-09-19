"""Ask the support bot one question: Responses API with file_search over the vector store."""
import argparse
import logging
import os
import sys

import openai
from dotenv import load_dotenv
from openai import OpenAI

log = logging.getLogger("beacon-kb")

# Verbatim from the brief. Do not reword.
SYSTEM_PROMPT = (
    "You are OptiBot, the customer-support bot for OptiSigns.com.\n"
    "• Tone: helpful, factual, concise.\n"
    "• Only answer using the uploaded docs.\n"
    "• Max 5 bullet points; else link to the doc.\n"
    '• Cite up to 3 "Article URL:" lines per reply.'
)
DEFAULT_MODEL = "gpt-5.4-mini"


def ask(client, store_id: str, question: str, model: str = DEFAULT_MODEL):
    return client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=question,
        tools=[{"type": "file_search", "vector_store_ids": [store_id]}],
        include=["file_search_call.results"],
    )


def retrieved(response) -> list:
    """The chunks file_search handed to the model: (filename, score, has 'Article URL:' in text, url attribute)."""
    rows = []
    for item in response.output:
        if item.type == "file_search_call":
            for result in item.results or []:
                rows.append((
                    result.filename,
                    result.score,
                    "Article URL:" in (result.text or ""),
                    (result.attributes or {}).get("url"),
                ))
    return rows


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ask the support bot a question.")
    parser.add_argument("question")
    parser.add_argument("--store-name", default=os.environ.get("VECTOR_STORE_NAME", "support-kb"))
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
    args = parser.parse_args(argv)

    client = OpenAI()
    store = next((s for s in client.vector_stores.list(limit=100) if s.name == args.store_name), None)
    if store is None:
        print(f"no vector store named {args.store_name!r}", file=sys.stderr)
        return 1
    try:
        response = ask(client, store.id, args.question, args.model)
    except openai.APIStatusError as exc:
        print(f"OpenAI error {exc.status_code}: {exc.message}", file=sys.stderr)
        return 1

    print(response.output_text)
    print("\n--- retrieved chunks (filename | score | chunk text has 'Article URL:' | url attribute)")
    for filename, score, has_url, url in retrieved(response):
        print(f"{filename} | {score:.2f} | {has_url} | {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
