"""Unit tests for app/storage.py"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest import mock


class TestDataDirEnv:
    """Tests for QINGTIAN_DATA_DIR startup-time data root override."""

    def test_uses_repo_data_dir_when_env_unset(self, monkeypatch):
        from app import storage

        monkeypatch.delenv("QINGTIAN_DATA_DIR", raising=False)
        try:
            reloaded = importlib.reload(storage)

            assert reloaded.DATA_DIR == reloaded.BASE_DIR / "data"
            assert reloaded.SUBMISSIONS_PATH == reloaded.DATA_DIR / "submissions.json"
        finally:
            monkeypatch.delenv("QINGTIAN_DATA_DIR", raising=False)
            importlib.reload(storage)

    def test_uses_external_data_dir_when_env_set(self, monkeypatch, tmp_path: Path):
        from app import storage

        external_data = tmp_path / "external-data"
        monkeypatch.setenv("QINGTIAN_DATA_DIR", str(external_data))
        try:
            reloaded = importlib.reload(storage)

            assert reloaded.DATA_DIR == external_data.resolve()
            assert reloaded.PROJECTS_PATH == external_data.resolve() / "projects.json"
            assert reloaded.SUBMISSIONS_PATH == external_data.resolve() / "submissions.json"
            assert reloaded.SCORE_REPORTS_PATH == external_data.resolve() / "score_reports.json"
            assert reloaded.MATERIALS_DIR == external_data.resolve() / "materials"
        finally:
            monkeypatch.delenv("QINGTIAN_DATA_DIR", raising=False)
            importlib.reload(storage)

    def test_load_submissions_reads_external_data_root(self, monkeypatch, tmp_path: Path):
        from app import storage

        external_data = tmp_path / "external-data"
        external_data.mkdir()
        submissions = [{"id": "s1", "project_id": "p1"}]
        (external_data / "submissions.json").write_text(json.dumps(submissions), encoding="utf-8")
        monkeypatch.setenv("QINGTIAN_DATA_DIR", str(external_data))
        try:
            reloaded = importlib.reload(storage)

            assert reloaded.load_submissions() == submissions
        finally:
            monkeypatch.delenv("QINGTIAN_DATA_DIR", raising=False)
            importlib.reload(storage)

    def test_load_submissions_missing_external_file_returns_empty(
        self, monkeypatch, tmp_path: Path
    ):
        from app import storage

        external_data = tmp_path / "external-data"
        external_data.mkdir()
        monkeypatch.setenv("QINGTIAN_DATA_DIR", str(external_data))
        try:
            reloaded = importlib.reload(storage)

            assert reloaded.load_submissions() == []
        finally:
            monkeypatch.delenv("QINGTIAN_DATA_DIR", raising=False)
            importlib.reload(storage)


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
