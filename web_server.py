import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from bot import create_telegram_application


# ==========================================================
# TELEGRAM APPLICATION
# ==========================================================

telegram_app = None


# ==========================================================
# FASTAPI LIFESPAN
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    global telegram_app

    print("Starting Telegram bot...")

    telegram_app = create_telegram_application()

    await telegram_app.initialize()
    await telegram_app.start()

    if telegram_app.updater is not None:
        await telegram_app.updater.start_polling()

    print("Telegram bot started.")

    yield

    print("Stopping Telegram bot...")

    if telegram_app is not None:

        if telegram_app.updater is not None:
            await telegram_app.updater.stop()

        await telegram_app.stop()
        await telegram_app.shutdown()

    print("Telegram bot stopped.")


# ==========================================================
# FASTAPI APPLICATION
# ==========================================================

app = FastAPI(
    title="TDS Data Analyst Bot",
    lifespan=lifespan
)


# ==========================================================
# HOME PAGE
# ==========================================================

@app.get("/")
def home():

    return {
        "status": "ok",
        "message": "TDS Data Analyst Telegram Bot is running"
    }


# ==========================================================
# SERVE JSONL LOG FILES
# ==========================================================

@app.get("/logs/{filename}")
def get_log(filename: str):

    # Only allow JSONL files
    if not filename.endswith(".jsonl"):
        raise HTTPException(
            status_code=400,
            detail="Only JSONL files are allowed"
        )

    # Prevent path traversal
    safe_filename = os.path.basename(filename)

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
        media_type="application/x-ndjson",
        filename=safe_filename
    )