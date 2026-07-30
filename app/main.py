import os
import asyncio
from fastapi import FastAPI, Request
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlmodel import Session, select

from app.database import create_db_and_tables, engine
from app.agent import StoreAgentOrchestrator
from app.models import ProcessedUpdate
from app.logger import setup_logger

log = setup_logger("webhook")

load_dotenv()

app = FastAPI(title="Supermarket Ops Agent")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PENDING_FILES = {}  # chat_id → file_path, cleaned after each webhook call


async def _transcribe_voice(voice: dict) -> str | None:
    """Download a Telegram voice note and transcribe via OpenAI Whisper."""
    from tempfile import NamedTemporaryFile

    if not BOT_TOKEN or not os.getenv("OPENAI_API_KEY"):
        return None

    try:
        import httpx

        # 1. Get file path from Telegram
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": voice["file_id"]},
                timeout=15.0,
            )
            file_info = resp.json()
            file_path = file_info["result"]["file_path"]

            # 2. Download the OGG file
            dl_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            resp = await client.get(dl_url, timeout=30.0)
            audio_bytes = resp.content

        # 3. Save to temp file for Whisper upload
        with NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # 4. Transcribe via OpenAI Whisper
        from openai import OpenAI

        openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with open(tmp_path, "rb") as f:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1", file=f, response_format="text"
            )

        os.unlink(tmp_path)
        return transcript.strip() if transcript else None

    except Exception as e:
        log.error(f"Voice transcription error: {e}")
        return None


async def send_typing(chat_id: int):
    """Fire-and-forget typing indicator via Telegram sendChatAction."""
    if not BOT_TOKEN:
        return
    import httpx
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "action": "typing"})


async def _keep_typing(chat_id: int):
    """Refresh typing indicator every 4s while the LLM processes."""
    try:
        while True:
            await asyncio.sleep(4)
            await send_typing(chat_id)
    except asyncio.CancelledError:
        pass

# To keep it simple without full python-telegram-bot webhook boilerplate,
# we can just receive the Telegram update JSON directly.
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict = None

@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    log.info("Database tables ensured.")

@app.get("/")
def read_root():
    return {"status": "Supermarket Ops Agent is running!"}

@app.post("/webhook")
async def telegram_webhook(update: Request):
    """
    Receives messages from Telegram.
    You must set this webhook URL in Telegram API:
    https://api.telegram.org/bot<TOKEN>/setWebhook?url=<YOUR_DOMAIN>/webhook
    """
    data = await update.json()
    update_id = data.get("update_id")

    # Idempotency check: if Telegram redelivers the same update, return cached reply
    if update_id is not None:
        with Session(engine) as session:
            cached = session.exec(
                select(ProcessedUpdate).where(ProcessedUpdate.update_id == update_id)
            ).first()
            if cached:
                log.info(f"Duplicate update_id={update_id}, returning cached reply")
                return {"status": "ok", "reply": cached.reply, "cached": True}

    # Safely extract message and chat_id
    message = data.get("message")
    if not message:
        return {"status": "ok", "msg": "No message body"}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")
    voice = message.get("voice")

    if not chat_id:
        return {"status": "ok", "msg": "Missing chat_id"}

    # Voice note → transcribe via Whisper
    if voice and not text:
        text = await _transcribe_voice(voice)
        if not text:
            return {"status": "ok", "msg": "Voice transcription failed"}
        log.info(f"chat_id={chat_id} | voice transcribed → '{text[:100]}'")

    if not text:
        return {"status": "ok", "msg": "Missing text"}

    log.info(f"chat_id={chat_id} | text='{text[:100]}'")

    # Send typing indicator immediately; keep refreshing every 4s during LLM call
    await send_typing(chat_id)
    typing_task = asyncio.create_task(_keep_typing(chat_id))

    # 1. Instantiate the multi-agent orchestrator for this specific chat_id
    orchestrator = StoreAgentOrchestrator(chat_id=chat_id)

    # 2. Get the LLM's response (blocking call, run in thread to not starve typing loop)
    try:
        reply = await asyncio.to_thread(orchestrator.handle_message, text)
    except Exception as e:
        reply = f"System Error: {str(e)}"
    finally:
        typing_task.cancel()

    # 3. Cache the result for idempotency (even errors — consistent on retry)
    if update_id is not None:
        with Session(engine) as session:
            try:
                session.add(ProcessedUpdate(update_id=update_id, reply=reply))
                session.commit()
            except Exception:
                session.rollback()
                log.info(f"update_id={update_id} already cached by concurrent request")

    # 4. Send the response back to Telegram
    if BOT_TOKEN:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": reply}
            )

            # If a tool generated a file, send it as a document
            pending = PENDING_FILES.pop(chat_id, None)
            if pending:
                ext = os.path.splitext(pending)[1]
                mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation" if ext == ".pptx" else "application/pdf"
                with open(pending, "rb") as fobj:
                    files = {"document": (os.path.basename(pending), fobj, mime)}
                    await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                        data={"chat_id": chat_id}, files=files, timeout=30.0
                    )

    return {"status": "ok", "reply": reply}
