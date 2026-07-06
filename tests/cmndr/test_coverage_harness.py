from cmndr.detect import WordlistDetector
from eval.coverage import measure_coverage, load_samples


def test_perfect_detector_scores_full_recall():
    samples = [{"text": "Jane Smith works at Acme Corp",
                "gold": [{"entity_type": "PERSON", "text": "Jane Smith"},
                         {"entity_type": "ORG", "text": "Acme Corp"}]}]
    det = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    report = measure_coverage(det, samples)
    assert report.recall == 1.0
    assert report.false_positives == 0
    assert report.gold_total == 2


def test_missing_entity_lowers_recall_and_extra_counts_fp():
    samples = [{"text": "Jane Smith works at Acme Corp",
                "gold": [{"entity_type": "PERSON", "text": "Jane Smith"},
                         {"entity_type": "ORG", "text": "Acme Corp"}]}]
    det = WordlistDetector({"Jane Smith": "PERSON", "works": "VERB"})
    report = measure_coverage(det, samples)
    assert report.recall == 0.5
    assert report.false_positives == 1


def test_load_samples_reads_jsonl():
    samples = load_samples("eval/datasets/pii_labeled.jsonl")
    assert len(samples) == 20
    assert all("text" in s and "gold" in s for s in samples)
