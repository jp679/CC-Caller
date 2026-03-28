import queue
from fastapi import FastAPI, Request


def create_app(transcript_queue: queue.Queue) -> FastAPI:
    app = FastAPI()

    @app.get("/webhook")
    async def webhook_health():
        return {"status": "healthy"}

    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.json()
        print(f"[webhook] Received event: {body.get('message', {}).get('type', 'unknown')}")
        message = body.get("message", {})

        if message.get("type") != "end-of-call-report":
            return {"status": "ignored"}

        artifact = message.get("artifact", {})
        messages = artifact.get("messages", [])

        user_messages = [
            msg["message"] for msg in messages
            if msg.get("role") == "user"
        ]

        if user_messages:
            transcript = " ".join(user_messages)
            transcript_queue.put(transcript)

        return {"status": "ok"}

    return app
