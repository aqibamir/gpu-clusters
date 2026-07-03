from cmndr.types import RequestItem
from cmndr.router import ThresholdRouter
from cmndr.anonymizer import Anonymizer
from cmndr.detect import WordlistDetector
from cmndr.backends.fake import EchoBackend
from cmndr.providers.fake import EchoProvider
from cmndr.pipeline import Pipeline


def build() -> Pipeline:
    det = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    return Pipeline(ThresholdRouter(), Anonymizer(det), EchoBackend(), EchoProvider())


if __name__ == "__main__":
    pipe = build()
    local = pipe.process_item(
        RequestItem("d1", "summarize", "what is 2+2", sensitivity_hint="low"))
    print("LOCAL :", local.route, "->", local.text)
    esc = pipe.process_item(
        RequestItem("d2", "summarize", "Jane Smith joined Acme Corp",
                    sensitivity_hint="high"),
        approve=lambda p: p.accept())
    print("ESCAL :", esc.route, "->", esc.text)
