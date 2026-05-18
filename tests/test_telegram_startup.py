"""Tests for telegram startup prompt suppression."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture
def declined_file(tmp_path, monkeypatch):
    import main
    marker = tmp_path / ".telegram_declined"
    monkeypatch.setattr(main, "_TELEGRAM_DECLINED_FILE", marker)
    return marker


def _fake_telegram_mod(setup_return: bool):
    m = MagicMock()
    m.setup_interactive.return_value = setup_return
    return m


class TestStartTelegram:
    def test_no_prompt_when_declined_file_exists(self, declined_file):
        declined_file.touch()
        tg = _fake_telegram_mod(False)
        with patch.dict("sys.modules", {"telegram_bot": tg}):
            import main
            main._start_telegram({"telegram": {"token": ""}})
        tg.setup_interactive.assert_not_called()

    def test_prompt_when_no_declined_file(self, declined_file):
        tg = _fake_telegram_mod(False)
        with patch.dict("sys.modules", {"telegram_bot": tg}):
            import main
            main._start_telegram({"telegram": {"token": ""}})
        tg.setup_interactive.assert_called_once()

    def test_declined_file_created_after_user_says_no(self, declined_file):
        tg = _fake_telegram_mod(False)
        with patch.dict("sys.modules", {"telegram_bot": tg}):
            import main
            main._start_telegram({"telegram": {"token": ""}})
        assert declined_file.exists()

    def test_declined_file_not_created_when_user_configures(self, declined_file):
        tg = _fake_telegram_mod(True)
        with patch.dict("sys.modules", {"telegram_bot": tg}):
            import main
            main._start_telegram({"telegram": {"token": ""}})
        assert not declined_file.exists()

    def test_token_present_starts_bot_without_prompt(self, declined_file):
        tg = _fake_telegram_mod(False)
        with patch.dict("sys.modules", {"telegram_bot": tg}):
            import main
            main._start_telegram({"telegram": {"token": "abc123"}})
        tg.setup_interactive.assert_not_called()
        tg.run_in_thread.assert_called_once()
