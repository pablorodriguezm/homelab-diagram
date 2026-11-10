#!/usr/bin/env python3
"""Download the third-party service icons into icons/.

They are not part of this repo (they belong to their projects). Source: the
homarr-labs/dashboard-icons collection via jsDelivr. The own glyph-*.svg icons
are already in icons/.

Usage: python3 fetch_icons.py
"""
import pathlib
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
ICONS = ROOT / "icons"
# tried in order; raw GitHub is the fallback when jsDelivr refuses a file
SOURCES = [
    "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/{}.svg",
    "https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/{}.svg",
]

# local file name (as used in inventory.yaml / build.py) -> dashboard-icons name
ICON_MAP = {
    "unifi": "unifi", "homeassistant": "home-assistant", "raspberrypi": "raspberry-pi",
    "synology": "synology-dsm", "casaos": "casaos", "dell": "dell-light", "apple": "apple-light",
    "shelly": "shelly", "teslamate": "teslamate", "portainer": "portainer", "pihole": "pi-hole",
    "immich": "immich", "freshrss": "freshrss", "karakeep": "karakeep", "outline-l": "outline-light",
    "bentopdf": "bentopdf", "meilisearch": "meilisearch", "postgres": "postgresql", "redis": "redis",
    "chrome": "google-chrome", "grafana": "grafana", "mosquitto": "mosquitto", "jetkvm": "jetkvm",
    "docker": "docker", "plex": "plex", "wireguard": "wireguard", "hp": "hp",
}

# dark icons without a light variant: recolor them for the dark background
RECOLOR = {}


def download(name, tries=3):
    """Fetch one icon from the first source that delivers it; retry with a short pause."""
    last = None
    for url in SOURCES:
        req = urllib.request.Request(url.format(name), headers={"User-Agent": "homelab-diagram/1.0"})
        for attempt in range(tries):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.read().decode("utf-8")
            except Exception as e:
                last = e
                if getattr(e, "code", None) in (403, 404):   # this source won't have it, try the next
                    break
                time.sleep(2 * (attempt + 1))
    raise last


def main():
    ICONS.mkdir(exist_ok=True)
    failed = []
    for local, remote in ICON_MAP.items():
        time.sleep(0.2)                   # be gentle with the CDN
        try:
            (ICONS / f"{local}.svg").write_text(download(remote))
        except Exception as e:  # keep going, report at the end
            failed.append(f"{local} ({remote}): {e}")
    for local, (remote, old, new) in RECOLOR.items():
        try:
            (ICONS / f"{local}.svg").write_text(download(remote).replace(old, new))
        except Exception as e:
            failed.append(f"{local} ({remote}): {e}")


    print(f"icons in {ICONS}: {len(list(ICONS.glob('*.svg')))}")
    if failed:
        print("failed:\n  " + "\n  ".join(failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
