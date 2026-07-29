import os
from fastapi import FastAPI, Request
from pydantic import BaseModel
from dotenv import load_dotenv

from app.database import create_db_and_tables
from app.agent import StoreAgentOrchestrator

load_dotenv()

app = FastAPI(title="Supermarket Ops Agent")

# To keep it simple without full python-telegram-bot webhook boilerplate,
# we can just receive the Telegram update JSON directly.
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict = None

@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    print("Database tables ensured.")

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
    
    # Safely extract message and chat_id
    message = data.get("message")
    if not message:
        return {"status": "ok", "msg": "No message body"}
        
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")
    
    if not chat_id or not text:
        return {"status": "ok", "msg": "Missing chat_id or text"}
        
    print(f"Received message from {chat_id}: {text}")
    
    # 1. Instantiate the multi-agent orchestrator for this specific chat_id
    orchestrator = StoreAgentOrchestrator(chat_id=chat_id)
    
    # 2. Get the LLM's response (this blocks while tools are called)
    try:
        reply = orchestrator.handle_message(text)
    except Exception as e:
        reply = f"System Error: {str(e)}"
        
    # 3. Send the response back to Telegram
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if bot_token:
        import httpx
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": reply
        }
        # Fire and forget response
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload)
            
    return {"status": "ok", "reply": reply}
