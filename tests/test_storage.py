"""Unit tests for app/storage.py"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest


class TestLoadJson:
    """Tests for load_json function"""

    def test_returns_default_when_file_not_exists(self, tmp_path: Path):
        from app.storage import load_json

        non_existent = tmp_path / "missing.json"
        result = load_json(non_existent, {"default": True})
        assert result == {"default": True}

    def test_returns_default_list_when_file_not_exists(self, tmp_path: Path):
        from app.storage import load_json

        non_existent = tmp_path / "missing.json"
        result = load_json(non_existent, [])
        assert result == []

    def test_loads_existing_json_file(self, tmp_path: Path):
        from app.storage import load_json

        test_file = tmp_path / "data.json"
        test_data = {"key": "value", "number": 42}
        test_file.write_text(json.dumps(test_data), encoding="utf-8")

        result = load_json(test_file, {})
        assert result == test_data

    def test_loads_json_array(self, tmp_path: Path):
        from app.storage import load_json

        test_file = tmp_path / "array.json"
        test_data = [{"id": 1}, {"id": 2}]
        test_file.write_text(json.dumps(test_data), encoding="utf-8")

        result = load_json(test_file, [])
        assert result == test_data

    def test_loads_chinese_content(self, tmp_path: Path):
        from app.storage import load_json

        test_file = tmp_path / "chinese.json"
        test_data = {"项目名称": "测试项目", "描述": "中文内容"}
        test_file.write_text(json.dumps(test_data, ensure_ascii=False), encoding="utf-8")

        result = load_json(test_file, {})
        assert result == test_data

    def test_raises_readable_error_when_json_is_corrupted(self, tmp_path: Path):
        from app.storage import StorageDataError, load_json

        test_file = tmp_path / "broken.json"
        test_file.write_text('{"broken": ', encoding="utf-8")

        with pytest.raises(StorageDataError) as exc_info:
            load_json(test_file, {})

        assert exc_info.value.code == "json_parse_failed"
        assert "broken.json" in exc_info.value.detail


class TestSaveJson:
    """Tests for save_json function"""

    def test_saves_dict_to_file(self, tmp_path: Path):
        from app.storage import save_json

        test_file = tmp_path / "output.json"
        test_data = {"key": "value"}

        save_json(test_file, test_data)

        assert test_file.exists()
        loaded = json.loads(test_file.read_text(encoding="utf-8"))
        assert loaded == test_data

    def test_saves_list_to_file(self, tmp_path: Path):
        from app.storage import save_json

        test_file = tmp_path / "list.json"
        test_data = [1, 2, 3]

        save_json(test_file, test_data)

        assert test_file.exists()
        loaded = json.loads(test_file.read_text(encoding="utf-8"))
        assert loaded == test_data

    def test_saves_chinese_without_escaping(self, tmp_path: Path):
        from app.storage import save_json

        test_file = tmp_path / "chinese_out.json"
        test_data = {"名称": "测试"}

        save_json(test_file, test_data)

        content = test_file.read_text(encoding="utf-8")
        assert "测试" in content  # Not escaped as \u6d4b\u8bd5

    def test_overwrites_existing_file(self, tmp_path: Path):
        from app.storage import save_json

        test_file = tmp_path / "overwrite.json"
        test_file.write_text('{"old": "data"}', encoding="utf-8")

        save_json(test_file, {"new": "data"})

        loaded = json.loads(test_file.read_text(encoding="utf-8"))
        assert loaded == {"new": "data"}

    def test_keep_history_creates_snapshots_and_allows_restore(self, tmp_path: Path):
        from app import storage

        test_file = tmp_path / "weights.json"
        versions_dir = tmp_path / "versions"

        with (
            mock.patch.object(storage, "DATA_DIR", tmp_path),
            mock.patch.object(storage, "MATERIALS_DIR", tmp_path / "materials"),
            mock.patch.object(storage, "VERSIONED_JSON_DIR", versions_dir),
        ):
            storage.save_json(test_file, {"version": 1}, keep_history=True)
            storage.save_json(test_file, {"version": 2}, keep_history=True)

            versions = storage.list_json_versions(test_file)
            assert len(versions) == 2

            older = versions[-1]
            restored = storage.restore_json_version(test_file, older["version_id"])

            assert restored["version_id"] == older["version_id"]
            assert storage.load_json(test_file, {}) == {"version": 1}


class TestSecureStorage:
    def test_save_json_encrypts_when_secure_mode_enabled(self, tmp_path: Path):
        from app import storage

        test_file = tmp_path / "secure.json"
        test_data = {"名称": "测试"}

        with (
            mock.patch.object(storage, "is_secure_desktop_mode_enabled", return_value=True),
            mock.patch.object(
                storage,
                "_dpapi_crypt",
                side_effect=lambda payload, decrypt=False: payload[::-1],
            ),
        ):
            storage.save_json(test_file, test_data)

            raw = test_file.read_bytes()
            assert raw.startswith(storage._SECURE_FILE_MAGIC)
            assert b"\xe6\xb5\x8b\xe8\xaf\x95" not in raw
            assert storage.load_json(test_file, {}) == test_data

    def test_prepare_secure_runtime_migrates_plaintext_files(self, tmp_path: Path):
        from app import storage

        legacy_file = tmp_path / "legacy.json"
        legacy_file.write_text('{"migrated": true}', encoding="utf-8")

        original_prepared = storage._SECURE_RUNTIME_PREPARED
        storage._SECURE_RUNTIME_PREPARED = False
        try:
            with (
                mock.patch.object(storage, "DATA_DIR", tmp_path),
                mock.patch.object(storage, "MATERIALS_DIR", tmp_path / "materials"),
                mock.patch.object(storage, "is_secure_desktop_mode_enabled", return_value=True),
                mock.patch.object(storage, "_require_windows_dpapi"),
                mock.patch.object(
                    storage, "_iter_secure_candidate_files", return_value=[legacy_file]
                ),
                mock.patch.object(
                    storage,
                    "_dpapi_crypt",
                    side_effect=lambda payload, decrypt=False: payload[::-1],
                ),
            ):
                storage.prepare_secure_runtime()
                raw = legacy_file.read_bytes()
                assert raw.startswith(storage._SECURE_FILE_MAGIC)
                assert storage.load_json(legacy_file, {}) == {"migrated": True}
        finally:
            storage._SECURE_RUNTIME_PREPARED = original_prepared


class TestEnsureDataDirs:
    """Tests for ensure_data_dirs function"""

    def test_creates_directories(self, tmp_path: Path):
        from app import storage

        # Temporarily patch the directory paths
        with mock.patch.object(storage, "DATA_DIR", tmp_path / "data"):
            with mock.patch.object(storage, "MATERIALS_DIR", tmp_path / "data" / "materials"):
                storage.ensure_data_dirs()
                assert (tmp_path / "data").exists()
                assert (tmp_path / "data" / "materials").exists()

    def test_idempotent_when_dirs_exist(self, tmp_path: Path):
        from app import storage

        data_dir = tmp_path / "data"
        materials_dir = data_dir / "materials"
        data_dir.mkdir()
        materials_dir.mkdir()

        with mock.patch.object(storage, "DATA_DIR", data_dir):
            with mock.patch.object(storage, "MATERIALS_DIR", materials_dir):
                # Should not raise
                storage.ensure_data_dirs()
                assert data_dir.exists()
                assert materials_dir.exists()


class TestLoadProjects:
    """Tests for load_projects function"""

    def test_returns_empty_list_when_no_file(self, tmp_path: Path):
        from app import storage

        with mock.patch.object(storage, "PROJECTS_PATH", tmp_path / "projects.json"):
            result = storage.load_projects()
            assert result == []

    def test_loads_existing_projects(self, tmp_path: Path):
        from app import storage

        projects_file = tmp_path / "projects.json"
        projects_data = [{"id": "p1", "name": "项目一"}]
        projects_file.write_text(json.dumps(projects_data, ensure_ascii=False), encoding="utf-8")

        with mock.patch.object(storage, "PROJECTS_PATH", projects_file):
            result = storage.load_projects()
            assert result == projects_data


class TestSaveProjects:
    """Tests for save_projects function"""

    def test_saves_projects_to_file(self, tmp_path: Path):
        from app import storage

        projects_file = tmp_path / "projects.json"
        projects_data = [{"id": "p1", "name": "Project 1"}]

        with mock.patch.object(storage, "PROJECTS_PATH", projects_file):
            storage.save_projects(projects_data)
            assert projects_file.exists()
            loaded = json.loads(projects_file.read_text(encoding="utf-8"))
            assert loaded == projects_data


class TestLoadSubmissions:
    """Tests for load_submissions function"""

    def test_returns_empty_list_when_no_file(self, tmp_path: Path):
        from app import storage

        with mock.patch.object(storage, "SUBMISSIONS_PATH", tmp_path / "subs.json"):
            result = storage.load_submissions()
            assert result == []

    def test_loads_existing_submissions(self, tmp_path: Path):
        from app import storage

        subs_file = tmp_path / "submissions.json"
        subs_data = [{"id": "s1", "score": 85.5}]
        subs_file.write_text(json.dumps(subs_data), encoding="utf-8")

        with mock.patch.object(storage, "SUBMISSIONS_PATH", subs_file):
            result = storage.load_submissions()
            assert result == subs_data


class TestSaveSubmissions:
    """Tests for save_submissions function"""

    def test_saves_submissions_to_file(self, tmp_path: Path):
        from app import storage

        subs_file = tmp_path / "submissions.json"
        subs_data = [{"id": "s1", "score": 90}]

        with mock.patch.object(storage, "SUBMISSIONS_PATH", subs_file):
            storage.save_submissions(subs_data)
            loaded = json.loads(subs_file.read_text(encoding="utf-8"))
            assert loaded == subs_data


class TestLoadMaterials:
    """Tests for load_materials function"""

    def test_returns_empty_list_when_no_file(self, tmp_path: Path):
        from app import storage

        with mock.patch.object(storage, "MATERIALS_PATH", tmp_path / "mats.json"):
            result = storage.load_materials()
            assert result == []

    def test_loads_existing_materials(self, tmp_path: Path):
        from app import storage

        mats_file = tmp_path / "materials.json"
        mats_data = [{"id": "m1", "filename": "doc.pdf"}]
        mats_file.write_text(json.dumps(mats_data), encoding="utf-8")

        with mock.patch.object(storage, "MATERIALS_PATH", mats_file):
            result = storage.load_materials()
            assert result == mats_data


class TestSaveMaterials:
    """Tests for save_materials function"""

    def test_saves_materials_to_file(self, tmp_path: Path):
        from app import storage

        mats_file = tmp_path / "materials.json"
        mats_data = [{"id": "m1", "filename": "plan.docx"}]

        with mock.patch.object(storage, "MATERIALS_PATH", mats_file):
            storage.save_materials(mats_data)
            loaded = json.loads(mats_file.read_text(encoding="utf-8"))
            assert loaded == mats_data


class TestLoadLearningProfiles:
    """Tests for load_learning_profiles function"""

    def test_returns_empty_list_when_no_file(self, tmp_path: Path):
        from app import storage

        with mock.patch.object(storage, "LEARNING_PATH", tmp_path / "learn.json"):
            result = storage.load_learning_profiles()
            assert result == []

    def test_loads_existing_profiles(self, tmp_path: Path):
        from app import storage

        learn_file = tmp_path / "learning_profiles.json"
        learn_data = [{"project_id": "p1", "multipliers": {}}]
        learn_file.write_text(json.dumps(learn_data), encoding="utf-8")

        with mock.patch.object(storage, "LEARNING_PATH", learn_file):
            result = storage.load_learning_profiles()
            assert result == learn_data


class TestSaveLearningProfiles:
    """Tests for save_learning_profiles function"""

    def test_saves_profiles_to_file(self, tmp_path: Path):
        from app import storage

        learn_file = tmp_path / "learning_profiles.json"
        learn_data = [{"project_id": "p1", "dimension_multipliers": {"d1": 1.2}}]

        with mock.patch.object(storage, "LEARNING_PATH", learn_file):
            storage.save_learning_profiles(learn_data)
            loaded = json.loads(learn_file.read_text(encoding="utf-8"))
            assert loaded == learn_data


class TestIntegration:
    """Integration tests for storage module"""

    def test_roundtrip_save_and_load(self, tmp_path: Path):
        """Test that data saved can be loaded back correctly"""
        from app import storage

        projects_file = tmp_path / "projects.json"
        original_data = [
            {"id": "p1", "name": "测试项目", "meta": {"region": "北京"}},
            {"id": "p2", "name": "Another", "meta": None},
        ]

        with mock.patch.object(storage, "PROJECTS_PATH", projects_file):
            storage.save_projects(original_data)
            loaded_data = storage.load_projects()
            assert loaded_data == original_data

    def test_multiple_storage_types_independent(self, tmp_path: Path):
        """Test that different storage types don't interfere"""
        from app import storage

        with (
            mock.patch.object(storage, "PROJECTS_PATH", tmp_path / "p.json"),
            mock.patch.object(storage, "SUBMISSIONS_PATH", tmp_path / "s.json"),
            mock.patch.object(storage, "MATERIALS_PATH", tmp_path / "m.json"),
        ):
            storage.save_projects([{"type": "project"}])
            storage.save_submissions([{"type": "submission"}])
            storage.save_materials([{"type": "material"}])

            assert storage.load_projects() == [{"type": "project"}]
            assert storage.load_submissions() == [{"type": "submission"}]
            assert storage.load_materials() == [{"type": "material"}]
