"""Tests for improved model picker details."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock
import json
import urllib.request


class TestFetchOllamaModels:
    def _mock_response(self, models_data):
        import io
        response_bytes = json.dumps({"models": models_data}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_bytes
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_extracts_parameter_size(self):
        from ui.picker import _fetch_ollama_models
        mock_data = [{"name": "llama3:8b", "size": 5_000_000_000, "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M"}}]
        with patch("urllib.request.urlopen", return_value=self._mock_response(mock_data)):
            models = _fetch_ollama_models("http://localhost:11434")
        assert models[0]["params"] == "8B"
        assert models[0]["quant"] == "Q4_K_M"

    def test_graceful_when_no_details(self):
        from ui.picker import _fetch_ollama_models
        mock_data = [{"name": "mymodel:latest", "size": 1_000_000_000}]
        with patch("urllib.request.urlopen", return_value=self._mock_response(mock_data)):
            models = _fetch_ollama_models("http://localhost:11434")
        assert models[0]["params"] == ""
        assert models[0]["quant"] == ""

    def test_model_id_format(self):
        from ui.picker import _fetch_ollama_models
        mock_data = [{"name": "qwen3:8b", "size": 0, "details": {}}]
        with patch("urllib.request.urlopen", return_value=self._mock_response(mock_data)):
            models = _fetch_ollama_models("http://localhost:11434")
        assert models[0]["id"] == "ollama/qwen3:8b"

    def test_empty_models_list(self):
        from ui.picker import _fetch_ollama_models
        with patch("urllib.request.urlopen", return_value=self._mock_response([])):
            models = _fetch_ollama_models("http://localhost:11434")
        assert models == []
