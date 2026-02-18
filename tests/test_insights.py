"""Tests for app.engine.insights module."""

from app.engine.insights import build_project_insights


def test_build_project_insights_empty():
    """Empty submissions should return empty results."""
    result = build_project_insights([])
    assert result == {
        "weakest_dims": [],
        "frequent_penalties": [],
        "recommendations": [],
        "dimension_avg": {},
    }


def test_build_project_insights_single_submission():
    """Single submission should calculate dimension averages correctly."""
    submissions = [
        {
            "report": {
                "dimension_scores": {
                    "D1": {"score": 80},
                    "D2": {"score": 60},
                },
                "penalties": [],
            }
        }
    ]
    result = build_project_insights(submissions)
    assert result["dimension_avg"] == {"D1": 80.0, "D2": 60.0}
    # D2 is weaker
    assert result["weakest_dims"][0]["dimension"] == "D2"
    assert result["weakest_dims"][0]["avg_score"] == 60.0


def test_build_project_insights_multiple_submissions():
    """Multiple submissions should average dimension scores."""
    submissions = [
        {
            "report": {
                "dimension_scores": {
                    "D1": {"score": 80},
                    "D2": {"score": 40},
                },
                "penalties": [],
            }
        },
        {
            "report": {
                "dimension_scores": {
                    "D1": {"score": 60},
                    "D2": {"score": 80},
                },
                "penalties": [],
            }
        },
    ]
    result = build_project_insights(submissions)
    # D1: (80+60)/2 = 70, D2: (40+80)/2 = 60
    assert result["dimension_avg"]["D1"] == 70.0
    assert result["dimension_avg"]["D2"] == 60.0


def test_penalty_statistics():
    """Penalties should be counted correctly."""
    submissions = [
        {
            "report": {
                "dimension_scores": {},
                "penalties": [
                    {"code": "P-ACTION-001"},
                    {"code": "P-ACTION-001"},
                    {"code": "P-EMPTY-001"},
                ],
            }
        },
        {
            "report": {
                "dimension_scores": {},
                "penalties": [
                    {"code": "P-ACTION-001"},
                ],
            }
        },
    ]
    result = build_project_insights(submissions)
    # P-ACTION-001 appears 3 times, P-EMPTY-001 appears 1 time
    frequent = {p["code"]: p["count"] for p in result["frequent_penalties"]}
    assert frequent["P-ACTION-001"] == 3
    assert frequent["P-EMPTY-001"] == 1


def test_recommendations_for_action_penalty():
    """P-ACTION-001 should generate specific recommendation."""
    submissions = [
        {
            "report": {
                "dimension_scores": {},
                "penalties": [{"code": "P-ACTION-001"}],
            }
        }
    ]
    result = build_project_insights(submissions)
    action_recs = [r for r in result["recommendations"] if "措施落地" in r["reason"]]
    assert len(action_recs) == 1
    assert "四要素" in action_recs[0]["action"]


def test_recommendations_for_empty_penalty():
    """P-EMPTY-001 should generate specific recommendation."""
    submissions = [
        {
            "report": {
                "dimension_scores": {},
                "penalties": [{"code": "P-EMPTY-001"}],
            }
        }
    ]
    result = build_project_insights(submissions)
    empty_recs = [r for r in result["recommendations"] if "空泛承诺" in r["reason"]]
    assert len(empty_recs) == 1
    assert "量化指标" in empty_recs[0]["action"]


def test_weakest_dims_limited_to_5():
    """Weakest dimensions should be limited to 5."""
    # Create 8 dimensions
    submissions = [
        {
            "report": {
                "dimension_scores": {f"D{i}": {"score": i * 10} for i in range(1, 9)},
                "penalties": [],
            }
        }
    ]
    result = build_project_insights(submissions)
    assert len(result["weakest_dims"]) == 5
    # Lowest scores first (D1=10, D2=20, D3=30, D4=40, D5=50)
    assert result["weakest_dims"][0]["dimension"] == "D1"
    assert result["weakest_dims"][0]["avg_score"] == 10.0


def test_frequent_penalties_limited_to_5():
    """Frequent penalties should be limited to 5."""
    # P-0 appears 10 times, P-1 appears 9 times, etc.
    all_penalties = []
    for i in range(10):
        all_penalties.extend([{"code": f"P-{i}"}] * (10 - i))

    submissions = [
        {
            "report": {
                "dimension_scores": {},
                "penalties": all_penalties,
            }
        }
    ]
    result = build_project_insights(submissions)
    assert len(result["frequent_penalties"]) == 5
    # Highest count first
    assert result["frequent_penalties"][0]["code"] == "P-0"
    assert result["frequent_penalties"][0]["count"] == 10


def test_recommendations_limited_to_8():
    """Recommendations should be limited to 8."""
    # Create many dimensions to generate many recommendations
    submissions = [
        {
            "report": {
                "dimension_scores": {f"D{i}": {"score": i} for i in range(1, 15)},
                "penalties": [
                    {"code": "P-ACTION-001"},
                    {"code": "P-EMPTY-001"},
                ],
            }
        }
    ]
    result = build_project_insights(submissions)
    assert len(result["recommendations"]) <= 8
