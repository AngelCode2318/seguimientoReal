"""Development entrypoint; Render uses the same app through Gunicorn."""
import os

from dotenv import load_dotenv

load_dotenv()

from occ import create_app  # noqa: E402

app = create_app()
socketio = app.extensions["socketio"]

if __name__ == "__main__":
    socketio.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        allow_unsafe_werkzeug=True,  # Local development only. Render uses Gunicorn.
    )

