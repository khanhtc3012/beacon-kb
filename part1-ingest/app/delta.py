"""Classify scraped articles against what is already in the vector store."""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Delta:
    added: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def classify(scraped: Dict[str, str], remote: Dict[str, str]) -> Delta:
    """Both arguments map article id -> content hash.

    Not in `remote` -> added; hash differs -> updated; otherwise skipped.
    """
    raise NotImplementedError("step 6")
