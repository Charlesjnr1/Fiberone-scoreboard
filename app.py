from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO
from datetime import datetime
import json
import requests
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'
# Allow cross-origin socket connections from browser
socketio = SocketIO(app, cors_allowed_origins="*")

SCOREBOARD_FILE = 'scoreboard.json'


# --------------------
# SCOREBOARD UTILITIES
# --------------------
def load_scoreboard():
    # keep red/white/gray present (avoids template math errors)
    default_score = {"red": 0, "white": 0, "gray": 0}
    default_data = {
        "team1": {"name": "Team 1", "score": default_score.copy()},
        "team2": {"name": "Team 2", "score": default_score.copy()},
        "winner": None
    }

    if os.path.exists(SCOREBOARD_FILE):
        try:
            with open(SCOREBOARD_FILE, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            data = default_data.copy()
    else:
        data = default_data.copy()

    # Ensure correct structure and keys exist
    for team_key in ["team1", "team2"]:
        if team_key not in data or not isinstance(data[team_key], dict):
            data[team_key] = {"name": f"Team {team_key[-1]}", "score": default_score.copy()}

        # Ensure score dictionary exists and has the three keys
        if "score" not in data[team_key] or not isinstance(data[team_key]["score"], dict):
            data[team_key]["score"] = default_score.copy()
        else:
            for color in default_score:
                # set missing keys to 0
                data[team_key]["score"].setdefault(color, 0)

        # set default name if missing
        data[team_key].setdefault("name", f"Team {team_key[-1]}")

    if "winner" not in data:
        data["winner"] = None

    return data


def save_scoreboard(data):
    # write pretty JSON for debugging
    with open(SCOREBOARD_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# --------------------
# ROUTES
# --------------------
@app.route('/')
def home():
    log_visit()
    data = load_scoreboard()
    return render_template('index.html', data=data)


@app.route('/api/scoreboard')
def api_scoreboard():
    """Return current scoreboard JSON — used by admin page to refresh initial values."""
    return jsonify(load_scoreboard())


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == '1234':
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='Invalid credentials')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard', methods=['GET', 'POST'])
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    scoreboard = load_scoreboard()

    if request.method == 'POST':
        # names
        scoreboard['team1']['name'] = request.form.get('team1_name', scoreboard['team1'].get('name', 'Team 1'))
        scoreboard['team2']['name'] = request.form.get('team2_name', scoreboard['team2'].get('name', 'Team 2'))

        # parse scores safely — keep red/white if admin page doesn't provide them
        def parse_int_field(form, key, existing):
            val = form.get(key)
            if val is None or val == '':
                return existing
            try:
                return int(val)
            except ValueError:
                return existing

        # existing values to fall back to
        t1_score = scoreboard['team1'].get('score', {})
        t2_score = scoreboard['team2'].get('score', {})

        scoreboard['team1']['score'] = {
            'red': parse_int_field(request.form, 'team1_red', t1_score.get('red', 0)),
            'white': parse_int_field(request.form, 'team1_white', t1_score.get('white', 0)),
            'gray': parse_int_field(request.form, 'team1_gray', t1_score.get('gray', 0)),
        }
        scoreboard['team2']['score'] = {
            'red': parse_int_field(request.form, 'team2_red', t2_score.get('red', 0)),
            'white': parse_int_field(request.form, 'team2_white', t2_score.get('white', 0)),
            'gray': parse_int_field(request.form, 'team2_gray', t2_score.get('gray', 0)),
        }

        save_scoreboard(scoreboard)

        # Send update to ALL connected clients (front page + other admin windows)
        # broadcast=True makes sure everyone receives it
        socketio.emit('score_update', {"data": scoreboard}, broadcast=True)

        # If AJAX (your admin form uses X-Requested-With), return JSON; otherwise redirect
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"status": "success", "scoreboard": scoreboard})

        return redirect(url_for('admin_dashboard'))

    # GET: render admin dashboard
    visits = load_visits()
    return render_template(
        'admin_dashboard.html',
        data=scoreboard,
        visits=visits
    )


@app.route('/admin/scoreboard')
def admin_scoreboard():
    """Optional separate admin scoreboard view (keeps for compatibility)."""
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    scoreboard = load_scoreboard()
    return render_template('admin_scoreboard.html', data=scoreboard)


@app.route('/declare_winner', methods=['POST'])
def declare_winner():
    req_data = request.get_json() or {}
    winner = req_data.get('winner')

    scoreboard = load_scoreboard()
    scoreboard['winner'] = winner
    save_scoreboard(scoreboard)

    socketio.emit('winner_declared', {'winner': winner}, broadcast=True)
    # also broadcast a score_update so templates that expect data update immediately
    socketio.emit('score_update', {'data': scoreboard}, broadcast=True)
    return jsonify({"status": "success", "winner": winner})


@app.route('/admin/visits')
def view_visits():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    visits = load_visits()
    return render_template('visits.html', visits=visits)


# --------------------
# VISITOR LOGGING
# --------------------
def load_visits():
    if not os.path.exists("visitors.log"):
        return []
    visits = []
    with open("visitors.log") as f:
        for line in f:
            parts = line.strip().split(" - ")
            if len(parts) >= 10:
                visits.append({
                    "time": parts[0],
                    "event": parts[1],
                    "userAgent": parts[2],
                    "screen": parts[3],
                    "ip": parts[4].replace("IP: ", ""),
                    "location": parts[5].replace("Location: ", ""),
                    "lat": parts[6].replace("Lat: ", ""),
                    "lon": parts[7].replace("Lon: ", ""),
                    "isp": parts[8].replace("ISP: ", ""),
                    "timezone": parts[9].replace("Timezone: ", "")
                })
    return visits


def log_visit(event_type="Visit"):
    ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'Unknown')
    screen = request.args.get('screen', 'N/A')
    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        geo_resp = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo_resp.get('city', '')
        region = geo_resp.get('regionName', '')
        country = geo_resp.get('country', '')
        lat = geo_resp.get('lat', '')
        lon = geo_resp.get('lon', '')
        isp = geo_resp.get('isp', '')
        timezone = geo_resp.get('timezone', '')
        location = f"{city}, {region}, {country}"
    except Exception:
        location = "Unknown"
        lat = lon = isp = timezone = "N/A"

    log_entry = (
        f"{time_now} - {event_type} - {user_agent} - {screen} - "
        f"IP: {ip} - Location: {location} - Lat: {lat} - Lon: {lon} - "
        f"ISP: {isp} - Timezone: {timezone}\n"
    )

    with open("visitors.log", "a") as logfile:
        logfile.write(log_entry)


# --------------------
# RUN
# --------------------
if __name__ == "__main__":
    # Use socketio.run so socket server is started correctly
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
