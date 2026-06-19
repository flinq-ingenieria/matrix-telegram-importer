from __future__ import annotations

from pathlib import Path

from telegram_to_matrix.checkpoint import CheckpointStore


def test_checkpoint_upsert_and_get(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    store = CheckpointStore(db)

    store.upsert(
        "123",
        1,
        status="sent",
        content_hash="abc",
        matrix_event_id="$evt1",
        attempts=1,
        last_error=None,
    )

    rec = store.get("123", 1)
    assert rec is not None
    assert rec.status == "sent"
    assert rec.matrix_event_id == "$evt1"

    store.upsert(
        "123",
        1,
        status="failed",
        content_hash="abc2",
        matrix_event_id=None,
        attempts=2,
        last_error="boom",
    )

    rec2 = store.get("123", 1)
    assert rec2 is not None
    assert rec2.status == "failed"
    assert rec2.attempts == 2
    assert rec2.last_error == "boom"
