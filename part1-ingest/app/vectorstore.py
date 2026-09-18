"""Create, list and upload to an OpenAI vector store."""
from pathlib import Path
from typing import Dict, List


def get_or_create_store(client, name: str) -> str:
    """Return the id of the vector store called `name`, creating it if missing."""
    raise NotImplementedError("step 4")


def list_remote(client, store_id: str) -> Dict[str, str]:
    """Map article id -> content hash for files already in the store."""
    raise NotImplementedError("step 6")


def upload_files(client, store_id: str, paths: List[Path]) -> None:
    """Upload Markdown files in a batch with a static chunking strategy."""
    raise NotImplementedError("step 4")


def delete_file(client, store_id: str, file_id: str) -> None:
    """Remove a file from the store and delete the underlying file."""
    raise NotImplementedError("step 6")
