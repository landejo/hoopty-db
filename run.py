"""Start the local Hoopty Scout server:  .venv/bin/python run.py"""
from __future__ import annotations

import uvicorn

from scout.config import CONFIG

if __name__ == "__main__":
    uvicorn.run("scout.server:app", host="127.0.0.1", port=CONFIG.port, reload=False)
