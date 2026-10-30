"""Fetch pinned browser libraries. Not needed to run/build: assets are committed."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / "occ" / "static" / "vendor"
ASSETS = {
    "leaflet/leaflet.js": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
    "leaflet/leaflet.css": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
    "leaflet/LICENSE": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/LICENSE",
    "socket.io.min.js": "https://cdn.jsdelivr.net/npm/socket.io-client@4.8.1/dist/socket.io.min.js",
    "socket.io.LICENSE": "https://cdn.jsdelivr.net/npm/socket.io-client@4.8.1/LICENSE",
}
for name in ("marker-icon.png", "marker-icon-2x.png", "marker-shadow.png", "layers.png", "layers-2x.png"):
    ASSETS["leaflet/images/" + name] = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/images/" + name


def main():
    manifest = {}
    for relative, url in ASSETS.items():
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read()
        destination = ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        manifest[relative] = {"url": url, "sha256": hashlib.sha256(data).hexdigest()}
        print(relative)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
