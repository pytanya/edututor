"""Общие фикстуры pytest: песочница для файловых артефактов тестов."""

import pytest

from src.config import settings


@pytest.fixture(autouse=True)
def _sandbox_wiki_dir(tmp_path, monkeypatch):
    """E4: хук wiki в чате пишет статьи на диск — уводим в tmp, не в репозиторий."""
    monkeypatch.setattr(settings, "knowledge_wiki_dir", str(tmp_path / "wiki"))
