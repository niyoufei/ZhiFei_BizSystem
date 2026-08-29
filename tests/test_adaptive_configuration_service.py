from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import app.config as config_module
from app import adaptive_configuration_service
from app.config import ConfigLoader


def _apply_configuration(
    resources_dir,
    load_config,
    reload_config,
    *,
    apply_lexicon_patch=None,
):
    if apply_lexicon_patch is None:

        def default_lexicon_patch(*_args):
            return {"generation": "new-lexicon"}, ["lexicon-change"]

        apply_lexicon_patch = default_lexicon_patch
    return adaptive_configuration_service.apply_and_persist(
        "p1",
        [{"id": "s1", "project_id": "p1"}],
        resources_dir=resources_dir,
        backup_timestamp="20260829120000",
        load_config=load_config,
        reload_config=reload_config,
        build_adaptive_suggestions=lambda *_args: {
            "penalty_stats": {"P01": 2},
            "source": {"submission_count": 1},
        },
        build_adaptive_patch=lambda *_args: {"rubric_adjustments": {"P01": 1}},
        apply_adaptive_patch=apply_lexicon_patch,
        apply_rubric_patch=lambda *_args: (
            {"generation": "new-rubric"},
            ["rubric-change"],
        ),
    )


def _write_legacy_pair(resources_dir):
    resources_dir.mkdir()
    lexicon_path = resources_dir / "lexicon.yaml"
    rubric_path = resources_dir / "rubric.yaml"
    original_lexicon = "generation: old-lexicon\n"
    original_rubric = "generation: old-rubric\n"
    lexicon_path.write_text(original_lexicon, encoding="utf-8")
    rubric_path.write_text(original_rubric, encoding="utf-8")
    return lexicon_path, rubric_path, original_lexicon, original_rubric


def test_apply_configuration_publishes_atomic_snapshot_and_backups(
    monkeypatch,
    tmp_path,
):
    resources_dir = tmp_path / "resources"
    lexicon_path, rubric_path, original_lexicon, original_rubric = _write_legacy_pair(resources_dir)
    monkeypatch.setattr(config_module, "RESOURCES_DIR", resources_dir)
    loader = ConfigLoader()
    observer = ConfigLoader()
    assert loader.load().lexicon == {"generation": "old-lexicon"}
    assert observer.load().lexicon == {"generation": "old-lexicon"}

    result = _apply_configuration(
        resources_dir,
        loader.load,
        loader.reload,
    )

    active_snapshot = yaml.safe_load(
        (resources_dir / "active_config.yaml").read_text(encoding="utf-8")
    )
    assert (resources_dir / ".adaptive_configuration.lock").exists()
    assert active_snapshot["lexicon"] == {"generation": "new-lexicon"}
    assert active_snapshot["rubric"] == {"generation": "new-rubric"}
    assert lexicon_path.read_text(encoding="utf-8") == original_lexicon
    assert rubric_path.read_text(encoding="utf-8") == original_rubric

    lexicon_backup = Path(str(result["backup_path"]))
    rubric_backup = lexicon_backup.with_name(
        lexicon_backup.name.replace("lexicon.yaml.bak_", "rubric.yaml.bak_", 1)
    )
    backup_generation = lexicon_backup.name.removeprefix("lexicon.yaml.bak_")
    assert active_snapshot["generation"] == backup_generation
    assert lexicon_backup.read_text(encoding="utf-8") == original_lexicon
    assert rubric_backup.read_text(encoding="utf-8") == original_rubric
    loaded = loader.load()
    assert loaded.lexicon == {"generation": "new-lexicon"}
    assert loaded.rubric == {"generation": "new-rubric"}
    assert observer.load().lexicon == {"generation": "new-lexicon"}
    assert result["changes"] == ["lexicon-change", "rubric-change"]
    assert result["source"] == {"submission_count": 1}


def test_apply_configuration_restores_snapshot_and_preserves_publish_error(
    monkeypatch,
    tmp_path,
):
    resources_dir = tmp_path / "resources"
    lexicon_path, rubric_path, original_lexicon, original_rubric = _write_legacy_pair(resources_dir)
    monkeypatch.setattr(config_module, "RESOURCES_DIR", resources_dir)
    loader = ConfigLoader()
    loader.load()
    reload_snapshots = []

    def reload_config():
        restored = loader.reload()
        reload_snapshots.append((restored.lexicon, restored.rubric))
        raise RuntimeError("controlled recovery reload failure")

    real_atomic_write_text = adaptive_configuration_service.atomic_write_text
    active_config_path = resources_dir / "active_config.yaml"
    failed = False

    def ambiguous_atomic_write(path, payload):
        nonlocal failed
        real_atomic_write_text(path, payload)
        if path == active_config_path and "new-rubric" in payload and not failed:
            failed = True
            raise OSError("controlled failure after active snapshot publication")

    monkeypatch.setattr(
        adaptive_configuration_service,
        "atomic_write_text",
        ambiguous_atomic_write,
    )

    with pytest.raises(
        OSError,
        match="controlled failure after active snapshot publication",
    ) as exc_info:
        _apply_configuration(
            resources_dir,
            loader.load,
            reload_config,
        )

    assert not active_config_path.exists()
    assert lexicon_path.read_text(encoding="utf-8") == original_lexicon
    assert rubric_path.read_text(encoding="utf-8") == original_rubric
    assert reload_snapshots == [
        (
            {"generation": "old-lexicon"},
            {"generation": "old-rubric"},
        )
    ]
    assert any(
        "controlled recovery reload failure" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )


def test_apply_configuration_restores_previous_snapshot_after_reload_failure(
    monkeypatch,
    tmp_path,
):
    resources_dir = tmp_path / "resources"
    _write_legacy_pair(resources_dir)
    active_config_path = resources_dir / "active_config.yaml"
    previous_snapshot = yaml.safe_dump(
        {
            "generation": "previous",
            "lexicon": {"generation": "previous-lexicon"},
            "rubric": {"generation": "previous-rubric"},
        }
    )
    active_config_path.write_text(previous_snapshot, encoding="utf-8")
    monkeypatch.setattr(config_module, "RESOURCES_DIR", resources_dir)
    loader = ConfigLoader()
    reload_snapshots = []

    def fail_first_reload():
        config = loader.reload()
        reload_snapshots.append((config.lexicon, config.rubric))
        if len(reload_snapshots) == 1:
            raise RuntimeError("controlled reload failure after publication")
        return config

    with pytest.raises(
        RuntimeError,
        match="controlled reload failure after publication",
    ):
        _apply_configuration(
            resources_dir,
            loader.load,
            fail_first_reload,
        )

    assert active_config_path.read_text(encoding="utf-8") == previous_snapshot
    assert reload_snapshots == [
        (
            {"generation": "new-lexicon"},
            {"generation": "new-rubric"},
        ),
        (
            {"generation": "previous-lexicon"},
            {"generation": "previous-rubric"},
        ),
    ]


def test_apply_configuration_uses_unique_create_once_backup_generation(
    monkeypatch,
    tmp_path,
):
    resources_dir = tmp_path / "resources"
    _write_legacy_pair(resources_dir)
    monkeypatch.setattr(config_module, "RESOURCES_DIR", resources_dir)
    loader = ConfigLoader()

    first = _apply_configuration(
        resources_dir,
        loader.load,
        loader.reload,
    )
    first_backup = Path(str(first["backup_path"]))
    first_payload = first_backup.read_text(encoding="utf-8")
    second = _apply_configuration(
        resources_dir,
        loader.load,
        loader.reload,
    )
    second_backup = Path(str(second["backup_path"]))

    assert first_backup != second_backup
    assert first_backup.read_text(encoding="utf-8") == first_payload
    assert second_backup.exists()


def test_apply_configuration_does_not_mutate_cached_or_backup_generation(
    monkeypatch,
    tmp_path,
):
    resources_dir = tmp_path / "resources"
    _write_legacy_pair(resources_dir)
    (resources_dir / "active_config.yaml").write_text(
        yaml.safe_dump(
            {
                "generation": "old",
                "lexicon": {"nested": {"values": ["old"]}},
                "rubric": {"generation": "old-rubric"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "RESOURCES_DIR", resources_dir)
    loader = ConfigLoader()
    cached = loader.load()

    def mutate_nested_lexicon(lexicon, _patch):
        lexicon["nested"]["values"].append("new")
        return lexicon, ["nested-change"]

    result = _apply_configuration(
        resources_dir,
        loader.load,
        loader.reload,
        apply_lexicon_patch=mutate_nested_lexicon,
    )

    assert cached.lexicon == {"nested": {"values": ["old"]}}
    assert yaml.safe_load(Path(str(result["backup_path"])).read_text(encoding="utf-8")) == {
        "nested": {"values": ["old"]}
    }
