import pytest
from deepdive.review import claim_rows, write_review, score_review


def test_blind_review_does_not_leak_reference_labels_or_accept_missing_judgments(tmp_path):
    dataset = {"examples": [{"id": "case", "claim": "20 adults", "passage": "20 adults",
                             "quote": "20 adults", "supported": True}]}
    rows = claim_rows(dataset)
    assert "supported" not in rows[0]
    path = tmp_path / "review.csv"
    write_review(rows, path)
    with pytest.raises(FileExistsError):
        write_review(rows, path)
    with pytest.raises(ValueError, match="label"):
        score_review(rows, rows)
    completed = [{**rows[0], "human_label": "supported", "reviewer": "test fixture",
                  "reviewed_at": "2026-10-20", "notes": "Fixture literal match"}]
    assert score_review(rows, completed)["human_labeled_support_fraction"] == 1
    completed[0]["passages"] = "Tampered evidence"
    with pytest.raises(ValueError, match="changed"):
        score_review(rows, completed)
