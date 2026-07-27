import os
import json
import uuid

from dotenv import load_dotenv
from google import genai

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

from logger import write_log


# ==========================================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==========================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN not found in environment variables"
    )

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY not found in environment variables"
    )


# ==========================================================
# 2. PUBLIC CLOUD RUN URL
# ==========================================================

PUBLIC_BASE_URL = (
    "https://tds-data-analyst-bot-217834432048."
    "europe-west1.run.app"
)


# ==========================================================
# 3. CONNECT TO GEMINI
# ==========================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ==========================================================
# 4. CONVERSATION MEMORY
# ==========================================================

conversation_history = {}


# ==========================================================
# 5. ASK GEMINI
# ==========================================================

def ask_gemini(conversation):

    prompt = f"""
You are a careful data analyst.

Your job is to solve the user's data-analysis question
accurately.

The text below may contain multiple messages from the same
conversation.

The LAST message is the current question.

Earlier messages may contain data, instructions, definitions,
or other information required to answer the last message.

IMPORTANT RULES:

1. Read the entire conversation carefully.

2. Use information from previous messages when necessary.

3. Answer the LAST message.

4. The user may specify an exact JSON response format.

5. Determine exactly what value should go inside the outer
   "answer" field.

6. Preserve the requested answer structure exactly.

7. Return ONLY valid JSON.

8. Your response must contain exactly one top-level key:
   "result"

9. Put the value that belongs inside the user's "answer"
   field into "result".

10. Do NOT include "log_url".

11. Do NOT use Markdown.

12. Do NOT use JSON code fences.

13. Do NOT add explanations before or after the JSON.

14. Perform calculations carefully.

15. Do not invent data.


EXAMPLE 1

Requested final response:

{{"answer":{{"state":"<state name>"}},"log_url":"<URL>"}}

Correct state: Assam

Return:

{{"result":{{"state":"Assam"}}}}


EXAMPLE 2

Requested final response:

{{"answer":{{"mean":"<number>"}},"log_url":"<URL>"}}

Correct mean: 25

Return:

{{"result":{{"mean":25}}}}


EXAMPLE 3

Requested final response:

{{"answer":["A","B"],"log_url":"<URL>"}}

Return:

{{"result":["A","B"]}}


CONVERSATION:

{conversation}
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt
    )

    return response.text


# ==========================================================
# 6. HANDLE TELEGRAM MESSAGE
# ==========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.message.text:
        return

    question = update.message.text
    chat_id = update.effective_chat.id

    # ------------------------------------------------------
    # Generate unique ID for this run
    # ------------------------------------------------------

    run_id = str(uuid.uuid4())

    # Public log URL for this run
    log_url = (
        f"{PUBLIC_BASE_URL}/logs/{run_id}.jsonl"
    )

    print()
    print("=" * 60)
    print("NEW RUN")
    print("Run ID:", run_id)
    print("=" * 60)

    print()
    print("Received question:")
    print(question)

    # ------------------------------------------------------
    # LOG 1: Incoming question
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
        # Create conversation history
        # --------------------------------------------------

        if chat_id not in conversation_history:
            conversation_history[chat_id] = []

        conversation_history[chat_id].append(
            question
        )

        # Keep latest 10 messages
        conversation_history[chat_id] = (
            conversation_history[chat_id][-10:]
        )

        # --------------------------------------------------
        # Build full conversation
        # --------------------------------------------------

        full_conversation = "\n\n".join(
            f"Message {i + 1}:\n{message}"
            for i, message in enumerate(
                conversation_history[chat_id]
            )
        )

        print()
        print("Conversation sent to Gemini:")
        print(full_conversation)

        # --------------------------------------------------
        # LOG 2: Conversation/context
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
        # LOG 3: LLM call
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
        # Ask Gemini
        # --------------------------------------------------

        ai_response = ask_gemini(
            full_conversation
        )

        print()
        print("Gemini response:")
        print(ai_response)

        # --------------------------------------------------
        # LOG 4: Raw Gemini response
        # --------------------------------------------------

        write_log(
            run_id,
            "llm_response",
            {
                "raw_response": ai_response
            }
        )

        # --------------------------------------------------
        # Parse Gemini JSON
        # --------------------------------------------------

        parsed = json.loads(
            ai_response
        )

        if "result" not in parsed:
            raise ValueError(
                "Gemini response does not contain 'result'"
            )

        answer = parsed["result"]

        # --------------------------------------------------
        # LOG 5: Parsed answer
        # --------------------------------------------------

        write_log(
            run_id,
            "parsed_answer",
            {
                "answer": answer
            }
        )

        # --------------------------------------------------
        # Build assignment response
        # --------------------------------------------------

        final_response = {
            "answer": answer,
            "log_url": log_url
        }

        # --------------------------------------------------
        # Convert to compact JSON
        # --------------------------------------------------

        reply = json.dumps(
            final_response,
            separators=(",", ":"),
            ensure_ascii=False
        )

        # --------------------------------------------------
        # Validate JSON
        # --------------------------------------------------

        json.loads(reply)

        # Ensure exactly two outer keys
        if set(final_response.keys()) != {
            "answer",
            "log_url"
        }:
            raise ValueError(
                "Final response contains incorrect keys"
            )

        # --------------------------------------------------
        # LOG 6: Final response
        # --------------------------------------------------

        write_log(
            run_id,
            "final_response",
            final_response
        )

        print()
        print("Final Telegram response:")
        print(reply)

        print()
        print(
            f"Log saved to: logs\\{run_id}.jsonl"
        )

    except Exception as error:

        print()
        print("ERROR:")
        print(type(error).__name__)
        print(error)

        # --------------------------------------------------
        # Log the error
        # --------------------------------------------------

        write_log(
            run_id,
            "error",
            {
                "type": type(error).__name__,
                "message": str(error)
            }
        )

        # --------------------------------------------------
        # Build valid JSON error response
        # --------------------------------------------------

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

        # --------------------------------------------------
        # Log final error response
        # --------------------------------------------------

        write_log(
            run_id,
            "final_response",
            final_response
        )

        print()
        print("Final error response:")
        print(reply)

    # ------------------------------------------------------
    # Send exactly one JSON object to Telegram
    # ------------------------------------------------------

    await update.message.reply_text(
        reply
    )


# ==========================================================
# 7. CREATE TELEGRAM APPLICATION
# ==========================================================

def create_telegram_application():

    application = (
        Application
        .builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    return application


# ==========================================================
# 8. START BOT LOCALLY
# ==========================================================

def main():

    print(
        "Starting Ash Data Analyst bot..."
    )

    application = create_telegram_application()

    print(
        "Bot is running. Press Ctrl+C to stop."
    )

    application.run_polling()


# ==========================================================
# 9. RUN PROGRAM
# ==========================================================

if __name__ == "__main__":
    main()