"""Classify scraped articles against what is already in the vector store."""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Delta:
    added: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)


def classify(scraped: Dict[str, str], remote: Dict[str, str], remove_missing: bool = False) -> Delta:
    """Both mappings are article id -> fingerprint.

    Not in `remote` -> added; fingerprint differs -> updated; otherwise skipped.
    With `remove_missing`, ids in `remote` but no longer scraped are removed.
    """
    delta = Delta()
    for article_id, fingerprint in scraped.items():
        if article_id not in remote:
            delta.added.append(article_id)
        elif remote[article_id] != fingerprint:
            delta.updated.append(article_id)
        else:
            delta.skipped.append(article_id)
    if remove_missing:
        delta.removed = sorted(set(remote) - set(scraped))
    return delta
