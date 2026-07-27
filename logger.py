import json
import os
from datetime import datetime, timezone


# Folder where logs will be stored
LOG_FOLDER = "logs"

# Create logs folder automatically
os.makedirs(LOG_FOLDER, exist_ok=True)


def write_log(run_id, event, data=None):
    """
    Write one JSON object as one line in a JSONL file.
    """

    log_file = os.path.join(
        LOG_FOLDER,
        f"{run_id}.jsonl"
    )

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event
    }

    if data is not None:
        record["data"] = data

    with open(
        log_file,
        "a",
        encoding="utf-8"
    ) as file:

        file.write(
            json.dumps(
                record,
                ensure_ascii=False
            )
            + "\n"
        )

    return log_file