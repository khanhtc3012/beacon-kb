"""Shared test doubles."""
from types import SimpleNamespace as NS

from app.scrape import Doc


def doc(n, text="# T\n\nbody\n"):
    return Doc(str(n), f"T{n}", f"https://x/{n}", "2026-01-01T00:00:00Z", f"{n}-t.md", text)


class FakeOpenAI:
    """In-memory stand-in for the part of the OpenAI client that sync uses. Keeps state between runs."""

    def __init__(self, fail_names=()):
        self.raw, self.attached, self.ops = {}, {}, []  # file id -> name; file id -> store entry; (op, id) log
        self.fail_names = set(fail_names)
        self._n = 0
        self.files = NS(create=self._create, delete=self._delete_raw)
        self.stores = []
        self.vector_stores = NS(
            list=lambda limit=None: iter(self.stores),
            create=self._create_store,
            retrieve=self._retrieve_store,
            files=NS(list=self._list, delete=self._detach),
            file_batches=NS(create_and_poll=self._batch, list_files=self._failed),
        )

    def _next(self):
        self._n += 1
        return self._n

    def _create(self, file, purpose):
        file_id = f"file-{self._next()}"
        self.raw[file_id] = file[0]
        return NS(id=file_id)

    def _create_store(self, name):
        store = NS(id="vs_1", name=name)
        self.stores.append(store)
        return store

    def _retrieve_store(self, store_id):
        by_status = [v["status"] for v in self.attached.values()]
        return NS(file_counts=NS(total=len(by_status), completed=by_status.count("completed"),
                                 failed=by_status.count("failed"), in_progress=0, cancelled=0))

    def _delete_raw(self, file_id):
        self.raw.pop(file_id, None)
        self.ops.append(("delete", file_id))

    def _batch(self, store_id, files):
        counts = {"completed": 0, "failed": 0, "cancelled": 0}
        for f in files:
            status = "failed" if self.raw[f["file_id"]] in self.fail_names else "completed"
            self.attached[f["file_id"]] = {"attributes": f["attributes"], "status": status, "created_at": self._next()}
            counts[status] += 1
            self.ops.append(("attach", f["file_id"]))
        return NS(id="batch", file_counts=NS(**counts))

    def _failed(self, batch_id, vector_store_id, filter):
        if filter != "failed":
            return iter([])
        return iter([
            NS(id=fid, attributes=v["attributes"], last_error=NS(message="cannot parse"))
            for fid, v in self.attached.items() if v["status"] == "failed"
        ])

    def _list(self, store_id, limit=None):
        return iter([
            NS(id=fid, attributes=v["attributes"], status=v["status"], created_at=v["created_at"])
            for fid, v in self.attached.items()
        ])

    def _detach(self, file_id, vector_store_id):
        self.attached.pop(file_id, None)
        self.ops.append(("detach", file_id))

    def article_ids(self):
        return sorted(v["attributes"]["article_id"] for v in self.attached.values())
