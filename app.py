#!/usr/bin/env python3
"""
Västtrafik avgångsvisare — Flask-version.

Ombyggd från CGI-skriptet jsonhtml-kopia.py så att den går att:
  * lägga på GitHub (inga hemligheter i koden),
  * köra på PythonAnywhere eller annan WSGI-värd (Flask/WSGI i stället för
    print()-baserad CGI),
  * köra lokalt för test (flask run / python app.py).

Skillnader mot originalet:
  * client_id/client_secret läses från miljövariabler i stället för att
    stå hårdkodade i filen.
  * Sökvägar till cache-filer (active-token, stop-areas) är relativa till
    appens mapp i stället för hårdkodade /var/www/cgi-bin/...
  * All utskrift byggs upp som en HTML-sträng och skickas tillbaka som ett
    riktigt HTTP-svar via Flask, i stället för print() rakt ut i stdout.
  * Använder markupsafe.escape för användarinmatning som skrivs in i HTML
    (grundläggande skydd mot XSS via sökfältet).
"""

import json
import os
from collections import OrderedDict
from datetime import datetime
from urllib.parse import quote

from flask import Flask, Response, request
from markupsafe import escape
from oauthlib.oauth2 import BackendApplicationClient
from requests.auth import HTTPBasicAuth
from requests_oauthlib import OAuth2Session

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CLIENT_ID = os.environ.get("VT_CLIENT_ID")
CLIENT_SECRET = os.environ.get("VT_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise RuntimeError(
        "Miljövariablerna VT_CLIENT_ID och VT_CLIENT_SECRET måste vara satta. "
        "Se README.md för hur du sätter dem lokalt eller på PythonAnywhere."
    )

PR_URL = "https://ext-api.vasttrafik.se/pr/v4"
TOKEN_URL = "https://ext-api.vasttrafik.se/token"

# Cache-filer sparas i samma mapp som app.py (går att styra med miljövariabel
# om man vill peka ut en annan, skrivbar mapp, t.ex. på PythonAnywhere).
CACHE_DIR = os.environ.get("VT_CACHE_DIR", BASE_DIR)
TOKEN_FILE = os.path.join(CACHE_DIR, "active-token")
STOP_AREAS_FILE = os.path.join(CACHE_DIR, "stop-areas")

CACHE_MAX_AGE_HOURS = float(os.environ.get("VT_CACHE_HOURS", "20"))

oauth = None  # global OAuth2-session, återanvänds mellan requests


# ---------------------------------------------------------------------------
# OAuth / cache-hjälpfunktioner
# ---------------------------------------------------------------------------

def younger(path1, path2, hours):
    """Rensar bort token- och hållplats-cache om den är äldre än `hours`."""
    now = datetime.now()
    if not os.path.exists(path1) or not os.path.exists(path2):
        return
    last_modification = datetime.fromtimestamp(os.path.getmtime(path1))
    age_in_seconds = (now - last_modification).total_seconds()
    if age_in_seconds > hours * 3600:
        for path in (path1, path2):
            if os.path.exists(path):
                os.remove(path)


def get_token(new=False):
    if not new:
        try:
            with open(TOKEN_FILE) as f:
                data = f.read().strip()
                if data:
                    return json.loads(data)
        except Exception:
            pass
    auth = HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
    client = BackendApplicationClient(client_id=CLIENT_ID)
    session = OAuth2Session(client=client)
    token = session.fetch_token(token_url=TOKEN_URL, auth=auth)
    if not token:
        raise Exception("Kunde inte hämta token från Västtrafiks API")
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)
    return token


def get_oauth_session(new_token=False):
    token = get_token(new_token)
    return OAuth2Session(CLIENT_ID, token=token)


def call(url):
    """Gör ett GET-anrop mot Planera Resa-API:t, hämtar nytt token vid 401."""
    global oauth
    if oauth is None:
        oauth = get_oauth_session()
    response = oauth.get(f"{PR_URL}/{url}")
    if response.status_code == 401:
        oauth = get_oauth_session(new_token=True)
        response = oauth.get(f"{PR_URL}/{url}")
    return json.loads(response.content)


def get_stop_areas():
    try:
        with open(STOP_AREAS_FILE, encoding="utf-8") as f:
            data = f.read().strip()
            if data:
                return json.loads(data)
    except Exception:
        pass
    try:
        stop_area_list = call("stop-areas")
        if not stop_area_list or not isinstance(stop_area_list, list):
            app.logger.warning("API:t gav ingen giltig hållplatslista")
            return {}
        stop_areas = {}
        for sa in stop_area_list:
            name, gid = sa["name"], sa["gid"]
            if name in stop_areas:
                if isinstance(stop_areas[name], list):
                    stop_areas[name].append(gid)
                else:
                    stop_areas[name] = [stop_areas[name], gid]
            else:
                stop_areas[name] = gid
        with open(STOP_AREAS_FILE, "w", encoding="utf-8") as f:
            json.dump(stop_areas, f, indent=4, ensure_ascii=False)
        return stop_areas
    except Exception as e:
        app.logger.error("Kunde inte hämta/spara hållplatser: %s", e)
        return {}


def sort_key(line):
    try:
        return (int(line["shortName"]), line["shortName"])
    except Exception:
        return (float("inf"), line["shortName"])


def get_lines_for_gid(gid):
    lines = OrderedDict()
    try:
        deps = call(
            f"stop-areas/{gid}/departures?timeSpanInMinutes=1439"
            "&maxDeparturesPerLineAndDirection=1&limit=1000&offset=0"
        )
        for dep in deps.get("results", []):
            sj = dep.get("serviceJourney")
            if not sj or "line" not in sj:
                continue
            line = sj["line"]
            key = (line.get("shortName", ""), line.get("transportMode", ""))
            if key not in lines:
                lines[key] = {
                    "shortName": line.get("shortName", ""),
                    "transportMode": line.get("transportMode", ""),
                    "foregroundColor": line.get("foregroundColor", "#000"),
                    "backgroundColor": line.get("backgroundColor", "#fff"),
                }
    except Exception as e:
        app.logger.error("Fel vid hämtning av linjer för gid %s: %s", gid, e)
        return []
    return sorted(lines.values(), key=sort_key)


# ---------------------------------------------------------------------------
# HTML-byggare
# ---------------------------------------------------------------------------

def build_line_spans(lines):
    if not lines:
        return "<span style='color:#888;'>Inga avgångar</span>"
    return " ".join(
        f"<span style='display:inline-block; min-width:2em; text-align:center; "
        f"color:{line['foregroundColor']}; background-color:{line['backgroundColor']}; "
        f"border: 2px solid {line['foregroundColor']}; border-radius: 10px; "
        f"margin-right:2px;'>{line['shortName']}</span>"
        for line in lines
    )


def format_departure_row(departure, direction, show_platform):
    sj_line = departure["serviceJourney"]["line"]
    line = sj_line["shortName"]
    font_size = "0.8em" if len(line) > 3 else "1em"
    span = (
        f"<span style='display:inline-block; min-width:48px; max-width:48px; "
        f"text-align:center; color:{sj_line['foregroundColor']}; "
        f"background-color:{sj_line['backgroundColor']}; "
        f"border: 3px outset {sj_line['foregroundColor']}; "
        f"border-radius: 10px; font-size:{font_size};'>{line}</span>"
    )

    if "estimatedTime" not in departure:
        tid, diff, nu_diff_str = "?????", "", ""
    else:
        est = datetime.fromisoformat(departure["estimatedTime"].split("+")[0])
        planned = datetime.fromisoformat(departure["plannedTime"].split("+")[0])
        tid = est.strftime("%H:%M")
        minuter = int((est - datetime.now()).total_seconds()) // 60
        skillnad = int((est - planned).total_seconds() // 60)
        if skillnad > 0:
            diff = f" (-{skillnad} min)"
        elif skillnad < 0:
            diff = f" (+{abs(skillnad)} min)"
        else:
            diff = ""
        if minuter <= 0:
            nu_diff_str = "   Nu"
        elif 0 < minuter < 10:
            nu_diff_str = f" {minuter} min"
        else:
            nu_diff_str = f"{minuter} min"

    row = f"{span:<10} {direction:<32} {tid:<6}{diff:<10} {nu_diff_str:<7}"
    if show_platform:
        row += f" {departure['stopPoint'].get('platform', '')}"
    return row


def render_departures(stop_area_gid, show_platform):
    deps = call(
        f"stop-areas/{stop_area_gid}/departures?timeSpanInMinutes=65"
        "&maxDeparturesPerLineAndDirection=3&limit=50&offset=0&includeOccupancy=true"
    )

    # (rubrik, transportMode, hämta riktning via directionDetails.shortDirection?)
    sections = [
        ("Spårvagnar:", "tram", False),
        ("Bussar:", "bus", True),
        ("Tåg:", "train", False),
        ("Färjor:", "ferry", False),
    ]

    out = []
    for title, mode, use_short_direction in sections:
        out.append(f"{title}\n\n")
        for departure in deps.get("results", []):
            sj = departure.get("serviceJourney", {})
            if sj.get("line", {}).get("transportMode") != mode:
                continue
            if use_short_direction:
                direction = sj.get("directionDetails", {}).get("shortDirection", "")
            else:
                direction = sj.get("direction", "")
            out.append(format_departure_row(departure, direction, show_platform) + "\n")
        out.append("\n")

    return "".join(out)


def stop_link(name, gid, extra_query=""):
    """Bygger en säkert URL-kodad länk till en hållplats."""
    href = f"?h%C3%A5llplats={quote(name)}&gid={quote(gid)}{extra_query}"
    return f"<a href='{href}' style='font-size:22px'>{escape(name)}</a>"


# ---------------------------------------------------------------------------
# Huvudroute
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    younger(TOKEN_FILE, STOP_AREAS_FILE, CACHE_MAX_AGE_HOURS)

    temp = request.args.get("hållplats", "")
    platform = request.args.get("platform", "false")
    gid_param = request.args.get("gid")

    stop_areas = get_stop_areas()

    parts = ["<html><body style='font-size: 24px; font-family: monospace;'>"]

    parts.append(
        "<div style='display: flex; align-items: center; margin-bottom: 24px; "
        "margin-top: 24px; margin-left: 32px; margin-right: 32px;'>"
        "<a href='?' style='font-size: 32px; text-decoration: none; background: none; "
        "border: #000; border-radius: 6px; padding: 0 18px 0 0; color: #000; "
        "margin-right: 32px;'>&#8962;</a>"
    )
    if temp.strip() and temp in stop_areas:
        parts.append(f"<span>Avgångar från {escape(temp)}</span>")
    parts.append("</div>")

    parts.append("<div style='flex: 1;'><pre style='margin-top: 0px;'>")

    if not temp.strip():
        parts.append("Välkommen till Västtrafik avgångslista!<br>")
        parts.append("Sök efter en hållplats för att se kommande avgångar. <br>")
    elif temp in stop_areas:
        gids = stop_areas[temp]
        gids = gids if isinstance(gids, list) else [gids]
        if gid_param and gid_param in gids:
            parts.append(render_departures(gid_param, platform == "true"))
        elif len(gids) > 1:
            parts.append(f"Flera hållplatser hittades för '{escape(temp)}':<br><br>")
            for gid in gids:
                lines = get_lines_for_gid(gid)
                extra = "&platform=true" if platform == "true" else ""
                parts.append(f"{stop_link(temp, gid, extra)} {build_line_spans(lines)}<br>")
            parts.append("<br>Välj en hållplats ovan.<br>")
        else:
            parts.append(render_departures(gids[0], platform == "true"))
    else:
        matches = [namn for namn in stop_areas if temp.lower() in namn.lower()]
        if matches:
            parts.append(f"Förslag för '{escape(temp)}':<br><br>")
            for namn in sorted(matches, key=lambda x: x.lower()):
                gids = stop_areas[namn]
                gids = gids if isinstance(gids, list) else [gids]
                for gid in gids:
                    lines = get_lines_for_gid(gid)
                    parts.append(f"{stop_link(namn, gid)} {build_line_spans(lines)}<br>")
            parts.append("<br>")
        else:
            parts.append(f"Fel: Hållplatsen '{escape(temp)}' finns inte.<br><br>")

    parts.append("</pre></div>")

    parts.append("<form method='get'>")
    parts.append(
        f"<input type='text' name='hållplats' placeholder='Hållplats' "
        f"value='{escape(temp)}' size='30' style='font-size: 24px; padding: 2px;'><br>"
    )
    checked = "checked" if platform == "true" else ""
    parts.append(
        f"<label style='font-size: 24px;'><input type='checkbox' name='platform' "
        f"value='true' style='width: 24px; height: 24px;' {checked}>"
        "Visa läge/plattform</label><br>"
    )
    parts.append(
        "<input type='submit' value='Sök' style='font-size: 24px; padding: 5px 24px;'>"
    )
    parts.append("</form></body></html>")

    return Response("".join(parts), mimetype="text/html; charset=utf-8")


if __name__ == "__main__":
    # Bara för lokal utveckling. På PythonAnywhere/en riktig WSGI-server
    # importeras `app`-objektet i stället, se README.md.
    app.run(debug=True)
