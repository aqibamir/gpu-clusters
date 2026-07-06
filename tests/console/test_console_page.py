from fastapi.testclient import TestClient

from cmndr.anonymizer import Anonymizer
from cmndr.backends.fake import EchoBackend
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.fake import EchoProvider
from cmndr.router import ThresholdRouter
from console.app import create_console_app


def test_console_page_served():
    pipe = Pipeline(ThresholdRouter(), Anonymizer(WordlistDetector({})),
                    EchoBackend(), EchoProvider())
    client = TestClient(create_console_app(pipe))
    r = client.get("/")
    assert r.status_code == 200
    assert "cmndr console" in r.text.lower()
    for stage in ("routing", "anonymizing", "preview", "dispatched",
                  "running", "restoring", "done"):
        assert stage in r.text
