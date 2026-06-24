"""
Verification engine — injects challenge-requests into the job stream and
scores node results to maintain reputation and credit integrity.

Challenge injection rate: ~5% of traffic.
Scoring (MVP): exact string match on pre-known answers.
Reputation: rolling pass-rate over the last 20 graded challenges per node.
"""

import json
import random
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from orchestrator.registry import update_reputation

CHALLENGE_RATE = 0.05
REPUTATION_WINDOW = 20
CHALLENGES_FILE = Path(__file__).parent.parent / "challenges.jsonl"


class ChallengePool:
    """Loads challenge prompts and expected answers from challenges.jsonl."""

    def __init__(self, path: Path = CHALLENGES_FILE):
        self._challenges: list[dict] = []
        if path.exists():
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._challenges.append(json.loads(line))

    def __len__(self) -> int:
        return len(self._challenges)

    def random_challenge(self) -> Optional[dict]:
        if not self._challenges:
            return None
        return random.choice(self._challenges)


_pool = ChallengePool()


def maybe_inject_challenge(
    conn: sqlite3.Connection, real_job_id: str, model: str
) -> Optional[str]:
    """
    With CHALLENGE_RATE probability, inserts a disguised challenge job and
    returns its job_id. The calling dispatcher should send this job_id to the
    node instead of the real one.

    Returns None if no injection occurs (normal path).
    """
    if not _pool or random.random() > CHALLENGE_RATE:
        return None

    challenge = _pool.random_challenge()
    if challenge is None:
        return None

    challenge_job_id = str(uuid.uuid4())
    challenge_id = str(uuid.uuid4())

    conn.execute(
        """INSERT INTO jobs (job_id, status, model, prompt, submitted_at)
           VALUES (?, 'queued', ?, ?, ?)""",
        (challenge_job_id, model, challenge["prompt"], int(time.time())),
    )
    conn.execute(
        """INSERT INTO challenges (challenge_id, job_id, expected_answer)
           VALUES (?, ?, ?)""",
        (challenge_id, challenge_job_id, challenge["expected_answer"]),
    )
    conn.commit()
    return challenge_job_id


def score_result(
    conn: sqlite3.Connection, node_id: str, job_id: str, node_answer: str
) -> Optional[bool]:
    """
    If `job_id` corresponds to a challenge, score it and update reputation.
    Returns True/False if it was a challenge, None if it was a real job.
    """
    row = conn.execute(
        "SELECT challenge_id, expected_answer FROM challenges WHERE job_id = ?",
        (job_id,),
    ).fetchone()

    if row is None:
        return None  # real job, no scoring

    expected = row["expected_answer"].strip().lower()
    actual = node_answer.strip().lower()
    passed = int(expected == actual)

    conn.execute(
        """UPDATE challenges SET node_id = ?, passed = ? WHERE challenge_id = ?""",
        (node_id, passed, row["challenge_id"]),
    )
    conn.commit()

    _recompute_reputation(conn, node_id)
    return bool(passed)


def _recompute_reputation(conn: sqlite3.Connection, node_id: str) -> None:
    rows = conn.execute(
        """SELECT passed FROM challenges
           WHERE node_id = ? AND passed IS NOT NULL
           ORDER BY rowid DESC LIMIT ?""",
        (node_id, REPUTATION_WINDOW),
    ).fetchall()

    if not rows:
        return

    pass_rate = sum(r["passed"] for r in rows) / len(rows)
    update_reputation(conn, node_id, pass_rate)
