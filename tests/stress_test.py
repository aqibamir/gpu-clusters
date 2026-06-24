"""
Phase 5 stress test — 100 concurrent batch jobs across 3+ nodes.

Requires a running orchestrator and at least 3 node agents.
Run with:
    ORCHESTRATOR_URL=http://localhost:8000 python -m tests.stress_test

Options (env vars):
    ORCHESTRATOR_URL   — orchestrator base URL (default: http://localhost:8000)
    STRESS_JOB_COUNT   — number of jobs to submit (default: 100)
    STRESS_MODEL       — model name (default: mistral-7b-q4_k_m)
    STRESS_MAX_TOKENS  — tokens per job (default: 128)
    STRESS_TIMEOUT     — seconds to wait for all jobs (default: 600)
"""

import asyncio
import os
import statistics
import time
import uuid

import httpx

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8000")
JOB_COUNT = int(os.environ.get("STRESS_JOB_COUNT", "100"))
MODEL = os.environ.get("STRESS_MODEL", "mistral-7b-q4_k_m")
MAX_TOKENS = int(os.environ.get("STRESS_MAX_TOKENS", "128"))
TIMEOUT = float(os.environ.get("STRESS_TIMEOUT", "600"))

SAMPLE_PROMPTS = [
    "Summarize the following contract clause in one sentence: [ORG_1] agrees to provide services to [ORG_2] for a period of 12 months commencing on [DATE_1], with a monthly fee of [AMOUNT_1].",
    "Explain the key obligations in this agreement: [PERSON_1] shall deliver the report by [DATE_1] and notify [PERSON_2] of any delays within 48 hours.",
    "What is the main risk described here? The vendor [ORG_1] may terminate this agreement with 30 days notice if [ORG_2] fails to make payment by [DATE_1].",
    "Identify the parties and their roles: [PERSON_1] (Consultant) and [ORG_1] (Client) agree to the following terms.",
    "Summarize the liability clause: Neither party shall be liable for indirect damages exceeding [AMOUNT_1] in total.",
]


async def submit_job(client: httpx.AsyncClient, prompt: str) -> str:
    r = await client.post(
        "/jobs",
        json={"model": MODEL, "prompt": prompt, "max_tokens": MAX_TOKENS},
    )
    r.raise_for_status()
    return r.json()["job_id"]


async def poll_job(client: httpx.AsyncClient, job_id: str, deadline: float) -> dict:
    while time.time() < deadline:
        r = await client.get(f"/jobs/{job_id}")
        r.raise_for_status()
        data = r.json()
        if data["status"] in ("done", "failed"):
            return data
        await asyncio.sleep(1.0)
    return {"job_id": job_id, "status": "timeout", "result": None}


async def run_stress_test():
    print(f"[stress] Submitting {JOB_COUNT} jobs to {ORCHESTRATOR_URL} …")
    deadline = time.time() + TIMEOUT

    async with httpx.AsyncClient(base_url=ORCHESTRATOR_URL, timeout=30) as client:
        # Submit all jobs
        submit_start = time.time()
        prompts = [SAMPLE_PROMPTS[i % len(SAMPLE_PROMPTS)] for i in range(JOB_COUNT)]
        job_ids = await asyncio.gather(*[submit_job(client, p) for p in prompts])
        print(f"[stress] All {JOB_COUNT} jobs submitted in {time.time() - submit_start:.1f}s")

        # Poll all jobs concurrently
        poll_start = time.time()
        results = await asyncio.gather(
            *[poll_job(client, jid, deadline) for jid in job_ids]
        )

    total_time = time.time() - poll_start
    done = [r for r in results if r["status"] == "done"]
    failed = [r for r in results if r["status"] == "failed"]
    timed_out = [r for r in results if r["status"] == "timeout"]

    print(f"\n[stress] === Results ===")
    print(f"  Done:      {len(done)}/{JOB_COUNT}")
    print(f"  Failed:    {len(failed)}/{JOB_COUNT}")
    print(f"  Timed out: {len(timed_out)}/{JOB_COUNT}")
    print(f"  Wall time: {total_time:.1f}s")

    if done:
        # Check for un-restored placeholders in results (data leakage check)
        import re
        leaky = [
            r for r in done
            if r.get("result") and re.search(r"\[(?:PERSON|ORG|AMOUNT|DATE_TIME|LOCATION)_\d+\]", r["result"])
        ]
        print(f"  Data leakage (placeholders in results): {len(leaky)}")

    if failed or timed_out:
        print("\n[stress] FAILED — not all jobs completed successfully.")
        return False

    print("\n[stress] PASSED — all jobs completed.")
    return True


if __name__ == "__main__":
    ok = asyncio.run(run_stress_test())
    raise SystemExit(0 if ok else 1)
