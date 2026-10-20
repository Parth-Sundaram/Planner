"""Entry point: `gunicorn wsgi:app` (hosted) or `python wsgi.py` (local)."""
import os

from dotenv import load_dotenv

load_dotenv()

from planner.app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    # Local development over plain http: allow OAuth on localhost.
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    app.config["SESSION_COOKIE_SECURE"] = False
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5050")), debug=True)
