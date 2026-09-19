from services.analysis.risk import analyze_local, merge_risk


def test_cloud_outage_keeps_local_urgent_signal():
    result = merge_risk("urgent", None, "unavailable")
    assert result["risk_level"] == "urgent"
    assert result["analysis_status"] == "partial"


def test_local_rule_does_not_mark_generic_sadness_urgent():
    assert analyze_local("我今天心情不好")["risk_level"] != "urgent"

