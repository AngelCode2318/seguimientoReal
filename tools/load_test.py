"""30 real WebSocket clients against a disposable, local application instance."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import threading
import time

import requests
import socketio as socketio_client
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from occ import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--https-proxy", action="store_true", help="Simula la terminación TLS del proxy con tráfico sintético en loopback.")
    options = parser.parse_args()
    if not 1 <= options.clients <= 30 or options.rounds < 1:
        parser.error("Usa de 1 a 30 clientes y al menos una ronda.")
    logging.getLogger("werkzeug").setLevel(logging.CRITICAL)
    sockets, http_sessions, state = [], [], []
    with tempfile.TemporaryDirectory(prefix="occ-load-") as temporary:
        previous_env = os.environ.get("APP_ENV")
        os.environ["APP_ENV"] = "production" if options.https_proxy else "development"
        try:
            app = create_app({
                "TESTING": True, "SECRET_KEY": "disposable-load-test",
                "DATABASE_PATH": str(Path(temporary) / "club.sqlite3"),
                "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000",
            })
        finally:
            if previous_env is None:
                os.environ.pop("APP_ENV", None)
            else:
                os.environ["APP_ENV"] = previous_env
        server = make_server("127.0.0.1", 0, app, threaded=True)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        url = f"http://127.0.0.1:{server.server_port}"
        latencies = []
        try:
            for index in range(options.clients):
                http = requests.Session()
                http.trust_env = False
                if options.https_proxy:
                    http.headers.update({"X-Forwarded-Proto": "https", "Origin": url.replace("http:", "https:")})
                http_sessions.append(http)
                html = http.get(url + "/register", timeout=10).text
                if options.https_proxy:
                    assert all(cookie.secure for cookie in http.cookies)
                    for cookie in http.cookies:
                        cookie.secure = False
                csrf = re.search(r'name="csrf-token" content="([^"]+)"', html)[1]
                response = http.post(url + "/api/register", json={
                    "email": f"car{index}@example.com", "password": "synthetic-load-test",
                    "alias": f"Auto {index+1}", "car": "Mustang", "year": 1967,
                }, headers={"X-CSRF-Token": csrf}, timeout=10)
                assert response.status_code == 201, response.text
                if options.https_proxy:
                    # Only synthetic cookies on our own loopback test server.
                    # Real deployments/browser traffic use HTTPS and Secure cookies.
                    assert all(cookie.secure for cookie in http.cookies)
                    for cookie in http.cookies:
                        cookie.secure = False
                current = {"positions": {}, "updates": 0}
                state.append(current)

                def updated(person, current=current):
                    current["positions"][person["user_id"]] = person
                    current["updates"] += 1

                extra_options = {"origin": url.replace("http:", "https:")} if options.https_proxy else {}
                sio = socketio_client.Client(http_session=http, reconnection=False, websocket_extra_options=extra_options)
                sio.on("participant:updated", updated)
                sio.connect(url, headers={"X-Forwarded-Proto": "https"} if options.https_proxy else {},
                    auth={"csrf_token": response.json()["csrf_token"]}, transports=["websocket"], wait_timeout=10)
                assert sio.transport() == "websocket"
                assert sio.call("share:start", {}, timeout=10)["ok"]
                sockets.append(sio)

            def send(item):
                index, sio = item
                started = time.perf_counter()
                result = sio.call("position:update", {"latitude": 19.43 + index * .001,
                    "longitude": -99.13, "accuracy": 6, "captured_at": time.time()}, timeout=10)
                assert result["ok"], result
                return (time.perf_counter() - started) * 1000

            with ThreadPoolExecutor(max_workers=options.clients) as pool:
                for round_index in range(options.rounds):
                    if round_index:
                        time.sleep(5.1)
                    latencies.extend(pool.map(send, enumerate(sockets)))
            deadline = time.monotonic() + 5
            expected = options.clients * options.rounds
            while time.monotonic() < deadline and any(client["updates"] < expected for client in state):
                time.sleep(.05)
            assert all(len(client["positions"]) == options.clients for client in state)
            assert all(client["updates"] == expected for client in state), [client["updates"] for client in state]
            people = http_sessions[0].get(url + "/api/participants", timeout=10).json()["participants"]
            assert len(people) == len({person["user_id"] for person in people}) == options.clients
            report = {"clients": options.clients, "transport": "websocket", "https_proxy_simulated": options.https_proxy, "rounds": options.rounds,
                "interval_seconds": 5.1, "accepted_positions": len(latencies),
                "updates_per_viewer": expected, "unique_markers": len(people),
                "ack_median_ms": round(statistics.median(latencies), 1),
                "ack_max_ms": round(max(latencies), 1), "result": "passed"}
            print(json.dumps(report, indent=2), flush=True)
            if options.report:
                options.report.parent.mkdir(parents=True, exist_ok=True)
                options.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        finally:
            for sio in sockets:
                sio.disconnect()
            for http in http_sessions:
                http.close()
            server.shutdown()
            server.server_close()
            serving.join(timeout=5)


if __name__ == "__main__":
    main()
