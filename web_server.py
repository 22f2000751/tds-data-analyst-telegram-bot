import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


app = FastAPI(
    title="TDS Data Analyst Bot"
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

    # Security:
    # Only allow .jsonl files
    if not filename.endswith(".jsonl"):
        raise HTTPException(
            status_code=400,
            detail="Only JSONL files are allowed"
        )

    # Prevent someone requesting:
    # ../../secret-file
    safe_filename = os.path.basename(filename)

    file_path = os.path.join(
        "logs",
        safe_filename
    )

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail="Log file not found"
        )

    return FileResponse(
        path=file_path,
        media_type="application/x-ndjson",
        filename=safe_filename
    )