import os
import json
import uuid
import asyncio
from contextlib import asynccontextmanager

from web_server import app

from dotenv import load_dotenv
from google import genai

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

from logger import write_log


# ==========================================================
# 1. ENVIRONMENT VARIABLES
# ==========================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Locally this will be:
# http://127.0.0.1:8000
#
# After deployment we will change it to the Cloud Run URL.
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://127.0.0.1:8000"
)

if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN not found"
    )

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY not found"
    )


# ==========================================================
# 2. GEMINI
# ==========================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ==========================================================
# 3. CONVERSATION MEMORY
# ==========================================================

conversation_history = {}


# ==========================================================
# 4. GEMINI DATA ANALYST
# ==========================================================

def ask_gemini(conversation):

    prompt = f"""
You are a careful data analyst.

Solve the user's data-analysis question accurately.

The conversation may contain multiple messages.

The LAST message is the current question.

Earlier messages may contain data or instructions needed
to answer the final message.

RULES:

1. Read the complete conversation.

2. Answer the LAST message.

3. Use previous messages when necessary.

4. The user may specify an exact JSON response format.

5. Determine exactly what belongs inside the outer
   "answer" field.

6. Preserve that requested structure exactly.

7. Return ONLY valid JSON.

8. Your output must contain exactly one top-level key:
   "result"

9. Put the value that belongs inside "answer"
   into "result".

10. Do NOT include log_url.

11. Do NOT use Markdown.

12. Do NOT add explanations.

13. Perform calculations carefully.

14. Do not invent data.


EXAMPLE:

If the requested response is:

{{"answer":{{"state":"<state>"}},"log_url":"<URL>"}}

and the answer is Assam, return:

{{"result":{{"state":"Assam"}}}}


CONVERSATION:

{conversation}
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt
    )

    return response.text


# ==========================================================
# 5. TELEGRAM MESSAGE HANDLER
# ==========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.message.text:
        return

    question = update.message.text
    chat_id = update.effective_chat.id

    # Unique log for this run
    run_id = str(uuid.uuid4())

    print()
    print("=" * 60)
    print("NEW RUN:", run_id)
    print("=" * 60)
    print(question)

    # ------------------------------------------------------
    # Log incoming question
    # ------------------------------------------------------

    write_log(
        run_id,
        "question",
        {
            "text": question
        }
    )

    try:

        # --------------------------------------------------
        # Conversation memory
        # --------------------------------------------------

        if chat_id not in conversation_history:
            conversation_history[chat_id] = []

        conversation_history[chat_id].append(
            question
        )

        conversation_history[chat_id] = (
            conversation_history[chat_id][-10:]
        )

        full_conversation = "\n\n".join(
            f"Message {i + 1}:\n{message}"
            for i, message in enumerate(
                conversation_history[chat_id]
            )
        )

        # --------------------------------------------------
        # Log conversation
        # --------------------------------------------------

        write_log(
            run_id,
            "conversation",
            {
                "messages":
                    conversation_history[chat_id]
            }
        )

        # --------------------------------------------------
        # Log model call
        # --------------------------------------------------

        write_log(
            run_id,
            "llm_call",
            {
                "provider": "Google Gemini",
                "model": "gemini-3.6-flash"
            }
        )

        # --------------------------------------------------
        # Gemini
        # --------------------------------------------------

        ai_response = await asyncio.to_thread(
            ask_gemini,
            full_conversation
        )

        print()
        print("Gemini response:")
        print(ai_response)

        # --------------------------------------------------
        # Log Gemini response
        # --------------------------------------------------

        write_log(
            run_id,
            "llm_response",
            {
                "raw_response": ai_response
            }
        )

        # --------------------------------------------------
        # Parse JSON
        # --------------------------------------------------

        parsed = json.loads(ai_response)

        if "result" not in parsed:
            raise ValueError(
                "Gemini response missing result"
            )

        answer = parsed["result"]

        write_log(
            run_id,
            "parsed_answer",
            {
                "answer": answer
            }
        )

        # --------------------------------------------------
        # REAL LOG URL
        # --------------------------------------------------

        log_url = (
            f"{PUBLIC_BASE_URL.rstrip('/')}"
            f"/logs/{run_id}.jsonl"
        )

        # --------------------------------------------------
        # Final assignment response
        # --------------------------------------------------

        final_response = {
            "answer": answer,
            "log_url": log_url
        }

        reply = json.dumps(
            final_response,
            separators=(",", ":"),
            ensure_ascii=False
        )

        # Validate before sending
        json.loads(reply)

        if set(final_response.keys()) != {
            "answer",
            "log_url"
        }:
            raise ValueError(
                "Incorrect final response keys"
            )

        write_log(
            run_id,
            "final_response",
            final_response
        )

        print()
        print("Final response:")
        print(reply)

    except Exception as error:

        print()
        print("ERROR:")
        print(type(error).__name__)
        print(error)

        write_log(
            run_id,
            "error",
            {
                "type": type(error).__name__,
                "message": str(error)
            }
        )

        log_url = (
            f"{PUBLIC_BASE_URL.rstrip('/')}"
            f"/logs/{run_id}.jsonl"
        )

        final_response = {
            "answer": {
                "error": "Unable to process question"
            },
            "log_url": log_url
        }

        reply = json.dumps(
            final_response,
            separators=(",", ":"),
            ensure_ascii=False
        )

        write_log(
            run_id,
            "final_response",
            final_response
        )

    # Exactly ONE Telegram message
    await update.message.reply_text(
        reply
    )


# ==========================================================
# 6. TELEGRAM APPLICATION
# ==========================================================

telegram_app = (
    Application
    .builder()
    .token(TELEGRAM_BOT_TOKEN)
    .build()
)

telegram_app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    )
)


# ==========================================================
# 7. START/STOP TELEGRAM WITH FASTAPI
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    print("Starting Telegram bot...")

    await telegram_app.initialize()
    await telegram_app.start()

    await telegram_app.updater.start_polling(
        drop_pending_updates=True
    )

    print("Telegram bot started.")

    yield

    print("Stopping Telegram bot...")

    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()


# ==========================================================
# 8. FASTAPI APPLICATION
# ==========================================================

app = FastAPI(
    title="TDS Data Analyst Telegram Bot",
    lifespan=lifespan
)


# ==========================================================
# 9. HEALTH CHECK
# ==========================================================

@app.get("/")
def home():

    return {
        "status": "ok",
        "service": "TDS Data Analyst Telegram Bot"
    }


# ==========================================================
# 10. PUBLIC JSONL LOGS
# ==========================================================

@app.get("/logs/{filename}")
def get_log(filename: str):

    if not filename.endswith(".jsonl"):
        raise HTTPException(
            status_code=400,
            detail="Only JSONL files are allowed"
        )

    safe_filename = os.path.basename(
        filename
    )

    file_path = os.path.join(
        "logs",
        safe_filename
    )

    if not os.path.isfile(file_path):
        raise HTTPException(
            status_code=404,
            detail="Log file not found"
        )

    return FileResponse(
        path=file_path,
        media_type="application/x-ndjson"
    )