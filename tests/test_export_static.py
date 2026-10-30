"""The static GitHub Pages export: honest REPLAY badges only, no server needed, refuses to publish a broken site."""

import json
import re
import shutil

import pytest

from dashboard import export_static as ex
from dashboard.steps import STEPS


def test_export_writes_the_site(tmp_path):
    written = ex.export(tmp_path / "docs")
    names = sorted(p.name for p in written)
    assert names == [".nojekyll", "index.html", "stage.html"]
    index = (tmp_path / "docs" / "index.html").read_text(encoding="utf-8")
    stage = (tmp_path / "docs" / "stage.html").read_text(encoding="utf-8")
    assert f"<title>{STEPS[0]['headline']}</title>" in index
    assert 'src="stage.html?autoplay=1&amp;step_ms=' in index
    assert stage.count("Static export (dashboard/export_static.py)") == 1  # the shim, once, before the page script
    assert stage.index("Static export") < stage.index("const AUTOPLAY")
    assert not list((tmp_path / "docs").glob("*.tmp"))


def test_every_badge_is_replay_and_nothing_says_live():
    state, replay = ex.collect()
    assert state["replay_only"] is True and state["mode"] == "replay" and state["preflight"] == []
    for guard in ("0", "1"):
        badge = replay["runs"][guard]["badge"]
        assert badge["source"] == "replay" and badge["text"].startswith("REPLAY")
    stage = ex.stage_html(ex.PAGE.read_text(encoding="utf-8"), state, replay)
    assert '"source":"live"' not in stage


def test_baked_json_cannot_close_the_script_tag():
    nasty = {"text": "</script><script>alert(1)</script> &  "}
    out = ex._js(nasty)
    assert "<" not in out and ">" not in out and "&" not in out and " " not in out
    assert json.loads(out) == nasty


def test_shim_keys_are_an_allowlist():
    stage = ex.stage_html("<html><head></head><body></body></html>", {"n": 1}, {"runs": {}})
    keys = json.loads(re.search(r"var KEYS = (\[.*?\]);", stage).group(1))
    assert "ArrowRight" in keys and "ArrowLeft" in keys
    assert "b" not in keys and "B" not in keys and "l" not in keys  # no backup video, no live toggle on the site


def test_missing_saved_run_refuses_to_export(tmp_path):
    demo = tmp_path / "demo"
    shutil.copytree(ex.DEMO_DIR, demo)
    (demo / "msg-004-guard0.json").unlink()
    with pytest.raises(ex.ExportError):
        ex.collect(demo)
    assert ex.main(["--out", str(tmp_path / "docs")]) == 0  # the real runs/demo still exports


def test_page_without_head_is_refused():
    with pytest.raises(ex.ExportError):
        ex.stage_html("<html><body></body></html>", {}, {})


def test_the_site_is_self_contained_and_carries_nothing_local(tmp_path):
    ex.export(tmp_path / "docs")
    for name in ("index.html", "stage.html"):
        text = (tmp_path / "docs" / name).read_text(encoding="utf-8")
        assert not re.search(r"<script[^>]+src=|<link\b|@import|src=[\"']https?:|url\(\s*[\"']?https?:", text), name
        for local in ("/home/", "localhost", "127.0.0.1", "11434", "Ghost in the Reply"):
            assert local not in text, (name, local)
    index = (tmp_path / "docs" / "index.html").read_text(encoding="utf-8")
    assert "Replays of real Gemma 4 runs" in index  # the frame around the page says what the page is


def test_a_failed_write_leaves_the_old_site_untouched(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    ex.export(docs)
    before = {p.name: p.read_bytes() for p in docs.iterdir()}
    calls = []
    real = type(docs).write_text

    def flaky(self, text, *args, **kwargs):
        calls.append(self.name)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(self, text, *args, **kwargs)

    monkeypatch.setattr(type(docs), "write_text", flaky)
    monkeypatch.setattr(ex, "stage_html", lambda *a, **k: "<!-- changed -->")
    with pytest.raises(OSError):
        ex.export(docs)
    assert {p.name: p.read_bytes() for p in docs.iterdir()} == before  # same files, same bytes, no .tmp left
