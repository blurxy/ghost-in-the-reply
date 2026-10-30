"""python -m dashboard.export_static [--out docs]

Freezes the audience view, in replay mode, into a static site for GitHub Pages. No server, no model, no network.

    docs/index.html   a 16:9 frame that scales to fit any screen (projector, laptop, phone); tap, click or keys step
                      through; a Play button runs the fixed-timing autoplay
    docs/stage.html   dashboard/page.html, unchanged, plus a small shim that answers the page's /api calls from data
                      baked in at export time (the same JSON the server's /api/state and /api/replay return)
    docs/.nojekyll    serve the files as they are

Every result on the site is a saved real run from runs/demo/, and every badge says REPLAY: the export refuses to
write anything if a saved run is missing or any badge would say otherwise. Re-run it after any change to
dashboard/steps.py, dashboard/page.html or runs/demo/, then commit docs/.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from ghostreply.harness import ROOT

from .state import DashboardState
from .steps import STEPS

DEMO_DIR = ROOT / "runs" / "demo"
PAGE = Path(__file__).with_name("page.html")
W, H = 1920, 1080  # the stage is laid out exactly as on the projector, then scaled as a whole
MANUAL_MS = 86_400_000  # the page's autoplay timer, pushed out a day: the visitor steps through by hand
PLAY_MS = 7_000

KEYS = ["ArrowRight", "ArrowLeft", " ", "PageDown", "PageUp", "r", "R"]


class ExportError(RuntimeError):
    pass


def _js(value: object) -> str:
    """JSON that is safe inside a <script> element."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def collect(replay_dir: Path = DEMO_DIR) -> tuple[dict, dict]:
    """The two payloads the page asks for, built by the dashboard's own code in replay-only mode."""
    state = DashboardState(replay_dir, live=None, mode="replay", preflight=None)
    if state.replay_error:
        raise ExportError(f"saved run missing or unreadable: {state.replay_error}")
    snap = state.snapshot()
    views = state.views(force_replay=True)
    runs = views.get("runs") or {}
    for guard in ("0", "1"):
        b = ((runs.get(guard) or {}).get("badge") or {})
        if b.get("source") != "replay" or not str(b.get("text", "")).startswith("REPLAY"):
            raise ExportError(f"GUARD={guard} badge is not an honest REPLAY badge: {b!r}")
    snap["replay_only"] = True
    snap["mode"] = "replay"
    snap["preflight"] = []
    snap["backup"] = False
    return snap, views


SHIM = """<script>
/* Static export (dashboard/export_static.py): no server here. Answer the page's /api calls from data baked in at
   export time, run the page's own local replay mode, and take keys from the frame around it (index.html). */
(function () {
  "use strict";
  if (!/[?&]autoplay=1(&|$)/.test(location.search)) {
    location.replace(location.pathname + "?autoplay=1&step_ms=__MANUAL__");
    return;
  }
  var STATE = __STATE__;
  var REPLAY = __REPLAY__;
  var json = function (o) {
    return Promise.resolve(new Response(JSON.stringify(o), { headers: { "Content-Type": "application/json" } }));
  };
  var real = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (url, opts) {
    var p = String(url).split("?")[0];
    if (/\\/api\\/state$/.test(p)) { var s = JSON.parse(JSON.stringify(STATE)); s.server_time = Date.now() / 1000; return json(s); }
    if (/\\/api\\/(replay|views)$/.test(p)) return json(REPLAY);
    if (/\\/api\\/action$/.test(p)) return json({});
    return real ? real(url, opts) : Promise.reject(new Error("offline"));
  };
  var KEYS = __KEYS__;
  window.addEventListener("message", function (e) {
    if (e.source !== window.parent || !e.data || typeof e.data.ghostKey !== "string") return;
    if (KEYS.indexOf(e.data.ghostKey) < 0) return;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: e.data.ghostKey, bubbles: true }));
  });
})();
</script>
"""


def stage_html(page: str, state: dict, replay: dict) -> str:
    if "<head>" not in page:
        raise ExportError("page.html has no <head>; the shim must load before the page's script")
    shim = (SHIM.replace("__STATE__", _js(state)).replace("__REPLAY__", _js(replay))
                .replace("__KEYS__", _js(KEYS)).replace("__MANUAL__", str(MANUAL_MS)))
    return page.replace("<head>", "<head>\n" + shim, 1)


def index_html(title: str, description: str) -> str:
    t, d = html.escape(title), html.escape(description)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{t}</title>
<meta name="description" content="{d}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:type" content="website">
<style>
html,body{{margin:0;height:100%;background:#0b0b10;color:#a0a0b4;overflow:hidden;font:15px/1.3 system-ui,"Segoe UI","Helvetica Neue",Arial,sans-serif}}
#wrap{{position:fixed;left:0;right:0;top:0;bottom:var(--bar,48px);display:flex;align-items:center;justify-content:center;overflow:hidden}}
#box{{position:relative;flex:none;width:{W}px;height:{H}px;transform-origin:center center}}
#box iframe{{position:absolute;inset:0;width:{W}px;height:{H}px;border:0;display:block;background:#0b0b10}}
#tap{{position:absolute;inset:0;cursor:pointer;-webkit-tap-highlight-color:transparent}}
#bar{{position:fixed;left:0;right:0;bottom:0;display:flex;gap:10px;align-items:center;justify-content:center;flex-wrap:wrap;
      padding:8px 12px calc(8px + env(safe-area-inset-bottom));background:#0b0b10;border-top:1px solid #2a2a3a}}
#bar button{{font:inherit;color:#f2f2f7;background:#12121a;border:1px solid #2a2a3a;border-radius:8px;padding:6px 14px;cursor:pointer}}
#bar button:focus-visible{{outline:2px solid #ff3df0}}
#hint{{text-align:center}}
#rot{{display:none;position:fixed;top:8px;left:0;right:0;text-align:center;color:#ffd600}}
@media (orientation:portrait) and (max-width:700px){{#rot{{display:block}}}}
@media (max-height:500px){{#hint{{display:none}} #bar{{padding:4px 10px calc(4px + env(safe-area-inset-bottom))}}}}
</style>
</head>
<body>
<div id="wrap"><div id="box">
  <iframe id="stage" title="{t}: the demo, replayed" src="stage.html?autoplay=1&amp;step_ms={MANUAL_MS}"></iframe>
  <div id="tap" aria-hidden="true"></div>
</div></div>
<div id="rot">Turn your phone sideways for a bigger view.</div>
<div id="bar">
  <button data-k="ArrowLeft" aria-label="Back">&larr; Back</button>
  <span id="hint">Tap or press &rarr; to step through. Replays of real Gemma 4 runs.</span>
  <button data-k="ArrowRight" aria-label="Next">Next &rarr;</button>
  <button id="play">&#9654; Play</button>
</div>
<script>
"use strict";
const frame = document.getElementById("stage"), box = document.getElementById("box"), bar = document.getElementById("bar");
const MANUAL = "stage.html?autoplay=1&step_ms={MANUAL_MS}", PLAY = "stage.html?autoplay=1&step_ms={PLAY_MS}";
function fit() {{
  const b = bar.getBoundingClientRect().height;
  document.documentElement.style.setProperty("--bar", b + "px");
  const s = Math.min(innerWidth / {W}, (innerHeight - b) / {H});
  box.style.transform = "scale(" + s + ")";
}}
addEventListener("resize", fit); addEventListener("orientationchange", fit); fit();
const send = (k) => frame.contentWindow && frame.contentWindow.postMessage({{ ghostKey: k }}, "*");
document.getElementById("tap").addEventListener("click", (e) => {{
  const r = box.getBoundingClientRect();
  send(e.clientX - r.left < r.width * 0.3 ? "ArrowLeft" : "ArrowRight");
}});
bar.querySelectorAll("button[data-k]").forEach((b) => b.addEventListener("click", () => send(b.dataset.k)));
const play = document.getElementById("play");
play.addEventListener("click", () => {{
  const playing = play.dataset.on === "1";
  play.dataset.on = playing ? "" : "1";
  play.innerHTML = playing ? "&#9654; Play" : "&#9632; Stop";
  frame.src = playing ? MANUAL : PLAY;
}});
addEventListener("keydown", (e) => {{
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "f" || e.key === "F") {{
    document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {{}});
  }} else if ({_js(KEYS)}.includes(e.key)) {{
    send(e.key);
  }} else return;
  e.preventDefault();
}});
</script>
</body>
</html>
"""


def export(out: Path, replay_dir: Path = DEMO_DIR, page: Path = PAGE) -> list[Path]:
    state, replay = collect(replay_dir)
    title = str(STEPS[0].get("headline") or "Demo")
    description = str(STEPS[0].get("sub") or "")
    files = {
        out / "stage.html": stage_html(page.read_text(encoding="utf-8"), state, replay),
        out / "index.html": index_html(title, description),
        out / ".nojekyll": "",
    }
    out.mkdir(parents=True, exist_ok=True)
    # Write every file next to its target first, then rename them all: a failure while writing leaves the old site
    # exactly as it was (no half-updated mix of old stage.html and new index.html) and no .tmp files behind.
    temps = {path: path.with_name(path.name + ".tmp") for path in files}
    try:
        for path, text in files.items():
            temps[path].write_text(text, encoding="utf-8")
        for path, tmp in temps.items():
            tmp.replace(path)
    finally:
        for tmp in temps.values():
            tmp.unlink(missing_ok=True)
    return list(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dashboard.export_static", description=" ".join((__doc__ or "").splitlines()[2:3]))
    parser.add_argument("--out", default=str(ROOT / "docs"), help="output folder (default: docs/)")
    args = parser.parse_args(argv)
    try:
        written = export(Path(args.out))
    except ExportError as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
