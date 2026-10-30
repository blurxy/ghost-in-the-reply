"""python -m dashboard [--replay-only] [--host IP] [--port N]

Serves the audience view at / and the presenter view at /presenter. The default host is 127.0.0.1, which keeps it
off the venue Wi-Fi; pass --host <tailnet IP> to open it from the Mac.
"""

from __future__ import annotations

import argparse
import os
import sys

from ghostreply.harness import DEFAULT_HOST, DEFAULT_MODEL, ROOT

from .preflight import run_checks
from .server import make_server
from .state import DashboardState

DEMO_DIR = ROOT / "runs" / "demo"
BACKUP = ROOT / "dashboard" / "backup.mp4"


def build_live_runner(model: str, host: str):
    """Real runs of msg-004 on the model, saved to runs/ (never runs/demo/). Imported late so --replay-only
    never touches the model code path."""
    from ghostreply.harness import OllamaModel, run_message, save_run

    from .live import LiveRunner

    def run_fn(guard: int) -> dict:
        return run_message("msg-004", bool(guard), OllamaModel(model, host))

    return LiveRunner(run_fn, save=save_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dashboard", description="The Ghost in the Reply demo dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1 only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--replay-only", action="store_true", help="never call Ollama; every badge says REPLAY")
    parser.add_argument("--model", default=os.environ.get("GHOST_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", DEFAULT_HOST))
    args = parser.parse_args(argv)

    live = None if args.replay_only else build_live_runner(args.model, args.ollama_host)
    state = DashboardState(
        DEMO_DIR, live=live, mode="replay" if args.replay_only else "live", backup=BACKUP,
        preflight=lambda: run_checks(DEMO_DIR, replay_only=args.replay_only, model=args.model,
                                     host=args.ollama_host, cwd=ROOT),
    )
    server = make_server(state, args.host, args.port)
    shown = args.host if args.host not in ("0.0.0.0", "") else "127.0.0.1"
    mode = "replay-only" if args.replay_only else "live with replay fallback"
    print(f"Ghost in the Reply dashboard ({mode})")
    print(f"  audience   http://{shown}:{args.port}/")
    print(f"  presenter  http://{shown}:{args.port}/presenter")
    print(f"  autoplay   http://{shown}:{args.port}/?autoplay=1&step_ms=6000")
    if state.replay_error:
        print(f"  WARNING: saved runs not loaded ({state.replay_error})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
