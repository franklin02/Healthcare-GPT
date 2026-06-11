import json

import pytest

from src.debug_noise import NoiseDebugWriter, classification_stage


def test_noise_debug_writer_keeps_valid_json_after_each_record(tmp_path):
    path = tmp_path / "noise.json"
    writer = NoiseDebugWriter(path)

    assert json.loads(path.read_text(encoding="utf-8")) == {"noise": []}

    writer.write_rejection(
        pipeline="GDELT",
        source="Example",
        title="Policy update",
        url="https://example.com/noise",
        publication_date="2026-01-02",
        classification_stage="llm",
        rejection_reason="No operational disruption",
        classified_text="Classified excerpt",
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["noise"]) == 1
    assert data["noise"][0] == {
        "pipeline": "GDELT",
        "source": "Example",
        "title": "Policy update",
        "url": "https://example.com/noise",
        "publication_date": "2026-01-02",
        "classification_stage": "llm",
        "rejection_reason": "No operational disruption",
        "classified_text": "Classified excerpt",
        "rejected_at": data["noise"][0]["rejected_at"],
    }

    writer.write_rejection(
        pipeline="HTML",
        source="Other source",
        title="Another rejection",
        url="https://example.com/other",
        publication_date="2026-01-03",
        classification_stage="bert",
        rejection_reason="BERT: unrelated news",
        classified_text="Other classified text",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["noise"]) == 2
    assert data["noise"][1]["classification_stage"] == "bert"

    writer.close()
    assert json.loads(path.read_text(encoding="utf-8")) == data


def test_noise_debug_writer_rejects_writes_after_close(tmp_path):
    writer = NoiseDebugWriter(tmp_path / "noise.json")
    writer.close()

    with pytest.raises(ValueError):
        writer.write_rejection(
            pipeline="HTML",
            source="Example",
            title="Title",
            url="https://example.com",
            publication_date="",
            classification_stage="bert",
            rejection_reason="BERT: unrelated news",
            classified_text="Text",
        )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("BERT: unrelated news", "bert"),
        ("No impact detected", "llm"),
        ("Body too short for LLM review", None),
        ("Parsing Error", None),
        (None, None),
    ],
)
def test_classification_stage_excludes_operational_failures(reason, expected):
    assert classification_stage(reason) == expected
