"""
cmndr — CLI job submitter.

Anonymizes a prompt on-device, submits to the orchestrator, polls for the
result, and restores entity placeholders before displaying the output.

Usage:
    ORCHESTRATOR_URL=http://localhost:8000 API_KEY=secret python -m client.cmndr \
        --model mistral-7b-instruct-v0.2.Q4_K_M \
        --prompt "Summarize the contract between Jane Smith and Acme Corp." \
        --max-tokens 256
"""

import argparse
import os
import sys
import time

import httpx

from anonymizer.pipeline import make_anonymizer


def submit_and_wait(
    orchestrator_url: str,
    model: str,
    prompt: str,
    api_key: str = "",
    max_tokens: int = 512,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
    stub_anon: bool = False,
) -> str:
    anon = make_anonymizer(stub=stub_anon)
    anonymized_prompt, enc_map = anon.anonymize(prompt)

    headers = {"X-Api-Key": api_key} if api_key else {}

    with httpx.Client(base_url=orchestrator_url, timeout=30, headers=headers) as client:
        r = client.post(
            "/jobs",
            json={"model": model, "prompt": anonymized_prompt, "max_tokens": max_tokens},
        )
        r.raise_for_status()
        job_id = r.json()["job_id"]
        print(f"[cmndr] Job submitted: {job_id}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            r = client.get(f"/jobs/{job_id}")
            r.raise_for_status()
            data = r.json()
            status = data["status"]

            if status == "done":
                raw_result = data["result"] or ""
                return anon.restore(raw_result, enc_map)

            if status == "failed":
                raise RuntimeError(f"Job {job_id} failed on the network.")

            print(f"[cmndr] status={status} … (assigned_node={data.get('assigned_node')})")
            time.sleep(poll_interval)

    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s.")


def main():
    parser = argparse.ArgumentParser(description="Submit an inference job to the cluster.")
    parser.add_argument(
        "--orchestrator",
        default=os.environ.get("ORCHESTRATOR_URL", "http://localhost:8000"),
    )
    parser.add_argument("--model", default="mistral-7b-instruct-v0.2.Q4_K_M")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--stub-anon", action="store_true", help="Skip NER (for testing)")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("API_KEY", ""),
        help="API key for the orchestrator (or set API_KEY env var)",
    )
    args = parser.parse_args()

    try:
        result = submit_and_wait(
            orchestrator_url=args.orchestrator,
            model=args.model,
            prompt=args.prompt,
            api_key=args.api_key,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            stub_anon=args.stub_anon,
        )
        print("\n--- Result ---")
        print(result)
    except (RuntimeError, TimeoutError) as e:
        print(f"[cmndr] Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
