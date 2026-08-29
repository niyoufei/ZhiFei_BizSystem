from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Iterable, Mapping

Loader = Callable[[], Any]
Saver = Callable[[Any], None]
TransactionDecorator = Callable[[Callable[[], Any]], Callable[[], Any]]
TransactionFactory = Callable[..., TransactionDecorator]


class CallbackRepository:
    def __init__(
        self,
        name: str,
        *,
        load: Loader,
        save: Saver | None = None,
    ) -> None:
        self.name = name
        self._load = load
        self._save = save

    @property
    def writable(self) -> bool:
        return self._save is not None

    def load(self) -> Any:
        return self._load()

    def save(self, value: Any) -> None:
        if self._save is None:
            raise RuntimeError(f"repository is read-only: {self.name}")
        self._save(value)


class RepositorySet:
    def __init__(self, repositories: Mapping[str, CallbackRepository]) -> None:
        self._repositories = dict(repositories)

    def __getitem__(self, name: str) -> CallbackRepository:
        try:
            return self._repositories[name]
        except KeyError as exc:
            raise KeyError(f"unknown repository: {name}") from exc

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._repositories)


def _append_rollback_note(error: BaseException, rollback_error: BaseException) -> None:
    note = (
        "repository unit-of-work rollback also failed: "
        f"{type(rollback_error).__name__}: {rollback_error}"
    )
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


class RepositoryUnitOfWork:
    def __init__(
        self,
        repositories: RepositorySet,
        *,
        transaction_factory: TransactionFactory,
    ) -> None:
        self._repositories = repositories
        self._transaction_factory = transaction_factory

    def run(
        self,
        store_names: Iterable[str],
        operation: Callable[[RepositorySet], Any],
    ) -> Any:
        names = tuple(store_names)
        if len(set(names)) != len(names):
            raise ValueError("unit-of-work store names must be unique")
        for name in names:
            self._repositories[name]

        @self._transaction_factory(*names)
        def commit() -> Any:
            originals = {name: deepcopy(self._repositories[name].load()) for name in names}
            attempted: list[str] = []
            session_repositories: Dict[str, CallbackRepository] = {}
            for name in names:
                base_repository = self._repositories[name]
                state = {"value": deepcopy(originals[name])}

                def load_current(*, _state=state) -> Any:
                    return deepcopy(_state["value"])

                def save_current(
                    value: Any,
                    *,
                    _name=name,
                    _base=base_repository,
                    _state=state,
                ) -> None:
                    if not _base.writable:
                        raise RuntimeError(f"repository is read-only: {_name}")
                    if _name not in attempted:
                        attempted.append(_name)
                    _base.save(deepcopy(value))
                    _state["value"] = deepcopy(value)

                session_repositories[name] = CallbackRepository(
                    name,
                    load=load_current,
                    save=save_current if base_repository.writable else None,
                )

            try:
                return operation(RepositorySet(session_repositories))
            except BaseException as error:
                for name in reversed(attempted):
                    try:
                        self._repositories[name].save(deepcopy(originals[name]))
                    except BaseException as rollback_error:
                        _append_rollback_note(error, rollback_error)
                raise

        return commit()


def repositories_from_callbacks(
    *,
    loaders: Mapping[str, Loader],
    savers: Mapping[str, Saver],
) -> RepositorySet:
    unknown_savers = set(savers).difference(loaders)
    if unknown_savers:
        raise ValueError(f"repository savers without loaders: {sorted(unknown_savers)}")
    return RepositorySet(
        {
            name: CallbackRepository(
                name,
                load=loader,
                save=savers.get(name),
            )
            for name, loader in loaders.items()
        }
    )
