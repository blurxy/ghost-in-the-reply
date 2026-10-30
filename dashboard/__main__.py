"""python -m dashboard [--replay-only] [--host IP] [--port N]

Serves the audience view at / and the presenter view at /presenter. The default host is 127.0.0.1, which keeps it
off the venue Wi-Fi; pass --host <tailnet IP> to open it from the Mac.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys

from ghostreply.harness import DEFAULT_HOST, DEFAULT_MODEL, ROOT

from .live import TIMEOUTS, LiveRunner
from .preflight import run_checks
from .server import make_server
from .state import DashboardState

DEMO_DIR = ROOT / "runs" / "demo"
BACKUP = ROOT / "dashboard" / "backup.mp4"


def build_live_runner(model: str, host: str, timeouts: dict[int, float] | None = None, variant: dict | None = None):
    """Real runs of msg-004 on the model (or of a `--random` variant's own message), saved to runs/ (never
    runs/demo/ or runs/pool/). Imported late so --replay-only never touches the model code path. The model is warmed
    with a 1-token call first, so the GUARD=0 timing measures the run, not the model loading into the GPU."""
    from ghostreply.harness import OllamaModel, load_inbox, load_patients, run_message, save_run

    from .preflight import warm_up

    def run_fn(guard: int) -> dict:
        if variant is None:
            return run_message("msg-004", bool(guard), OllamaModel(model, host))
        from ghostreply import variants  # the lead's module; only `--random` needs it

        message, extra = variants.load(variant["id"])
        return run_message(message["id"], bool(guard), OllamaModel(model, host),
                           inbox={**load_inbox(), message["id"]: message}, patients={**load_patients(), **extra})

    # the warm-up is `prepare`: it is not counted against GUARD=0's 40 s, and it has a limit of its own
    return LiveRunner(run_fn, timeouts=timeouts, save=save_run, prepare=lambda: warm_up(model, host))


def port_in_use(host: str, port: int) -> bool:
    """True if something already answers on this port (any interface a client could reach it by)."""
    targets = {"127.0.0.1" if host in ("", "0.0.0.0", "::") else host, "127.0.0.1"}
    for target in targets:
        try:
            with socket.create_connection((target, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dashboard", description="The Haunted Inbox demo dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1 only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--stage", action="store_true",
                        help="enable the presenter's stage buttons (projector switch, fixed terminal commands); "
                             "loopback only, never on a shared or tunnelled instance")
    parser.add_argument("--replay-only", action="store_true", help="never call Ollama; every badge says REPLAY")
    parser.add_argument("--random", action="store_true",
                        help="a variant picked at random from the tested pool (runs/pool/); R re-rolls it")
    parser.add_argument("--practice", action="store_true",
                        help="hands-free rehearsal at speaking pace, then judge questions; replay only")
    parser.add_argument("--model", default=os.environ.get("GHOST_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", DEFAULT_HOST))
    parser.add_argument("--timeout0", type=float, default=TIMEOUTS[0], help="seconds before a live GUARD=0 run falls back")
    parser.add_argument("--timeout1", type=float, default=TIMEOUTS[1], help="seconds before a live GUARD=1 run falls back")
    args = parser.parse_args(argv)

    if port_in_use(args.host, args.port):
        print(f"port {args.port} is in use; try --port {args.port + 34}", file=sys.stderr)
        return 2

    replay_only = args.replay_only or args.practice  # a rehearsal never touches the model
    timeouts = {0: args.timeout0, 1: args.timeout1}
    live, factory = None, None
    if not replay_only and args.random:  # a runner per variant: R re-rolls, and the live runs follow the variant
        factory = lambda variant: build_live_runner(args.model, args.ollama_host, timeouts, variant)  # noqa: E731
    elif not replay_only:
        live = build_live_runner(args.model, args.ollama_host, timeouts)
    state = DashboardState(
        DEMO_DIR, live=live, live_factory=factory, mode="replay" if replay_only else "live", backup=BACKUP,
        preflight=lambda: run_checks(DEMO_DIR, replay_only=replay_only, model=args.model,
                                     host=args.ollama_host, cwd=ROOT),
        random_mode=args.random, practice=args.practice,
    )
    if args.stage and args.host not in ("127.0.0.1", "localhost", "::1"):
        print("--stage only works on a loopback host (127.0.0.1); refusing to expose stage actions", file=sys.stderr)
        return 2
    state.stage_enabled = bool(args.stage)
    from .chatportal import ChatSession  # /chat: a typed message needs a model; the two suggested ones replay without

    if replay_only:
        state.chat = ChatSession(None)
    else:
        from ghostreply.harness import OllamaModel as ChatModel

        state.chat = ChatSession(lambda: ChatModel(args.model, args.ollama_host))
    if not replay_only:  # /sandbox runs a stranger's text on the real model: only when there is one
        from ghostreply.harness import OllamaModel

        from .sandbox import Sandbox

        state.sandbox = Sandbox(lambda: OllamaModel(args.model, args.ollama_host))
    try:
        server = make_server(state, args.host, args.port)
    except OSError as exc:
        print(f"cannot listen on {args.host}:{args.port} ({exc.strerror or type(exc).__name__}); "
              f"try --port {args.port + 34}", file=sys.stderr)
        return 2
    shown = args.host if args.host not in ("0.0.0.0", "") else "127.0.0.1"
    mode = "replay-only" if replay_only else "live with replay fallback"
    print(f"Haunted Inbox dashboard ({mode})")
    if args.practice:
        print("  practice: hands-free, each step lasts as long as its line takes to say; three judge questions at the end")
    if args.random:
        if state.fallback:
            print(f"  random: fewer than 3 tested variants are kept ({state.pool_note or 'none listed'}); "
                  f"showing the standard attack, and the screen says so", file=sys.stderr)
        else:
            v = state.variant
            print(f"  random: variant {v['id']} ({v['decoy']}, {v['phone']}, hidden {v['hiding']}) "
                  f"of {len(state.pool)} tested; R on the presenter re-rolls")
    print(f"  audience   http://{shown}:{args.port}/")
    print(f"  presenter  http://{shown}:{args.port}/presenter")
    print(f"  autoplay   http://{shown}:{args.port}/?autoplay=1&step_ms=6000")
    print(f"  chat       http://{shown}:{args.port}/chat")
    print(f"  sandbox    http://{shown}:{args.port}/sandbox" + ("   (needs a model: not in --replay-only)" if replay_only else ""))
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
