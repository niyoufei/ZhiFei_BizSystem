from __future__ import annotations

from copy import deepcopy

import pytest

from app.repository_uow import RepositoryUnitOfWork, repositories_from_callbacks


class StoreHarness:
    def __init__(self, *, fail_calls=None):
        self.data = {
            "projects": [{"id": "p1"}],
            "submissions": [{"id": "s1"}],
            "settings": {"enabled": False},
        }
        self.original = deepcopy(self.data)
        self.fail_calls = fail_calls or {}
        self.write_counts = {name: 0 for name in self.data}
        self.writes = []
        self.lock_calls = []

    def loader(self, name):
        return lambda: deepcopy(self.data[name])

    def saver(self, name):
        def save(value):
            self.write_counts[name] += 1
            call_number = self.write_counts[name]
            self.writes.append(name)
            self.data[name] = deepcopy(value)
            if call_number in self.fail_calls.get(name, set()):
                raise OSError(f"controlled {name} save failure #{call_number}")

        return save

    def transaction_factory(self, *names):
        self.lock_calls.append(names)
        return lambda func: func

    def unit_of_work(self, *, read_only=()):
        read_only = set(read_only)
        return RepositoryUnitOfWork(
            repositories_from_callbacks(
                loaders={name: self.loader(name) for name in self.data},
                savers={name: self.saver(name) for name in self.data if name not in read_only},
            ),
            transaction_factory=self.transaction_factory,
        )


def test_unit_of_work_locks_stores_and_exposes_updated_working_copy():
    stores = StoreHarness()

    def operation(repositories):
        projects = repositories["projects"].load()
        projects.append({"id": "p2"})
        repositories["projects"].save(projects)
        assert repositories["projects"].load() == projects
        repositories["settings"].save({"enabled": True})
        return "committed"

    result = stores.unit_of_work().run(("projects", "settings"), operation)

    assert result == "committed"
    assert stores.lock_calls == [("projects", "settings")]
    assert stores.writes == ["projects", "settings"]
    assert stores.data["projects"] == [{"id": "p1"}, {"id": "p2"}]
    assert stores.data["settings"] == {"enabled": True}


def test_unsaved_working_copy_mutation_does_not_escape_unit_of_work():
    stores = StoreHarness()

    def operation(repositories):
        projects = repositories["projects"].load()
        projects.append({"id": "not-saved"})

    stores.unit_of_work().run(("projects",), operation)

    assert stores.writes == []
    assert stores.data == stores.original


@pytest.mark.parametrize("failing_store", ["projects", "submissions", "settings"])
def test_unit_of_work_restores_every_attempted_store_after_ambiguous_failure(
    failing_store,
):
    stores = StoreHarness(fail_calls={failing_store: {1}})

    def operation(repositories):
        for name in ("projects", "submissions", "settings"):
            value = repositories[name].load()
            if isinstance(value, list):
                value.append({"partial": True})
            else:
                value["partial"] = True
            repositories[name].save(value)

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        stores.unit_of_work().run(("projects", "submissions", "settings"), operation)

    assert stores.data == stores.original


def test_unit_of_work_preserves_primary_error_and_adds_rollback_note():
    stores = StoreHarness(fail_calls={"projects": {1, 2}})

    def operation(repositories):
        repositories["projects"].save([{"id": "partial"}])

    with pytest.raises(OSError, match="controlled projects save failure #1") as exc_info:
        stores.unit_of_work().run(("projects",), operation)

    assert any(
        "controlled projects save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )


def test_read_only_repository_rejects_write_without_rollback_write():
    stores = StoreHarness()

    with pytest.raises(RuntimeError, match="repository is read-only: projects"):
        stores.unit_of_work(read_only=("projects",)).run(
            ("projects",),
            lambda repositories: repositories["projects"].save([]),
        )

    assert stores.writes == []
    assert stores.data == stores.original


def test_unit_of_work_rejects_duplicate_or_unknown_store_names_before_locking():
    stores = StoreHarness()
    unit_of_work = stores.unit_of_work()

    with pytest.raises(ValueError, match="must be unique"):
        unit_of_work.run(("projects", "projects"), lambda _repositories: None)
    with pytest.raises(KeyError, match="unknown repository: missing"):
        unit_of_work.run(("missing",), lambda _repositories: None)

    assert stores.lock_calls == []


def test_repository_factory_rejects_saver_without_loader():
    with pytest.raises(ValueError, match="savers without loaders"):
        repositories_from_callbacks(
            loaders={"projects": lambda: []},
            savers={"missing": lambda _value: None},
        )
