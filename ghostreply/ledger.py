"""ledger.py: the possession ledger. A SHA-256 fingerprint of each run record, written as a Memo on Solana devnet.

    result = record_run(run)
    # {"status": "anchored"|"queued", "hash", "memo", "signature", "explorer_url", "reason"}

What goes on-chain is one string, "gitr1:<64 hex>". The hash covers the run's guard, model, message id, verdict,
oracle hits and timestamp, so anyone holding the run log can recompute it with run_hash(). No fixture text, names,
MRNs, DOBs or contacts ever leave the machine: the hits are hashed, never sent.

The demo never depends on the network. On ANY failure (no keypair, solana client missing, RPC offline, airdrop
refused, timeout) the fingerprint is appended to runs/ledger-queue.jsonl and the result says "queued". record_run
never raises. flush_queue() drains the queue later, and checks a recorded signature before it sends anything again.

Devnet only. The keypair (~/.config/solana/ghost-ledger-devnet.json) is created by Rafi, loaded here and nowhere
else, and never printed, logged or put in an error message.

Sources, confirmed 2026-10-30: the Memo program id and its UTF-8 / optional-signer rules from the Solana program
docs (solana-program.com/docs/memo, formerly spl.solana.com/memo); solana 0.41.0 + solders 0.29.0 from PyPI. In
solana 0.41 only the async client exists, so the sync functions here run it with asyncio.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
DEVNET_URL = "https://api.devnet.solana.com"
EXPLORER_TX = "https://explorer.solana.com/tx/{signature}?cluster=devnet"
KEYPAIR_PATH = Path("~/.config/solana/ghost-ledger-devnet.json")
QUEUE_PATH = Path("runs/ledger-queue.jsonl")
MEMO_PREFIX = "gitr1:"
SCHEMA = 1

MIN_LAMPORTS = 20_000  # one memo transaction costs 5,000
AIRDROP_LAMPORTS = 100_000_000  # 0.1 SOL of devnet play money
DEADLINE_S = 25.0  # record_run gives up and queues after this long
CONFIRM_TIMEOUT_S = 30.0


class LedgerUnavailable(Exception):
    """The ledger cannot run here (no keypair, no client). The message is fixed text, never file content."""


class Chain(Protocol):
    """What the ledger needs from Solana. SolanaChain is the real one; the tests pass a fake."""

    async def balance(self) -> int: ...
    async def airdrop(self, lamports: int) -> None: ...
    async def submit(self, memo: str) -> str: ...
    async def confirm(self, signature: str) -> bool: ...
    async def landed(self, signature: str) -> bool: ...
    async def close(self) -> None: ...


# ------------------------------------------------------------------------------------------ fingerprint

def _text(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _hits(value: object) -> list[str]:
    return [_text(v) for v in value] if isinstance(value, (list, tuple)) else []


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def run_record(run: dict) -> dict:
    """The fields that are hashed, and only these. Draft text, message text and transcripts are left out."""
    run = _dict(run)
    message, model, verdict = _dict(run.get("message")), _dict(run.get("model")), _dict(run.get("verdict"))
    return {
        "schema": SCHEMA,
        "guard": 1 if run.get("guard") else 0,
        "model": {"name": _text(model.get("name")), "digest": _text(model.get("digest"))},
        "message_id": _text(message.get("id")),
        "level": _text(verdict.get("level")),
        "leak": _hits(verdict.get("leak")),
        "phishing": _hits(verdict.get("phishing")),
        "timestamp": _text(run.get("started_at") or run.get("run_id")),
    }


def run_hash(run: dict) -> str:
    canonical = json.dumps(run_record(run), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def memo_for(digest: str) -> str:
    return MEMO_PREFIX + digest


def explorer_url(signature: str) -> str:
    return EXPLORER_TX.format(signature=signature)


# ------------------------------------------------------------------------------------------ the real chain

def build_memo_transaction(keypair, memo: str, blockhash):
    """A signed transaction holding one Memo instruction. The keypair signs as the memo's optional signer."""
    from solders.instruction import AccountMeta, Instruction
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction

    instruction = Instruction(
        Pubkey.from_string(MEMO_PROGRAM_ID), memo.encode("utf-8"), [AccountMeta(keypair.pubkey(), True, False)]
    )
    message = MessageV0.try_compile(keypair.pubkey(), [instruction], [], blockhash)
    return VersionedTransaction(message, [keypair])


class SolanaChain:
    """solana-py's AsyncClient pointed at devnet. There is no endpoint argument on purpose."""

    def __init__(self, keypair) -> None:
        from solana.rpc.async_api import AsyncClient

        self._keypair = keypair
        self._client = AsyncClient(DEVNET_URL, commitment="confirmed", timeout=15.0)
        self._last_valid: int | None = None

    async def balance(self) -> int:
        return (await self._client.get_balance(self._keypair.pubkey())).value

    async def airdrop(self, lamports: int) -> None:
        sent = await self._client.request_airdrop(self._keypair.pubkey(), lamports)
        await asyncio.wait_for(self._client.confirm_transaction(sent.value, commitment="confirmed"),
                               CONFIRM_TIMEOUT_S)

    async def submit(self, memo: str) -> str:
        latest = (await self._client.get_latest_blockhash()).value
        self._last_valid = latest.last_valid_block_height
        sent = await self._client.send_transaction(build_memo_transaction(self._keypair, memo, latest.blockhash))
        return str(sent.value)

    async def confirm(self, signature: str) -> bool:
        from solders.signature import Signature

        resp = await asyncio.wait_for(
            self._client.confirm_transaction(Signature.from_string(signature), commitment="confirmed",
                                             last_valid_block_height=self._last_valid),
            CONFIRM_TIMEOUT_S,
        )
        status = resp.value[0]
        return status is not None and status.err is None

    async def landed(self, signature: str) -> bool:
        from solders.signature import Signature

        resp = await self._client.get_signature_statuses([Signature.from_string(signature)],
                                                         search_transaction_history=True)
        status = resp.value[0]
        return status is not None and status.err is None

    async def close(self) -> None:
        await self._client.close()


def _open_chain(keypair_path: Path | str | None) -> Chain:
    path = Path(keypair_path or KEYPAIR_PATH).expanduser()
    if not path.is_file():
        raise LedgerUnavailable("keypair missing")
    try:
        from solders.keypair import Keypair

        keypair = Keypair.from_json(path.read_text())
    except ImportError:
        raise LedgerUnavailable("solana client not installed") from None
    except Exception:
        raise LedgerUnavailable("keypair unreadable") from None  # never echo what the file held
    return SolanaChain(keypair)


# --------------------------------------------------------------------------------------------- the queue

def _reason(exc: BaseException) -> str:
    if isinstance(exc, LedgerUnavailable):
        return str(exc)
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:200]


def _enqueue(queue_path: Path | str | None, entry: dict) -> None:
    path = Path(queue_path or QUEUE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _read_queue(queue_path: Path | str | None) -> list[dict]:
    path = Path(queue_path or QUEUE_PATH)
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("memo"), str):
            entries.append(entry)
    return entries


def _write_queue(queue_path: Path | str | None, entries: list[dict]) -> None:
    path = Path(queue_path or QUEUE_PATH)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(e, sort_keys=True, ensure_ascii=True) + "\n" for e in entries),
                   encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------------------------------------- anchoring

def _run(coro):
    """Run a coroutine from sync code, also when the caller already sits inside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _anchor(chain: Chain, memo: str, state: dict) -> str:
    try:
        if await chain.balance() < MIN_LAMPORTS:
            await chain.airdrop(AIRDROP_LAMPORTS)
        state["signature"] = await chain.submit(memo)
        if not await chain.confirm(state["signature"]):
            raise RuntimeError("transaction was not confirmed")
        return state["signature"]
    finally:
        await chain.close()


def _result(status: str, digest: str, signature: str | None, reason: str | None) -> dict:
    return {
        "status": status, "hash": digest, "memo": memo_for(digest), "signature": signature,
        "explorer_url": explorer_url(signature) if signature and status == "anchored" else None,
        "reason": reason,
    }


def record_run(run: dict, *, chain: Chain | None = None, keypair_path: Path | str | None = None,
               queue_path: Path | str | None = None, deadline: float = DEADLINE_S) -> dict:
    """Anchor this run's fingerprint on devnet, or queue it. Never raises."""
    try:
        digest = run_hash(run)
    except Exception:  # run_hash cannot fail on a dict, but the ledger must never end a demo
        digest = hashlib.sha256(b"unhashable run").hexdigest()
    memo = memo_for(digest)
    state: dict = {"signature": None}
    try:
        chain = chain if chain is not None else _open_chain(keypair_path)
        signature = _run(asyncio.wait_for(_anchor(chain, memo, state), deadline))
        return _result("anchored", digest, signature, None)
    except Exception as exc:
        reason = _reason(exc)
    entry = {
        "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": _text(run.get("run_id")) if isinstance(run, dict) else "",
        "hash": digest, "memo": memo, "signature": state["signature"], "reason": reason,
    }
    try:
        _enqueue(queue_path, entry)
    except Exception as exc:
        reason = f"{reason}; queue write failed: {type(exc).__name__}"
    return _result("queued", digest, state["signature"], reason)


async def _drain(chain: Chain, entries: list[dict], save) -> tuple[list[dict], int]:
    """Anchor entries in order, saving the queue after every step, so a timeout or a crash never leaves an
    anchored entry behind to be sent twice. Stops at the first failure and keeps it and everything after it."""
    remaining = list(entries)
    anchored = 0
    try:
        while remaining:
            entry = remaining[0]
            signature = entry.get("signature")
            if not (isinstance(signature, str) and signature and await chain.landed(signature)):
                if await chain.balance() < MIN_LAMPORTS:
                    await chain.airdrop(AIRDROP_LAMPORTS)
                signature = await chain.submit(entry["memo"])
                entry["signature"] = signature
                save(remaining)  # the signature is on disk before we wait to hear whether it landed
                if not await chain.confirm(signature):
                    raise RuntimeError("transaction was not confirmed")
            remaining.pop(0)
            anchored += 1
            save(remaining)
    except Exception as exc:
        if remaining:
            remaining[0]["reason"] = _reason(exc)
            save(remaining)
    finally:
        await chain.close()
    return remaining, anchored


def flush_queue(*, chain: Chain | None = None, keypair_path: Path | str | None = None,
                queue_path: Path | str | None = None, deadline: float = 120.0) -> dict:
    """Retry queued fingerprints. A recorded signature is checked first, so nothing is sent twice."""
    entries = _read_queue(queue_path)
    if not entries:
        return {"anchored": 0, "queued": 0, "reason": None}
    try:
        chain = chain if chain is not None else _open_chain(keypair_path)
        remaining, anchored = _run(asyncio.wait_for(
            _drain(chain, entries, lambda rows: _write_queue(queue_path, rows)), deadline))
    except Exception as exc:
        left = _read_queue(queue_path)  # a timeout leaves whatever progress was saved
        return {"anchored": len(entries) - len(left), "queued": len(left), "reason": _reason(exc)}
    return {"anchored": anchored, "queued": len(remaining), "reason": remaining[0].get("reason") if remaining else None}
