"""Incremental Lidl Plus backup."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lidlplus import LidlPlusApi

from lidlplus.archive.site import write_site

log = logging.getLogger(__name__)

STATE_FILE = "state.json"
ARCHIVE_RECEIPTS = "archive/receipts"
ARCHIVE_COUPONS = "archive/coupons"


def default_data_dir() -> Path:
    return Path(os.environ.get("LIDL_DATA_DIR", "data"))


def load_state(data_dir: Path) -> dict[str, Any]:
    path = data_dir / STATE_FILE
    if not path.is_file():
        return {"receipt_ids": [], "syncs": []}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(data_dir: Path, state: dict[str, Any]) -> None:
    path = data_dir / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp.replace(path)


def local_receipt_ids(data_dir: Path) -> set[str]:
    archive = data_dir / ARCHIVE_RECEIPTS
    if not archive.is_dir():
        return set()
    return {path.stem for path in archive.glob("*.json")}


def save_receipt(data_dir: Path, ticket: dict[str, Any]) -> None:
    ticket_id = ticket["id"]
    archive_dir = data_dir / ARCHIVE_RECEIPTS
    archive_dir.mkdir(parents=True, exist_ok=True)

    archive_path = archive_dir / f"{ticket_id}.json"
    with archive_path.open("w", encoding="utf-8") as handle:
        json.dump(ticket, handle, ensure_ascii=False)


def save_coupons(data_dir: Path, coupons: dict[str, Any]) -> Path:
    archive_dir = data_dir / ARCHIVE_COUPONS
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    dated = archive_dir / f"{stamp}.json"
    latest = archive_dir / "latest.json"
    payload = json.dumps(coupons, indent=2, ensure_ascii=False)
    dated.write_text(payload + "\n", encoding="utf-8")
    latest.write_text(payload + "\n", encoding="utf-8")
    return dated


def sync(
    api: LidlPlusApi,
    data_dir: Path,
    *,
    full: bool = False,
    with_coupons: bool = True,
) -> dict[str, int | bool]:
    """
    Download new receipts and refresh the local site index.

    Returns counts: listed, downloaded, skipped, failed, indexed, interrupted.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    existing = set() if full else local_receipt_ids(data_dir)
    remote_ids: list[str] = []
    downloaded = 0
    skipped = 0
    failed = 0
    interrupted = False
    coupons_saved = False

    try:
        log.info("Fetching receipt list from Lidl Plus…")
        remote_list = api.tickets()
        remote_ids = [item["id"] for item in remote_list]

        to_fetch = remote_ids if full else [rid for rid in remote_ids if rid not in existing]
        skipped = len(remote_ids) - len(to_fetch)
        remote_total = len(remote_ids)
        pending = len(to_fetch)

        if pending:
            log.info(
                "Account has %d receipts — %d already archived, %d to download",
                remote_total,
                skipped,
                pending,
            )
        else:
            log.info(
                "Account has %d receipts — all already archived",
                remote_total,
            )

        for index, ticket_id in enumerate(to_fetch, start=1):
            archived = skipped + downloaded
            log.info(
                "Downloading receipt %s (%d/%d new · %d/%d archived)",
                ticket_id,
                index,
                pending,
                archived,
                remote_total,
            )
            try:
                ticket = api.ticket(ticket_id)
                save_receipt(data_dir, ticket)
                downloaded += 1
            except KeyboardInterrupt:
                raise
            except Exception as error:  # pylint: disable=broad-except
                log.error("Failed to download %s: %s", ticket_id, error)
                failed += 1

        if with_coupons:
            try:
                log.info("Saving coupon snapshot…")
                save_coupons(data_dir, api.coupons())
                coupons_saved = True
            except KeyboardInterrupt:
                raise
            except Exception as error:  # pylint: disable=broad-except
                log.warning("Coupon backup failed: %s", error)
    except KeyboardInterrupt:
        interrupted = True
        log.warning("Interrupted — saving progress…")

    indexed = write_site(data_dir) if local_receipt_ids(data_dir) else 0

    state = load_state(data_dir)
    state["receipt_ids"] = sorted(local_receipt_ids(data_dir))
    state["syncs"] = (state.get("syncs") or [])[-49:]
    state["syncs"].append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "listed": len(remote_ids),
            "downloaded": downloaded,
            "skipped": skipped,
            "failed": failed,
            "indexed": indexed,
            "full": full,
            "interrupted": interrupted,
            "coupons_saved": coupons_saved,
        }
    )
    save_state(data_dir, state)
    return {
        "listed": len(remote_ids),
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "indexed": indexed,
        "interrupted": interrupted,
    }


