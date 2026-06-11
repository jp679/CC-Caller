"""Serialized Claude task execution with completion callbacks and pending results."""
import threading
import time
from typing import Callable, Optional

from cc_caller.claude_worker import (
    clean_transcript, log_interaction, name_to_uuid, run_claude,
)
from cc_caller.summarizer import summarize_output


class TaskManager:
    def __init__(self, session_name="caller", new_session=False):
        import uuid as _uuid
        self.session_name = session_name
        self.session_id = str(_uuid.uuid4()) if new_session else name_to_uuid(session_name)
        self.first_run = True
        self.history = []          # [{"task", "summary"}]
        self.pending = None        # {"task", "summary", "detail", "meta"} until consumed
        self.on_complete = None    # Callable[[dict], None], set by wiring
        self._lock = threading.Lock()
        self._started_at = None
        self.current_task = None

    @property
    def busy(self):
        return self._started_at is not None

    @property
    def elapsed(self):
        if self._started_at is None:
            return None
        return time.time() - self._started_at

    def submit(self, task, meta=None):
        """Start a task. Returns False if one is already running."""
        if not self._lock.acquire(blocking=False):
            return False
        self._started_at = time.time()
        self.current_task = task
        thread = threading.Thread(target=self._run, args=(task, meta or {}), daemon=True)
        thread.start()
        return True

    def take_pending(self):
        result, self.pending = self.pending, None
        return result

    def _run(self, task, meta):
        try:
            cleaned = clean_transcript(task)
            output, self.session_id = run_claude(
                cleaned, self.session_id,
                session_name=self.session_name, is_first_run=self.first_run,
            )
            self.first_run = False
            summary = summarize_output(output)["summary"]
            log_interaction(cleaned, output)
            result = {"task": task, "summary": summary, "detail": output, "meta": meta}
            self.history.append({"task": task, "summary": summary})
            self.pending = result
        except Exception as e:
            result = {"task": task, "summary": "The task failed: {}".format(e),
                      "detail": str(e), "meta": meta}
            self.pending = result
        finally:
            self._started_at = None
            self.current_task = None
            self._lock.release()
        if self.on_complete:
            try:
                self.on_complete(result)
            except Exception as e:
                print("[tasks] on_complete error: {}".format(e))
