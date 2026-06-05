"""Launcher for the Chaplin Language Trainer web app.

Run directly (python web_chaplin.py) or via uvicorn:
    uvicorn app.main:app --host 127.0.0.1 --port 8765
"""
import uvicorn

from app.settings import get_settings


def main():
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
