from cmndr.detect import Detector, WordlistDetector


def test_wordlist_detector_finds_all_occurrences():
    det: Detector = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    ents = det.detect("Jane Smith met Acme Corp and Jane Smith left")
    kinds = sorted((e.entity_type, e.text) for e in ents)
    assert ("ORG", "Acme Corp") in kinds
    assert sum(1 for e in ents if e.text == "Jane Smith") == 2
    # spans must be correct and non-overlapping
    for e in ents:
        assert "Jane Smith met Acme Corp and Jane Smith left"[e.start:e.end] == e.text
