"""Serialized Claude task execution with completion callbacks and pending results."""
import os
import threading
import time
from typing import Callable, Optional

from cc_caller import callermem
from cc_caller.claude_worker import (
    clean_transcript, log_interaction, name_to_uuid, run_claude, WorkerCancelled,
)
from cc_caller.summarizer import summarize_output


class TaskManager:
    def __init__(self, session_name="caller", new_session=False, show_exchange=False, session_id=None):
        import uuid as _uuid
        self.session_name = session_name
        self.show_exchange = show_exchange
        if session_id:
            self.session_id = session_id
        elif new_session:
            self.session_id = str(_uuid.uuid4())
        else:
            self.session_id = name_to_uuid(session_name)
        self.first_run = True
        self.on_complete = None    # Callable[[dict], None], set by wiring
        self.on_activity = None    # Callable[[str], None], set by wiring
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()  # guards pending + history (NOT _lock: held for task duration)
        self._cancel_event = threading.Event()
        self._queued = None
        self._started_at = None
        self.current_task = None
        self.current_activity = None
        self.workdir = os.getcwd()
        state = callermem.load(self.session_id)
        self.history = state["history"]      # [{"task", "summary"}]
        self.pending = state["pending"]      # {"task", "summary", "detail", "meta"} until consumed
        self.voice_notes = state["voice_notes"]

    @property
    def busy(self):
        return self._started_at is not None

    @property
    def elapsed(self):
        started = self._started_at  # snapshot: worker thread may None this between reads
        if started is None:
            return None
        return time.time() - started

    def submit(self, task, meta=None):
        """Start a task. Returns False if one is already running."""
        if not self._lock.acquire(blocking=False):
            return False
        self._cancel_event.clear()
        self._started_at = time.time()
        self.current_task = task
        thread = threading.Thread(target=self._run, args=(task, meta or {}), daemon=True)
        thread.start()
        return True

    def take_pending(self):
        with self._state_lock:
            result, self.pending = self.pending, None
            hist = list(self.history)
        self._persist(hist, None)
        return result

    def cancel(self):
        """Cancel the running task (and drop any queued one). Returns False if idle."""
        with self._state_lock:
            self._queued = None
        if not self.busy:
            return False
        self._cancel_event.set()
        return True

    def queue_next(self, task, meta=None):
        """Queue ONE follow-up task to run when the current one finishes.
        Returns False if nothing is running (caller should submit instead)."""
        if not self.busy:
            return False
        with self._state_lock:
            self._queued = (task, meta or {})
        return True

    def switch_session(self, session_id=None, session_name=None):
        """Rebind to another Claude session. Refused (False) while a task runs.
        Same-session is a no-op; a real switch restores the target session's
        caller memory (history, pending, voice_notes) from disk."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if session_id:
                new_id, new_name = session_id, None
            else:
                name = session_name or "caller"
                new_id, new_name = name_to_uuid(name), name
            if new_id == self.session_id:
                return True
            self.session_id = new_id
            self.session_name = new_name
            self.first_run = True
            state = callermem.load(new_id)
            with self._state_lock:
                self.history = state["history"]
                self.pending = state["pending"]
                self.voice_notes = state["voice_notes"]
            return True
        finally:
            self._lock.release()

    def _persist(self, history, pending):
        """Write history/pending only -- voice_notes belongs to the distiller
        (callermem.save preserves unprovided fields). Called OUTSIDE
        _state_lock with copies so the file op never holds the lock."""
        try:
            callermem.save(self.session_id, history=history, pending=pending)
        except Exception as e:
            print("[tasks] persist failed: {}".format(e))

    def _set_activity(self, text):
        self.current_activity = text
        if self.show_exchange:
            try:
                print("[task] ~ {}".format(text))
            except Exception:
                pass
        if self.on_activity:
            try:
                self.on_activity(text)
            except Exception as e:
                print("[tasks] on_activity error: {}".format(e))

    def _run(self, task, meta):
        t0 = time.time()
        try:
            cleaned = clean_transcript(task)
            if self.show_exchange:
                print("[task] -> {}".format(cleaned))
            fresh_id = None
            if self.session_name and self.session_id == name_to_uuid(self.session_name):
                fresh_id = self.session_id
            output, self.session_id = run_claude(
                cleaned, self.session_id,
                session_name=self.session_name, is_first_run=self.first_run,
                on_activity=self._set_activity, cwd=self.workdir,
                fresh_session_id=fresh_id,
                cancel_event=self._cancel_event,
            )
            self.first_run = False
            summary = summarize_output(output)["summary"]
            if self.show_exchange:
                print("[task] done ({}s): {}".format(int(time.time() - t0), summary))
            log_interaction(cleaned, output)
            result = {"task": task, "summary": summary, "detail": output, "meta": meta}
            with self._state_lock:
                self.history.append({"task": task, "summary": summary})
                del self.history[:-50]   # bound growth; consumers read history[-5:]
                self.pending = result
                hist, pend = list(self.history), self.pending
            self._persist(hist, pend)
        except WorkerCancelled:
            result = {"task": task, "summary": "Task cancelled.", "detail": "",
                      "meta": meta, "cancelled": True}
            # Do NOT set pending, do NOT append to history, do NOT persist
        except Exception as e:
            if self.show_exchange:
                print("[task] FAILED: {}".format(e))
            result = {"task": task, "summary": "The task failed: {}".format(e),
                      "detail": str(e), "meta": meta}
            with self._state_lock:
                self.pending = result
                hist, pend = list(self.history), self.pending
            self._persist(hist, pend)
        finally:
            self._started_at = None
            self.current_task = None
            self.current_activity = None
            self._lock.release()
        if self.on_complete:
            try:
                self.on_complete(result)
            except Exception as e:
                print("[tasks] on_complete error: {}".format(e))
        with self._state_lock:
            queued, self._queued = self._queued, None
        if queued:
            self.submit(queued[0], meta=queued[1])
