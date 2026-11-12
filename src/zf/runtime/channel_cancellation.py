"""Deliver durable Web cancellation to the process that owns a Channel RPC."""
from __future__ import annotations

import threading
from pathlib import Path

from zf.core.events import EventLog


class ChannelCancellationMonitor:
    def __init__(self, state_dir: Path, *, channel_id: str, channel_thread_id: str,
                 run_id: str, provider_thread_id: str, generation: int, synthesis_id: str = ""):
        self.log = EventLog(state_dir / "events.jsonl")
        self.offset = self.log.current_offset()
        self.channel_id, self.channel_thread_id = channel_id, channel_thread_id
        self.run_id, self.provider_thread_id = run_id, provider_thread_id
        self.generation, self.synthesis_id = generation, synthesis_id
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._watch, daemon=True, name="channel-cancel")

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.worker.join(7)

    def _watch(self):
        from zf.web.agent_session_runtime import cancel_agent_session_run, run_key
        while not self.stop.wait(0.2):
            try:
                events, self.offset = self.log.read_from_offset(self.offset)
            except (OSError, ValueError):
                continue
            for event in events:
                p = event.payload or {}
                if p.get("channel_id") != self.channel_id:
                    continue
                direct = (event.type == "agent.session.run.cancelled" and p.get("source") == "web"
                          and p.get("run_id") == self.run_id and p.get("thread_id") == self.provider_thread_id
                          and int(p.get("run_generation") or 1) == self.generation)
                synthesis = (event.type == "channel.synthesis.cancelled" and self.synthesis_id
                             and p.get("request_id") == self.synthesis_id
                             and str(p.get("thread_id") or "main") == self.channel_thread_id)
                if direct or synthesis:
                    cancel_agent_session_run(run_key(run_id=self.run_id, thread_id=self.provider_thread_id))
                    return
