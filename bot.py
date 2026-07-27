import os
import re
import io
import json
import uuid
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

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

MAX_HISTORY_MESSAGES = 10

# Prevent extremely large web pages / datasets from being
# inserted into the Gemini prompt.
MAX_DATASET_TEXT_LENGTH = 60000

# Network timeout
REQUEST_TIMEOUT = 20

# Maximum public file size we will download: 15 MB
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024


# ==========================================================
# 5. FIND PUBLIC URLS IN QUESTION
# ==========================================================

def extract_urls(text):

    urls = re.findall(
        r'https?://[^\s<>"\']+',
        text
    )

    cleaned_urls = []

    for url in urls:

        # Remove punctuation commonly attached to URLs
        url = url.rstrip(".,);]}")

        if url not in cleaned_urls:
            cleaned_urls.append(url)

    return cleaned_urls


# ==========================================================
# 6. DOWNLOAD PUBLIC URL
# ==========================================================

def download_public_url(url):

    headers = {
        "User-Agent":
            "Mozilla/5.0 TDS-Data-Analyst-Bot/1.0"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        stream=True
    )

    response.raise_for_status()

    # Check declared content length first
    content_length = response.headers.get(
        "Content-Length"
    )

    if content_length:

        try:
            if int(content_length) > MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    "Public file is too large to process"
                )
        except ValueError as error:
            if "too large" in str(error):
                raise

    # Download with a size limit
    chunks = []
    total_size = 0

    for chunk in response.iter_content(
        chunk_size=64 * 1024
    ):

        if not chunk:
            continue

        total_size += len(chunk)

        if total_size > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                "Public file is too large to process"
            )

        chunks.append(chunk)

    content = b"".join(chunks)

    content_type = (
        response.headers
        .get("Content-Type", "")
        .lower()
    )

    final_url = response.url

    return {
        "requested_url": url,
        "final_url": final_url,
        "content_type": content_type,
        "content": content,
        "size_bytes": len(content)
    }


# ==========================================================
# 7. CONVERT DATAFRAME TO USEFUL TEXT
# ==========================================================

def dataframe_to_context(df, label):

    # Avoid gigantic tables in the prompt.
    preview_rows = 200

    preview = df.head(
        preview_rows
    ).to_csv(
        index=False
    )

    context = (
        f"\nDATASET: {label}\n"
        f"Rows: {len(df)}\n"
        f"Columns: {len(df.columns)}\n"
        f"Column names: "
        f"{list(map(str, df.columns))}\n\n"
        f"DATA PREVIEW "
        f"(first {min(len(df), preview_rows)} rows):\n"
        f"{preview}\n"
    )

    return context


# ==========================================================
# 8. PARSE CSV
# ==========================================================

def parse_csv(content, label):

    # Try normal UTF-8 first.
    try:

        df = pd.read_csv(
            io.BytesIO(content)
        )

    except Exception:

        # Some government datasets use older encodings.
        text = content.decode(
            "latin-1",
            errors="replace"
        )

        df = pd.read_csv(
            io.StringIO(text)
        )

    return dataframe_to_context(
        df,
        label
    )


# ==========================================================
# 9. PARSE EXCEL
# ==========================================================

def parse_excel(content, label):

    excel_file = pd.ExcelFile(
        io.BytesIO(content)
    )

    contexts = []

    # Limit number of sheets placed into prompt.
    for sheet_name in excel_file.sheet_names[:5]:

        df = pd.read_excel(
            excel_file,
            sheet_name=sheet_name
        )

        contexts.append(
            dataframe_to_context(
                df,
                f"{label} / sheet: {sheet_name}"
            )
        )

    return "\n".join(contexts)


# ==========================================================
# 10. PARSE JSON
# ==========================================================

def parse_json_data(content, label):

    text = content.decode(
        "utf-8",
        errors="replace"
    )

    data = json.loads(text)

    pretty = json.dumps(
        data,
        ensure_ascii=False,
        indent=2
    )

    return (
        f"\nJSON DATASET: {label}\n"
        f"{pretty}\n"
    )


# ==========================================================
# 11. PARSE HTML / WEB PAGE
# ==========================================================

def parse_html(content, label):

    text = content.decode(
        "utf-8",
        errors="replace"
    )

    contexts = []

    # ------------------------------------------------------
    # Try HTML tables with pandas
    # ------------------------------------------------------

    try:

        tables = pd.read_html(
            io.StringIO(text)
        )

        for index, df in enumerate(
            tables[:10]
        ):

            contexts.append(
                dataframe_to_context(
                    df,
                    (
                        f"{label} / "
                        f"HTML table {index + 1}"
                    )
                )
            )

    except Exception:
        pass

    # ------------------------------------------------------
    # Also extract visible webpage text
    # ------------------------------------------------------

    soup = BeautifulSoup(
        text,
        "lxml"
    )

    for unwanted in soup(
        [
            "script",
            "style",
            "noscript"
        ]
    ):
        unwanted.decompose()

    page_text = soup.get_text(
        separator="\n",
        strip=True
    )

    page_text = re.sub(
        r"\n{3,}",
        "\n\n",
        page_text
    )

    if page_text:

        contexts.append(
            f"\nWEB PAGE TEXT: {label}\n"
            f"{page_text[:30000]}\n"
        )

    if not contexts:
        raise ValueError(
            "Could not extract useful data "
            "from web page"
        )

    return "\n".join(contexts)


# ==========================================================
# 12. PARSE PLAIN TEXT
# ==========================================================

def parse_text_data(content, label):

    text = content.decode(
        "utf-8",
        errors="replace"
    )

    return (
        f"\nTEXT DATA: {label}\n"
        f"{text}\n"
    )


# ==========================================================
# 13. DETECT AND PARSE PUBLIC DATA
# ==========================================================

def parse_downloaded_data(download):

    content = download["content"]

    content_type = download[
        "content_type"
    ]

    final_url = download[
        "final_url"
    ]

    parsed_path = urlparse(
        final_url
    ).path.lower()

    label = final_url

    # ------------------------------------------------------
    # CSV
    # ------------------------------------------------------

    if (
        parsed_path.endswith(".csv")
        or "text/csv" in content_type
        or "application/csv" in content_type
    ):

        return (
            "csv",
            parse_csv(
                content,
                label
            )
        )

    # ------------------------------------------------------
    # Excel
    # ------------------------------------------------------

    if (
        parsed_path.endswith(".xlsx")
        or parsed_path.endswith(".xls")
        or "spreadsheet" in content_type
        or "excel" in content_type
    ):

        return (
            "excel",
            parse_excel(
                content,
                label
            )
        )

    # ------------------------------------------------------
    # JSON
    # ------------------------------------------------------

    if (
        parsed_path.endswith(".json")
        or "application/json" in content_type
        or "text/json" in content_type
    ):

        return (
            "json",
            parse_json_data(
                content,
                label
            )
        )

    # ------------------------------------------------------
    # HTML
    # ------------------------------------------------------

    if (
        "text/html" in content_type
        or parsed_path.endswith(".html")
        or parsed_path.endswith(".htm")
    ):

        return (
            "html",
            parse_html(
                content,
                label
            )
        )

    # ------------------------------------------------------
    # Plain text
    # ------------------------------------------------------

    if (
        "text/plain" in content_type
        or parsed_path.endswith(".txt")
        or parsed_path.endswith(".tsv")
    ):

        return (
            "text",
            parse_text_data(
                content,
                label
            )
        )

    # ------------------------------------------------------
    # Fallback:
    # Try UTF-8 text / HTML
    # ------------------------------------------------------

    try:

        decoded = content.decode(
            "utf-8"
        )

        if (
            "<html" in decoded.lower()
            or "<table" in decoded.lower()
        ):

            return (
                "html",
                parse_html(
                    content,
                    label
                )
            )

        return (
            "text",
            parse_text_data(
                content,
                label
            )
        )

    except Exception:

        raise ValueError(
            "Unsupported public dataset format"
        )


# ==========================================================
# 14. RETRIEVE PUBLIC DATA FOR QUESTION
# ==========================================================

def retrieve_public_data(
    conversation,
    run_id
):

    urls = extract_urls(
        conversation
    )

    if not urls:
        return ""

    all_context = []

    # Avoid unexpectedly downloading many links from one
    # message.
    urls = urls[:5]

    for url in urls:

        write_log(
            run_id,
            "tool_call",
            {
                "tool": "public_url_fetch",
                "url": url
            }
        )

        try:

            download = download_public_url(
                url
            )

            data_type, context = (
                parse_downloaded_data(
                    download
                )
            )

            write_log(
                run_id,
                "tool_result",
                {
                    "tool":
                        "public_url_fetch",
                    "requested_url":
                        url,
                    "final_url":
                        download["final_url"],
                    "content_type":
                        download["content_type"],
                    "size_bytes":
                        download["size_bytes"],
                    "detected_data_type":
                        data_type,
                    "status":
                        "success"
                }
            )

            all_context.append(
                context
            )

        except Exception as error:

            write_log(
                run_id,
                "tool_result",
                {
                    "tool":
                        "public_url_fetch",
                    "url":
                        url,
                    "status":
                        "error",
                    "error_type":
                        type(error).__name__,
                    "error_message":
                        str(error)
                }
            )

            # Give Gemini information about the failed
            # retrieval instead of crashing the entire run.
            all_context.append(
                "\nPUBLIC DATA RETRIEVAL FAILURE\n"
                f"URL: {url}\n"
                f"Error: {type(error).__name__}: "
                f"{error}\n"
            )

    combined = "\n".join(
        all_context
    )

    return combined[
        :MAX_DATASET_TEXT_LENGTH
    ]


# ==========================================================
# 15. ASK GEMINI
# ==========================================================

def ask_gemini(
    conversation,
    retrieved_data=""
):

    prompt = f"""
You are a careful data analyst.

Your job is to solve the user's data-analysis question
accurately.

The text below may contain multiple messages from the same
conversation.

The LAST message is the current question.

Earlier messages may contain data, instructions, definitions,
URLs, or other information required to answer the last
message.

If PUBLICLY RETRIEVED DATA is provided below, use that data
as evidence for your analysis.

IMPORTANT RULES:

1. Read the entire conversation carefully.

2. Use information from previous messages when necessary.

3. Answer the LAST message.

4. Use retrieved public data when it is relevant.

5. Do not claim that you retrieved or verified data unless
   it actually appears in PUBLICLY RETRIEVED DATA.

6. The user may specify an exact JSON response format.

7. Determine exactly what value should go inside the outer
   "answer" field.

8. Preserve the requested answer structure exactly.

9. Return ONLY valid JSON.

10. Your response must contain exactly one top-level key:
    "result"

11. Put the value that belongs inside the user's "answer"
    field into "result".

12. Do NOT include "log_url".

13. Do NOT use Markdown.

14. Do NOT use JSON code fences.

15. Do NOT add explanations before or after the JSON.

16. Perform calculations carefully.

17. Do not invent data.

18. If the question provides data inline, analyse that data.

19. If retrieved public data is supplied, inspect the actual
    values rather than relying on memory.

20. Follow the exact field names, types, arrays, strings,
    numbers, and object structure requested by the user.


EXAMPLE 1

Requested final response:

{{"answer":{{"state":"<state name>"}},"log_url":"<URL>"}}

If analysis establishes that Assam is correct, return:

{{"result":{{"state":"Assam"}}}}


EXAMPLE 2

Requested final response:

{{"answer":{{"mean":"<number>"}},"log_url":"<URL>"}}

If the calculated mean is 25, return:

{{"result":{{"mean":25}}}}


EXAMPLE 3

Requested final response:

{{"answer":["A","B"],"log_url":"<URL>"}}

Return:

{{"result":["A","B"]}}


CONVERSATION:

{conversation}


PUBLICLY RETRIEVED DATA:

{retrieved_data if retrieved_data else "No public URL data was retrieved for this run."}
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt
    )

    return response.text


# ==========================================================
# 16. HANDLE TELEGRAM MESSAGE
# ==========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if (
        not update.message
        or not update.message.text
    ):
        return

    question = update.message.text

    chat_id = (
        update.effective_chat.id
    )

    # ------------------------------------------------------
    # Generate unique ID for this run
    # ------------------------------------------------------

    run_id = str(
        uuid.uuid4()
    )

    # ------------------------------------------------------
    # Public log URL for this run
    # ------------------------------------------------------

    log_url = (
        f"{PUBLIC_BASE_URL}/logs/"
        f"{run_id}.jsonl"
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
        # Conversation history
        # --------------------------------------------------

        if (
            chat_id
            not in conversation_history
        ):
            conversation_history[
                chat_id
            ] = []

        conversation_history[
            chat_id
        ].append(
            question
        )

        conversation_history[
            chat_id
        ] = conversation_history[
            chat_id
        ][
            -MAX_HISTORY_MESSAGES:
        ]

        # --------------------------------------------------
        # Build full conversation
        # --------------------------------------------------

        full_conversation = (
            "\n\n".join(
                (
                    f"Message {i + 1}:\n"
                    f"{message}"
                )
                for i, message
                in enumerate(
                    conversation_history[
                        chat_id
                    ]
                )
            )
        )

        print()
        print(
            "Conversation sent "
            "to analyst:"
        )
        print(
            full_conversation
        )

        # --------------------------------------------------
        # LOG 2: Conversation
        # --------------------------------------------------

        write_log(
            run_id,
            "conversation",
            {
                "messages":
                    conversation_history[
                        chat_id
                    ]
            }
        )

        # --------------------------------------------------
        # Retrieve public URLs appearing anywhere in the
        # short conversation.
        # --------------------------------------------------

        retrieved_data = (
            retrieve_public_data(
                full_conversation,
                run_id
            )
        )

        # --------------------------------------------------
        # Log retrieval summary
        # --------------------------------------------------

        write_log(
            run_id,
            "retrieval_summary",
            {
                "urls_found":
                    extract_urls(
                        full_conversation
                    ),
                "retrieved_context_chars":
                    len(
                        retrieved_data
                    )
            }
        )

        # --------------------------------------------------
        # LOG: LLM call
        # --------------------------------------------------

        write_log(
            run_id,
            "llm_call",
            {
                "provider":
                    "Google Gemini",
                "model":
                    "gemini-3.6-flash",
                "retrieved_public_data":
                    bool(
                        retrieved_data
                    )
            }
        )

        # --------------------------------------------------
        # Ask Gemini
        # --------------------------------------------------

        ai_response = ask_gemini(
            full_conversation,
            retrieved_data
        )

        print()
        print("Gemini response:")
        print(ai_response)

        # --------------------------------------------------
        # LOG: Raw Gemini response
        # --------------------------------------------------

        write_log(
            run_id,
            "llm_response",
            {
                "raw_response":
                    ai_response
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
                "Gemini response does not "
                "contain 'result'"
            )

        # Reject unexpected outer keys from Gemini.
        if set(parsed.keys()) != {
            "result"
        }:
            raise ValueError(
                "Gemini response contains "
                "unexpected top-level keys"
            )

        answer = parsed[
            "result"
        ]

        # --------------------------------------------------
        # LOG: Parsed answer
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
            "answer":
                answer,
            "log_url":
                log_url
        }

        # --------------------------------------------------
        # Validate exactly two outer keys
        # --------------------------------------------------

        if set(
            final_response.keys()
        ) != {
            "answer",
            "log_url"
        }:
            raise ValueError(
                "Final response contains "
                "incorrect keys"
            )

        # --------------------------------------------------
        # Convert to compact JSON
        # --------------------------------------------------

        reply = json.dumps(
            final_response,
            separators=(",", ":"),
            ensure_ascii=False
        )

        # Validate serialized JSON
        json.loads(
            reply
        )

        # --------------------------------------------------
        # LOG: Final response
        # --------------------------------------------------

        write_log(
            run_id,
            "final_response",
            final_response
        )

        print()
        print(
            "Final Telegram response:"
        )
        print(
            reply
        )

        print()
        print(
            f"Log saved to: "
            f"logs\\{run_id}.jsonl"
        )

    except Exception as error:

        print()
        print("ERROR:")
        print(
            type(error).__name__
        )
        print(
            error
        )

        # --------------------------------------------------
        # Log error
        # --------------------------------------------------

        write_log(
            run_id,
            "error",
            {
                "type":
                    type(error).__name__,
                "message":
                    str(error)
            }
        )

        # --------------------------------------------------
        # Valid assignment-shaped error response
        # --------------------------------------------------

        final_response = {
            "answer": {
                "error":
                    "Unable to process question"
            },
            "log_url":
                log_url
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

        print()
        print(
            "Final error response:"
        )
        print(
            reply
        )

    # ------------------------------------------------------
    # Send EXACTLY one JSON object to Telegram
    # ------------------------------------------------------

    await update.message.reply_text(
        reply
    )


# ==========================================================
# 17. CREATE TELEGRAM APPLICATION
# ==========================================================

def create_telegram_application():

    application = (
        Application
        .builder()
        .token(
            TELEGRAM_BOT_TOKEN
        )
        .build()
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    return application


# ==========================================================
# 18. START BOT LOCALLY
# ==========================================================

def main():

    print(
        "Starting Ash Data Analyst bot..."
    )

    application = (
        create_telegram_application()
    )

    print(
        "Bot is running. "
        "Press Ctrl+C to stop."
    )

    application.run_polling()


# ==========================================================
# 19. RUN PROGRAM
# ==========================================================

if __name__ == "__main__":
    main()