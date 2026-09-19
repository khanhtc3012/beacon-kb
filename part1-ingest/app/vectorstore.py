"""Create, list and upload to an OpenAI vector store."""
import hashlib
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

import openai

log = logging.getLogger("beacon-kb")

MAX_CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 200
UPLOAD_WORKERS = 8
BATCH_SIZE = 200  # the API accepts up to 2000 files per batch
TOKEN_ENCODING = "cl100k_base"  # used only to estimate chunk counts


@dataclass(frozen=True)
class Chunking:
    """Static chunking. The API requires 100 <= max_tokens <= 4096 and overlap <= max_tokens / 2."""

    max_tokens: int = MAX_CHUNK_TOKENS
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS

    def __post_init__(self):
        if not 100 <= self.max_tokens <= 4096:
            raise ValueError("max_tokens must be between 100 and 4096")
        if not 0 <= self.overlap_tokens <= self.max_tokens // 2:
            raise ValueError("overlap_tokens must be between 0 and max_tokens / 2")

    def param(self) -> dict:
        return {
            "type": "static",
            "static": {
                "max_chunk_size_tokens": self.max_tokens,
                "chunk_overlap_tokens": self.overlap_tokens,
            },
        }


@dataclass
class UploadResult:
    files: int = 0
    completed: int = 0
    failed: int = 0
    est_chunks: int = 0
    failures: List[Tuple[str, str]] = field(default_factory=list)  # (article id or filename, reason)


def fingerprint(markdown: str, chunking: Chunking) -> str:
    """Hash of what ends up in the store: the text and how it was chunked, so changing either re-uploads."""
    return hashlib.sha256(f"{chunking.max_tokens}/{chunking.overlap_tokens}\n{markdown}".encode("utf-8")).hexdigest()


def file_attributes(doc, chunking: Chunking) -> Dict[str, str]:
    """Per-file metadata stored in the vector store; `list_remote` reads it back."""
    attrs = {
        "article_id": doc.article_id,
        "content_hash": fingerprint(doc.markdown, chunking),
        "url": doc.url,
        "updated_at": doc.updated_at,
    }
    return {key: value for key, value in attrs.items() if value}


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding(TOKEN_ENCODING)
    except Exception as exc:  # tiktoken downloads its vocabulary on first use; an estimate must not fail the job
        log.warning("tiktoken unavailable (%s: %s), estimating tokens as characters / 4", type(exc).__name__, exc)
        return None


def count_tokens(text: str) -> int:
    encoding = _encoding()
    if encoding is None:
        return math.ceil(len(text) / 4)
    return len(encoding.encode(text, disallowed_special=()))


def estimate_chunks(tokens: int, chunking: Chunking) -> int:
    """The API does not report chunk counts, so estimate: each chunk after the first adds max - overlap tokens."""
    if tokens <= 0:
        return 0
    if tokens <= chunking.max_tokens:
        return 1
    stride = chunking.max_tokens - chunking.overlap_tokens
    return 1 + math.ceil((tokens - chunking.max_tokens) / stride)


def find_store(client, name: str):
    """The vector store called `name` (the most recent one if several), or None."""
    matches = [store for store in client.vector_stores.list(limit=100) if store.name == name]
    if len(matches) > 1:
        log.warning("%d vector stores are named %r, using the most recent", len(matches), name)
    return matches[0] if matches else None


def get_or_create_store(client, name: str) -> str:
    """Return the id of the vector store called `name`, creating it if missing."""
    store = find_store(client, name)
    if store is None:
        store = client.vector_stores.create(name=name)
        log.info("created vector store %r", name)
    return store.id


def _is_fatal(exc: Exception) -> bool:
    """Errors that would fail every file the same way: stop instead of retrying 400 times."""
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return True
    # out of credit arrives as a 429 with type `insufficient_quota` and code `insufficient_quota` or `credit_balance_exhausted`
    return isinstance(exc, openai.RateLimitError) and "insufficient_quota" in (
        getattr(exc, "type", None), getattr(exc, "code", None)
    )


def _create_file(client, doc) -> Tuple[object, Optional[str], Optional[str]]:
    try:
        created = client.files.create(
            file=(doc.filename, doc.markdown.encode("utf-8"), "text/markdown"),
            purpose="assistants",
        )
        return doc, created.id, None
    except openai.OpenAIError as exc:
        if _is_fatal(exc):
            raise
        return doc, None, f"{type(exc).__name__}: {exc}"


def upload_docs(
    client,
    store_id: str,
    docs: Sequence,
    chunking: Chunking,
    workers: int = UPLOAD_WORKERS,
    batch_size: int = BATCH_SIZE,
) -> UploadResult:
    """Upload each doc as a file, then attach the files to the store in batches with per-file attributes."""
    result = UploadResult(files=len(docs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        created = list(pool.map(lambda doc: _create_file(client, doc), docs))

    ready = []
    for doc, file_id, error in created:
        if file_id:
            ready.append((doc, file_id))
        else:
            result.failed += 1
            result.failures.append((doc.article_id, error))
    result.est_chunks = sum(estimate_chunks(count_tokens(doc.markdown), chunking) for doc, _ in ready)

    for start in range(0, len(ready), batch_size):
        part = ready[start:start + batch_size]
        batch = client.vector_stores.file_batches.create_and_poll(
            store_id,
            files=[
                {"file_id": file_id, "attributes": file_attributes(doc, chunking), "chunking_strategy": chunking.param()}
                for doc, file_id in part
            ],
        )
        counts = batch.file_counts
        result.completed += counts.completed
        bad = counts.failed + counts.cancelled
        result.failed += bad
        if bad:
            for status in ("failed", "cancelled"):
                for item in client.vector_stores.file_batches.list_files(batch.id, vector_store_id=store_id, filter=status):
                    reason = item.last_error.message if item.last_error else status
                    result.failures.append(((item.attributes or {}).get("article_id", item.id), reason))
    return result


@dataclass
class RemoteFile:
    file_id: str
    content_hash: str
    created_at: int = 0


@dataclass
class Remote:
    files: Dict[str, RemoteFile] = field(default_factory=dict)  # article id -> its newest completed file
    junk: List[str] = field(default_factory=list)  # file ids to delete: failed, cancelled, older duplicates


def list_remote(client, store_id: str) -> Remote:
    """What the store holds now, read from the attributes `upload_docs` attached."""
    remote = Remote()
    for item in client.vector_stores.files.list(store_id, limit=100):
        attrs = item.attributes or {}
        article_id = attrs.get("article_id")
        if item.status in ("failed", "cancelled"):
            remote.junk.append(item.id)
            continue
        if item.status != "completed":
            log.warning("file %s is still %s, leaving it alone", item.id, item.status)
            continue
        if not article_id:
            log.warning("file %s has no article_id, leaving it alone", item.id)
            continue
        found = RemoteFile(item.id, attrs.get("content_hash", ""), item.created_at)
        current = remote.files.get(article_id)
        if current is not None:
            log.warning("article %s is in the store twice, keeping the newer file", article_id)
            current, found = sorted((current, found), key=lambda f: f.created_at)
            remote.junk.append(current.file_id)
        remote.files[article_id] = found
    return remote


def delete_file(client, store_id: str, file_id: str) -> None:
    """Remove a file from the store and delete the underlying file (removing it from the store leaves it behind)."""
    for delete in (
        lambda: client.vector_stores.files.delete(file_id, vector_store_id=store_id),
        lambda: client.files.delete(file_id),
    ):
        try:
            delete()
        except openai.NotFoundError:
            pass


def delete_store(client, store_id: str) -> int:
    """Delete a vector store and the files uploaded to it (deleting the store alone leaves them behind)."""
    file_ids = [item.id for item in client.vector_stores.files.list(store_id, limit=100)]
    client.vector_stores.delete(store_id)
    for file_id in file_ids:
        client.files.delete(file_id)
    return len(file_ids)
