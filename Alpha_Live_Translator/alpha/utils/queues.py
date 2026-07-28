"""Bounded queue helpers for audio pipelines."""

import queue


def put_bounded(audio_queue, item):
    """Enqueue audio; drop oldest item if the queue is full."""
    if audio_queue is None:
        return False
    try:
        audio_queue.put_nowait(item)
        return True
    except queue.Full:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            audio_queue.put_nowait(item)
            return True
        except queue.Full:
            return False
