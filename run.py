"""Entrypoint for process managers that run a plain Python script directly,
rather than invoking `uvicorn` as a separate CLI command — e.g. Pyker:

    pyker start ergasia-digest run.py --venv ./.venv

See README.md for other deployment options (systemd, Docker).

Loads .env itself: process managers like Pyker have no declarative
env-file mechanism the way systemd (EnvironmentFile=) or docker-compose
(env_file:) do, so relying on the invoking shell to already have
API_KEY/PORT exported would be fragile.
"""

import os

from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402 -- must import after load_dotenv() populates os.environ

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
