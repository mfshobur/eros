"""Tests for image input: encoding, message building, CLI extraction, clipboard cleanup."""
import struct
import sys
import os
import tempfile
import time
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from agent import _encode_image, _IMAGE_EXTS
from main import _extract_images, _resolve_clip_refs


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_png(path: Path) -> Path:
    """Write a minimal valid 1x1 red PNG to path."""
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(name, data):
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    iend = chunk(b"IEND", b"")
    path.write_bytes(sig + ihdr + idat + iend)
    return path


# ── _encode_image ─────────────────────────────────────────────────────────────

class TestEncodeImage:
    def test_returns_image_url_block(self, tmp_path):
        png = _make_png(tmp_path / "test.png")
        block = _encode_image(str(png))
        assert block is not None
        assert block["type"] == "image_url"
        assert block["image_url"]["url"].startswith("data:image/png;base64,")

    def test_missing_file_returns_none(self):
        assert _encode_image("/nonexistent/file.png") is None

    def test_jpeg_mime(self, tmp_path):
        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(b"fake")
        block = _encode_image(str(jpg))
        assert block["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ── _extract_images ───────────────────────────────────────────────────────────

class TestExtractImages:
    def test_extracts_image_ref(self, tmp_path):
        img = _make_png(tmp_path / "photo.png")
        text, images = _extract_images(f"describe @{img}")
        assert str(img) in images
        assert f"@{img}" not in text

    def test_non_image_ref_untouched(self, tmp_path):
        doc = tmp_path / "notes.txt"
        doc.write_text("hello")
        text, images = _extract_images(f"see @{doc}")
        assert images == []
        assert f"@{doc}" in text

    def test_no_refs(self):
        text, images = _extract_images("just a normal message")
        assert images == []
        assert text == "just a normal message"

    def test_mixed_refs(self, tmp_path):
        img = _make_png(tmp_path / "shot.png")
        doc = tmp_path / "notes.txt"
        doc.write_text("hi")
        text, images = _extract_images(f"look at @{img} and @{doc}")
        assert str(img) in images
        assert str(doc) not in images
        assert f"@{doc}" in text

    def test_image_exts_covered(self):
        assert ".png" in _IMAGE_EXTS
        assert ".jpg" in _IMAGE_EXTS
        assert ".jpeg" in _IMAGE_EXTS
        assert ".webp" in _IMAGE_EXTS


# ── _resolve_clip_refs ────────────────────────────────────────────────────────

class TestResolveClipRefs:
    def _register(self, path: str) -> str:
        from ui.input import _clip_images
        _clip_images.append(path)
        return f"[image #{len(_clip_images)}]"

    def test_resolves_placeholder(self, tmp_path):
        img = _make_png(tmp_path / "clip.png")
        placeholder = self._register(str(img))
        text, images = _resolve_clip_refs(f"what is this? {placeholder}")
        assert str(img) in images
        assert placeholder not in text

    def test_unknown_index_left_intact(self):
        text, images = _resolve_clip_refs("[image #9999]")
        assert images == []
        assert "[image #9999]" in text

    def test_temp_file_deleted_after_use(self, tmp_path):
        img = _make_png(tmp_path / "clip_del.png")
        placeholder = self._register(str(img))
        _, clip_images = _resolve_clip_refs(f"describe {placeholder}")
        assert img.exists()
        for p in clip_images:
            Path(p).unlink(missing_ok=True)
        assert not img.exists()


# ── _build_messages with images ───────────────────────────────────────────────

class TestBuildMessagesWithImages:
    def test_multipart_content_when_images_present(self, tmp_path):
        import yaml
        from agent import Agent
        with open("config.yaml") as f:
            config = yaml.safe_load(f)
        agent = Agent(config)
        img = _make_png(tmp_path / "px.png")
        messages = agent._build_messages("describe this", agent._get_tools(), images=[str(img)])
        last = messages[-1]
        assert last["role"] == "user"
        assert isinstance(last["content"], list)
        assert last["content"][0] == {"type": "text", "text": "describe this"}
        assert last["content"][1]["type"] == "image_url"

    def test_plain_content_without_images(self):
        import yaml
        from agent import Agent
        with open("config.yaml") as f:
            config = yaml.safe_load(f)
        agent = Agent(config)
        messages = agent._build_messages("hello", agent._get_tools())
        last = messages[-1]
        assert last["content"] == "hello"
