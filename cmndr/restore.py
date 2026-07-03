from cmndr.types import RedactionMap, RestoreResult


def restore(text: str, redaction_map: RedactionMap) -> RestoreResult:
    out = text
    anomalies: list[str] = []
    for entry in redaction_map.entries:
        if entry.placeholder in out:
            out = out.replace(entry.placeholder, entry.original_value)
        else:
            anomalies.append(entry.placeholder)
    return RestoreResult(text=out, restoration_anomalies=anomalies)
