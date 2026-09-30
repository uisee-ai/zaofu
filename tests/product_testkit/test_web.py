import pytest

from .web import assert_sse_delivery_once, assert_web_truth


def test_web_truth_requires_api_dom_and_refresh_convergence():
    assert_web_truth(canonical_status="submitted", api_status="submitted",
                     dom_status="submitted", refreshed_status="submitted")
    with pytest.raises(AssertionError):
        assert_web_truth(canonical_status="submitted", api_status="submitted",
                         dom_status="pending", refreshed_status="submitted")


def test_web_sse_event_must_arrive_exactly_once():
    assert_sse_delivery_once("evt-1", [{"data": '{"id":"evt-1"}'}])
    with pytest.raises(AssertionError):
        assert_sse_delivery_once("evt-1", [{"data": '{"id":"evt-1"}'}, {"data": '{"id":"evt-1"}'}])
