import time

import pytest

from services.audio.worker import AsrWorker, QueueFullError


class SlowService:
    def transcribe(self, audio, language=None):
        time.sleep(0.03)
        return {"text": str(audio), "language": language}


def test_worker_rejects_when_bounded_queue_is_full():
    worker = AsrWorker(SlowService(), max_queue=1)
    try:
        first = worker.submit("one")
        second = worker.submit("two")
        with pytest.raises(QueueFullError):
            worker.submit("three")
        assert first.result(timeout=1)["text"] == "one"
        assert second.result(timeout=1)["text"] == "two"
    finally:
        worker.shutdown()

