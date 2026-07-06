"""TR-26/27/31/32: the trusted-side console. A UI over the Entry Point —
console jobs run the same Pipeline as programmatic ones; the preview gate is
the same object; restored output exists only here, on the device."""

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from cgn.contract import StatusEvent
from cmndr.pipeline import Pipeline
from cmndr.preview import EscalationNotAccepted, Preview
from cmndr.types import RequestItem

_PCT = {"routing": 0.1, "anonymizing": 0.25, "preview": 0.4, "dispatched": 0.6,
        "running": 0.75, "restoring": 0.9, "done": 1.0, "failed": 1.0}


@dataclass
class Session:
    request_id: str
    stage: str = "routing"
    preview: Preview | None = None
    result: dict | None = None
    events: list[dict] = field(default_factory=list)
    gate: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False


class SubmitBody(BaseModel):
    payload: str
    task_type: str
    sensitivity_hint: str | None = None


class ApproveBody(BaseModel):
    accept: bool
    remove: list[str] = []
    add: list[dict] = []


def create_console_app(pipeline: Pipeline) -> FastAPI:
    app = FastAPI(title="cmndr-console")
    sessions: dict[str, Session] = {}
    all_events: list[dict] = []

    def emit(s: Session, state: str) -> None:
        s.stage = state
        ev = StatusEvent(job_id=s.request_id, state=state, pct=_PCT[state],
                         node_id=None, elapsed_ms=0, stage=state).model_dump()
        s.events.append(ev)
        all_events.append(ev)

    def run(s: Session, item: RequestItem) -> None:
        def approve(preview: Preview) -> None:
            emit(s, "anonymizing")
            s.preview = preview
            emit(s, "preview")
            s.gate.wait()
            if s.accepted:
                preview.accept()
                emit(s, "dispatched")
                emit(s, "running")

        try:
            resp = pipeline.process_item(item, approve=approve)
            if resp.route == "escalate":
                emit(s, "restoring")
            else:
                emit(s, "running")
            s.result = {"route": resp.route, "text": resp.text,
                        "restoration_anomalies": resp.restoration_anomalies}
            emit(s, "done")
        except EscalationNotAccepted:
            emit(s, "failed")
        except Exception:
            emit(s, "failed")

    @app.post("/requests")
    def submit(body: SubmitBody):
        rid = f"req-{uuid.uuid4().hex[:12]}"
        s = Session(request_id=rid)
        sessions[rid] = s
        emit(s, "routing")
        item = RequestItem(rid, body.task_type, body.payload,
                           sensitivity_hint=body.sensitivity_hint)
        threading.Thread(target=run, args=(s, item), daemon=True).start()
        return {"request_id": rid}

    @app.get("/requests/{rid}")
    def status(rid: str):
        s = sessions.get(rid)
        if s is None:
            raise HTTPException(status_code=404)
        preview = None
        if s.stage == "preview" and s.preview is not None:
            preview = {"text": s.preview.payload.text,
                       "entities": [{"placeholder": e.placeholder,
                                     "entity_type": e.entity_type}
                                    for e in s.preview.redaction_map.entries]}
        return {"request_id": rid, "stage": s.stage,
                "preview": preview, "result": s.result}

    @app.post("/requests/{rid}/approve")
    def approve(rid: str, body: ApproveBody):
        s = sessions.get(rid)
        if s is None or s.preview is None:
            raise HTTPException(status_code=404)
        for add in body.add:
            s.preview.add_redaction(add["value"], add["entity_type"])
        for ph in body.remove:
            s.preview.remove_redaction(ph)
        s.accepted = body.accept
        s.gate.set()
        return {"ok": True}

    @app.get("/events")
    def events():
        return {"events": list(all_events)}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "static" / "console.html").read_text()

    return app


def main() -> None:
    import argparse

    import httpx
    import uvicorn

    from cmndr.anonymizer import Anonymizer
    from cmndr.audit import AuditLog
    from cmndr.backends.fake import EchoBackend
    from cmndr.detect import WordlistDetector
    from cmndr.providers.cgn import CGNProvider
    from cmndr.router import ThresholdRouter

    p = argparse.ArgumentParser()
    p.add_argument("--orchestrator", default="http://localhost:8000")
    p.add_argument("--model", default="mistral-7b-instruct-v0.2.Q4_K_M")
    p.add_argument("--port", type=int, default=8100)
    args = p.parse_args()

    from cmndr.detectors.presidio import PresidioDetector, spacy_model_available
    if spacy_model_available("en_core_web_sm"):
        detector = PresidioDetector()
        from cmndr.routers.heuristic import HeuristicRouter
        router = HeuristicRouter(detector)
    else:
        detector, router = WordlistDetector({}), ThresholdRouter()

    from cmndr.backends.llamacpp import (DEFAULT_MODEL_PATH, LlamaCppBackend,
                                         llamacpp_available)
    backend = (LlamaCppBackend(DEFAULT_MODEL_PATH)
               if llamacpp_available() and Path(DEFAULT_MODEL_PATH).exists()
               else EchoBackend())

    pipeline = Pipeline(router, Anonymizer(detector), backend,
                        CGNProvider(httpx.Client(base_url=args.orchestrator,
                                                 timeout=300), args.model),
                        audit=AuditLog("console_audit.jsonl"))
    uvicorn.run(create_console_app(pipeline), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
