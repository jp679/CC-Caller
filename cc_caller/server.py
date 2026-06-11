"""FastAPI server for the Gemini PWA: token-gated WS bridge, push, static."""
import hmac
import pathlib

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from cc_caller import push
from cc_caller.gemini_live import GeminiLiveSession

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"


class AppState:
    def __init__(self, token, task_manager, api_key, model, vapid_public_key,
                 base_system_prompt, subscriptions):
        self.token = token
        self.task_manager = task_manager
        self.api_key = api_key
        self.model = model
        self.vapid_public_key = vapid_public_key
        self.base_system_prompt = base_system_prompt
        self.subscriptions = subscriptions
        self.session_holder = {"session": None}


def build_system_prompt(state):
    """Base relay prompt + recent history + any pending (undelivered) result."""
    prompt = state.base_system_prompt
    if state.task_manager.history:
        prompt += ("\n\nRECENT CONVERSATION (results you already reported -- use these to "
                   "answer follow-ups WITHOUT calling askCodingAgent again):\n")
        for entry in state.task_manager.history[-5:]:
            prompt += "\nUser asked: {}\nResult: {}\n".format(
                entry["task"], entry["summary"][:500])
    if state.task_manager.pending:
        prompt += ("\n\nPENDING RESULT -- the user has not heard this yet. Open the "
                   "conversation by telling them: {}\n".format(
                       state.task_manager.pending["summary"]))
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
    async def index():
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    @app.get("/sw.js")
    async def service_worker():
        return Response((STATIC_DIR / "sw.js").read_text(),
                        media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})

    @app.get("/manifest.json")
    async def manifest():
        return Response((STATIC_DIR / "manifest.json").read_text(),
                        media_type="application/manifest+json")

    @app.get("/api/config")
    async def api_config(request: Request):
        require_token(request)
        return {"vapidPublicKey": state.vapid_public_key}

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

        session = GeminiLiveSession(
            api_key=state.api_key,
            system_prompt=build_system_prompt(state),
            task_manager=state.task_manager,
            send_to_browser=websocket.send_json,
            model=state.model,
            on_ready=state.task_manager.take_pending,
        )
        state.session_holder["session"] = session

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
        finally:
            if state.session_holder["session"] is session:
                state.session_holder["session"] = None
            try:
                await websocket.close()
            except Exception:
                pass

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
