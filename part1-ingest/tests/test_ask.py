from types import SimpleNamespace as NS

from app.ask import SYSTEM_PROMPT, ask, retrieved


def test_system_prompt_is_verbatim_from_the_brief():
    assert SYSTEM_PROMPT.split("\n") == [
        "You are OptiBot, the customer-support bot for OptiSigns.com.",
        "• Tone: helpful, factual, concise.",
        "• Only answer using the uploaded docs.",
        "• Max 5 bullet points; else link to the doc.",
        '• Cite up to 3 "Article URL:" lines per reply.',
    ]


def test_ask_uses_file_search_on_the_given_store_with_the_prompt():
    calls = []
    client = NS(responses=NS(create=lambda **kw: calls.append(kw) or "resp"))
    assert ask(client, "vs_1", "How do I add a YouTube video?", model="m") == "resp"
    kw = calls[0]
    assert kw["instructions"] == SYSTEM_PROMPT
    assert kw["input"] == "How do I add a YouTube video?"
    assert kw["tools"] == [{"type": "file_search", "vector_store_ids": ["vs_1"]}]
    assert "file_search_call.results" in kw["include"]


def test_retrieved_reports_whether_each_chunk_carries_the_url():
    response = NS(output=[
        NS(type="message"),
        NS(type="file_search_call", results=[
            NS(filename="a.md", score=0.9, text="Article URL: https://x/a", attributes={"url": "https://x/a"}),
            NS(filename="b.md", score=0.5, text="later part of the article", attributes=None),
        ]),
    ])
    assert retrieved(response) == [("a.md", 0.9, True, "https://x/a"), ("b.md", 0.5, False, None)]
