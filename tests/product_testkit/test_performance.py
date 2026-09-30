from .performance import evaluate_product_performance


def test_product_performance_budget_is_explicit_and_fail_closed():
    summary = {"metrics": {"http_req_duration": {"values": {"p(95)": 120, "count": 10}, "thresholds": {"p(95)<200": {"ok": True}, "p(95)<100": {"ok": False}}}}}
    thresholds = [{"metric": "http_req_duration", "statistic": "p(95)", "operator": "<", "limit": 200}]
    assert evaluate_product_performance(summary, thresholds).status == "pass"
    assert evaluate_product_performance(summary, [{**thresholds[0], "limit": 100}], exit_code=99).status == "fail"


def test_real_k6_summary_export_legacy_threshold_boolean_is_supported():
    summary = {
        "metrics": {
            "http_req_duration": {
                "avg": 12.0, "p(95)": 20.0, "min": 10.0, "max": 25.0,
                "thresholds": {"p(95)<500": False},
            }
        }
    }
    thresholds = [{"metric": "http_req_duration", "statistic": "p(95)", "operator": "<", "limit": 500}]

    result = evaluate_product_performance(summary, thresholds)

    assert result.status == "pass"
