"""FastAPI server for the Gemini PWA: token-gated WS bridge, push, static."""
import hmac
import json
import pathlib
import threading
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from cc_caller import callermem, push, sessions, summarizer
from cc_caller.gemini_live import GeminiLiveSession

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"


class AppState:
    def __init__(self, token, task_manager, api_key, model, vapid_public_key,
                 base_system_prompt, subscriptions, show_exchange=False):
        self.token = token
        self.task_manager = task_manager
        self.api_key = api_key
        self.model = model
        self.vapid_public_key = vapid_public_key
        self.base_system_prompt = base_system_prompt
        self.subscriptions = subscriptions
        self.show_exchange = show_exchange
        self.session_holder = {"session": None}


def build_system_prompt(state, resumed=None, suppress_pending=False):
    """Base relay prompt + recent history + any pending (undelivered) result.
    suppress_pending: skip the PENDING block when the opener already carries
    the result, so the agent doesn't report it twice."""
    prompt = state.base_system_prompt
    if state.task_manager.history:
        prompt += ("\n\nRECENT CONVERSATION (results you already reported -- use these to "
                   "answer follow-ups WITHOUT calling askCodingAgent again):\n")
        for entry in state.task_manager.history[-5:]:
            prompt += "\nUser asked: {}\nResult: {}\n".format(
                entry["task"], entry["summary"][:500])
    notes = getattr(state.task_manager, "voice_notes", None)
    if notes:
        block = "\n\nPREVIOUS CALLS (your own memory of earlier conversations on this session):\n"
        for n in notes:
            line = "- {}\n".format(n)
            if len(block) + len(line) > 1500:
                break
            block += line
        prompt += block
    if state.task_manager.pending and not suppress_pending:
        prompt += ("\n\nPENDING RESULT -- the user has not heard this yet. Open the "
                   "conversation by telling them: {}\n".format(
                       state.task_manager.pending["summary"]))
    if resumed:
        block = ("\n\nRESUMED CLAUDE SESSION -- recent exchange between the user and Claude "
                 "(context only; Claude already remembers all of it; answer follow-ups from "
                 "this when you can):\n")
        for m in resumed:
            line = "{}: {}\n".format(m["role"], m["text"])
            if len(block) + len(line) > 3000:
                break
            block += line
        prompt += block
    return prompt


def _token_ok(state, supplied):
    return bool(supplied) and hmac.compare_digest(state.token, supplied)


def create_app(state):
    app = FastAPI()

    def require_token(request: Request):
        supplied = request.query_params.get("token") or request.headers.get("x-cc-token")
        if not _token_ok(state, supplied):
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/")
    async def index(request: Request):
        html = (STATIC_DIR / "index.html").read_text()
        supplied = request.query_params.get("token", "")
        if _token_ok(state, supplied):
            html = html.replace('href="/manifest.json"',
                                'href="/manifest.json?token={}"'.format(supplied))
        return Response(html, media_type="text/html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(STATIC_DIR / "sw.js",
                            media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/"})

    @app.get("/manifest.json")
    async def manifest(request: Request):
        data = json.loads((STATIC_DIR / "manifest.json").read_text())
        supplied = request.query_params.get("token", "")
        if _token_ok(state, supplied):
            data["start_url"] = "/?callback=0&token={}".format(supplied)
        return Response(json.dumps(data), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/config")
    async def api_config(request: Request):
        require_token(request)
        return {"vapidPublicKey": state.vapid_public_key}

    @app.get("/api/sessions")
    async def api_sessions(request: Request):
        require_token(request)
        tm = state.task_manager
        recent = sessions.recent_sessions(limit=5)
        for s in recent:
            try:
                title = callermem.load(s["session_id"]).get("title")
            except Exception:
                title = None
            if title:
                s["label"] = title
        current = {"id": tm.session_id, "name": tm.session_name}
        try:
            current["title"] = callermem.load(tm.session_id).get("title")
        except Exception:
            pass
        return {
            "current": current,
            "sessions": recent,
        }

    @app.post("/api/push-subscribe")
    async def push_subscribe(request: Request):
        require_token(request)
        sub = await request.json()
        if sub not in state.subscriptions:
            state.subscriptions.append(sub)
            push.save_subscriptions(state.subscriptions)
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws_bridge(websocket: WebSocket):
        if not _token_ok(state, websocket.query_params.get("token")):
            await websocket.close(code=4401)
            return
        await websocket.accept()

        switch_note = None
        requested = websocket.query_params.get("session", "")
        if requested:
            if requested.startswith("id:"):
                ok = state.task_manager.switch_session(session_id=requested[3:])
            elif requested.startswith("name:"):
                ok = state.task_manager.switch_session(session_name=requested[5:])
            else:
                ok = False
            if not ok:
                switch_note = ("Could not switch session — a task is still running. "
                               "Connected to the current session instead.")

        resumed = sessions.recent_messages(state.task_manager.session_id)
        for m in resumed:
            try:
                await websocket.send_json({"type": "transcript",
                                           "role": m["role"], "text": m["text"]})
            except Exception:
                break

        opening = None
        pend = state.task_manager.pending
        if pend:
            age = time.time() - pend.get("ts", time.time())
            if age < 2 * 3600:
                lead = ("[SYSTEM] The user just reconnected after stepping away. "
                        "Greet them briefly, then tell them this finished result right away: ")
            else:
                hours = int(age // 3600)
                when = "{} hours ago".format(hours) if hours < 48 else "{} days ago".format(
                    hours // 24
                )
                lead = ("[SYSTEM] The user reconnected after a while. Greet them, mention "
                        "that Claude finished a task {} , and give them the result: ").format(
                            when
                        )
            opening = lead + pend["summary"]

        def on_session_end(voice_log):
            text = "\n".join("{}: {}".format(r, t) for r, t in voice_log)
            sid = state.task_manager.session_id

            def distill():
                out = summarizer.summarize_conversation(text)
                note = out.get("note") if isinstance(out, dict) else out
                title = out.get("title") if isinstance(out, dict) else ""
                try:
                    if note:
                        stamp = time.strftime("%Y-%m-%d %H:%M")
                        callermem.append_voice_note(sid, "{} -- {}".format(stamp, note))
                    if title:
                        callermem.save(sid, title=title)
                    if (note or title) and state.task_manager.session_id == sid:
                        state.task_manager.voice_notes = callermem.load(sid)["voice_notes"]
                except Exception as e:
                    print("[server] voice note save failed: {}".format(e))

            threading.Thread(target=distill, daemon=True).start()

        def on_remember(note):
            sid = state.task_manager.session_id
            stamp = time.strftime("%Y-%m-%d %H:%M")
            callermem.append_voice_note(
                sid, "{} -- user asked to remember: {}".format(stamp, note)
            )
            if state.task_manager.session_id == sid:
                state.task_manager.voice_notes = callermem.load(sid)["voice_notes"]

        session = GeminiLiveSession(
            api_key=state.api_key,
            system_prompt=build_system_prompt(state, resumed=resumed,
                                              suppress_pending=bool(opening)),
            task_manager=state.task_manager,
            send_to_browser=websocket.send_json,
            model=state.model,
            on_ready=state.task_manager.take_pending,
            show_exchange=state.show_exchange,
            opening=opening,
            on_session_end=on_session_end,
            on_remember=on_remember,
        )
        state.session_holder["session"] = session

        if switch_note:
            try:
                await websocket.send_json({"type": "error", "message": switch_note})
            except Exception:
                pass

        async def browser_messages():
            try:
                while True:
                    yield await websocket.receive_json()
            except WebSocketDisconnect:
                return

        try:
            await session.run(browser_messages())
        except Exception as e:
            print("[server] session error: {!r}".format(e))
            try:
                await websocket.send_json({"type": "error",
                                           "message": "Session failed: {}".format(e)})
            except Exception:
                pass
        finally:
            if state.session_holder["session"] is session:
                state.session_holder["session"] = None
            try:
                await websocket.close()
            except Exception:
                pass

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
