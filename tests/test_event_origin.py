"""1405:事件 origin 分类法(谁发的=证明什么,显式化)。"""

from __future__ import annotations

from zf.core.events.model import ZfEvent


class TestEnvelope:
    def test_origin_roundtrip(self):
        e = ZfEvent(type="t", origin="worker")
        back = ZfEvent.from_json(e.to_json())
        assert back.origin == "worker"

    def test_reader_tolerates_unknown_keys_both_directions(self):
        # 旧读新:未知键过滤;新读旧:origin 缺省空
        e = ZfEvent.from_json('{"type":"t","future_field":1}')
        assert e.type == "t" and e.origin == ""

    def test_writer_stamps_default_origin_without_overriding(self, tmp_path):
        from zf.core.events.log import EventLog
        from zf.core.events.writer import EventWriter
        log = EventLog(tmp_path / "events.jsonl")
        w = EventWriter(log, default_origin="kernel")
        out = w.append(ZfEvent(type="a"))
        assert out.origin == "kernel"
        out2 = w.append(ZfEvent(type="b", origin="worker"))
        assert out2.origin == "worker"  # 显式最高,不覆盖


class TestLivenessOriginFirst:
    def _stale(self, events, tmp_path):
        import time
        from zf.runtime.lifecycle_liveness_evidence import (
            LifecycleLivenessEvidenceMixin,
        )

        now = time.time()
        for e in events:
            e.payload["epoch"] = now - e.payload.pop("age_s")

        class Host(LifecycleLivenessEvidenceMixin):
            def __init__(self, evs, state_dir):
                self.state_dir = state_dir  # registry 文件不存在 → 走 events
                self.project_root = state_dir
                class _Log:
                    def __init__(s, evs): s._evs = evs
                    def read_days(s, n): return list(s._evs)
                self.event_log = _Log(evs)
            def _event_epoch(self, event):
                return float(event.payload.get("epoch", 0))

        class Role:
            stuck_threshold_seconds = 300
            instance_id = "dev-1"
            name = "dev"

        return Host(events, tmp_path)._worker_liveness_stale(Role())

    def test_worker_origin_counts_even_custom_type(self, tmp_path):
        events = [ZfEvent(type="my.custom.ping", actor="dev-1",
                          origin="worker", payload={"age_s": 10.0})]
        stale, basis = self._stale(events, tmp_path)
        assert stale is False and "last_activity" in basis

    def test_kernel_origin_never_counts_even_allowlisted_type(self, tmp_path):
        # 0325 教训的分类法化:kernel 镜像即使类型在 allowlist 也不算自证
        events = [ZfEvent(type="worker.heartbeat", actor="dev-1",
                          origin="kernel", payload={"age_s": 10.0})]
        stale, basis = self._stale(events, tmp_path)
        assert stale is True and basis == "no_liveness_evidence"

    def test_legacy_no_origin_falls_back_to_allowlist(self, tmp_path):
        ok = [ZfEvent(type="worker.heartbeat", actor="dev-1",
                      payload={"age_s": 10.0})]
        stale, _ = self._stale(ok, tmp_path)
        assert stale is False
        bad = [ZfEvent(type="worker.pane.dead_observed", actor="dev-1",
                       payload={"age_s": 10.0})]
        stale2, basis2 = self._stale(bad, tmp_path)
        assert stale2 is True and basis2 == "no_liveness_evidence"
