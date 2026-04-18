#!/usr/bin/env python
"""Transcribe pending voice messages using faster-whisper locally.

Run from a Claude Code session on the developer's Mac:
    uv run python scripts/transcribe_voices.py

For each row in `voice_messages` where `transcribed_text IS NULL`:
  1. Dump ogg_data to a temp file.
  2. Pipe through faster-whisper (small multilingual model).
  3. UPDATE transcribed_text + transcribed_at.
  4. Insert the transcript into message_log so the normal batch analyzer
     will pick it up on the next flush.
  5. Mark voice_messages.analyzed = true.

Reads DATABASE_URL from env / .scratch/deploy-secrets.env.

Dependencies (installed on demand):
    uv add --dev faster-whisper
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Inject scratch secrets if present so you don't have to export anything.
_secrets = ROOT / ".scratch" / "deploy-secrets.env"
if _secrets.exists():
    for line in _secrets.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    # psycopg expects sync DSN
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if raw.startswith(prefix):
            return raw.replace(prefix, "postgresql://", 1)
    return raw


def main() -> None:
    dsn = _dsn()
    if not dsn:
        print("DATABASE_URL not set — put it in .scratch/deploy-secrets.env")
        sys.exit(1)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "faster-whisper not installed. Install with:\n"
            "  uv add --dev faster-whisper\n"
            "  uv sync --extra dev"
        )
        sys.exit(1)

    import psycopg

    model = WhisperModel("small", device="cpu", compute_type="int8")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, tg_message_id, tg_user_id, chat_id, duration_sec, "
                "ogg_data FROM voice_messages WHERE transcribed_text IS NULL "
                "ORDER BY created_at LIMIT 100"
            )
            rows = cur.fetchall()

        print(f"{len(rows)} voice messages pending")

        for vid, tg_msg_id, tg_user_id, chat_id, duration_sec, ogg_data in rows:
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                f.write(bytes(ogg_data))
                path = f.name

            print(f"  voice #{vid} ({duration_sec}s) — transcribing...")
            try:
                segments, _ = model.transcribe(path, language="ru", vad_filter=True)
                text = " ".join(seg.text.strip() for seg in segments).strip()
            except Exception as e:
                print(f"    FAIL: {e}")
                Path(path).unlink(missing_ok=True)
                continue
            Path(path).unlink(missing_ok=True)

            if not text:
                text = "(тишина)"

            print(f"    → {text[:120]}")

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE voice_messages SET transcribed_text=%s, transcribed_at=%s "
                    "WHERE id=%s",
                    (text, datetime.now(UTC), vid),
                )
                # Also feed into message_log so the next batch flush sees it.
                cur.execute(
                    "INSERT INTO message_log "
                    "(tg_message_id, tg_user_id, chat_id, text, has_media, is_bot, is_mention, intent_detected) "
                    "VALUES (%s, %s, %s, %s, TRUE, FALSE, FALSE, 'voice_transcript')",
                    (tg_msg_id, tg_user_id, chat_id, f"[voice] {text}"),
                )
            conn.commit()

    print("done. Run batch analyzer on-demand by sending any trigger in chat.")


if __name__ == "__main__":
    main()
