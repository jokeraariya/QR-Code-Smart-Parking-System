from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import sqlite3
import qrcode
import io
import base64
from datetime import datetime
import csv
import os

app = Flask(__name__)
CORS(app)

app.config['UPLOAD_FOLDER'] = 'static/qr_codes'
app.config['SECRET_KEY'] = 'your-secret-key-here'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static', exist_ok=True)

# ---------------- DATABASE ---------------- #

def init_db():
    conn = sqlite3.connect('parking.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            vehicle_no TEXT UNIQUE,
            qr_code TEXT,
            phone TEXT,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_number TEXT UNIQUE,
            status TEXT DEFAULT 'available',
            vehicle_no TEXT,
            entry_time TIMESTAMP,
            qr_code TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS parking_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            vehicle_no TEXT,
            slot_id INTEGER,
            qr_code TEXT,
            entry_time TIMESTAMP,
            exit_time TIMESTAMP,
            duration_minutes INTEGER,
            fee REAL,
            status TEXT
        )
    ''')

    c.execute("SELECT COUNT(*) FROM slots")
    if c.fetchone()[0] == 0:
        for i in range(1, 51):
            c.execute(
                "INSERT INTO slots (slot_number, status) VALUES (?, ?)",
                (f"SLOT-{i:03d}", "available")
            )

    conn.commit()
    conn.close()

def get_db_connection():
    conn = sqlite3.connect('parking.db')
    conn.row_factory = sqlite3.Row
    return conn

# ---------------- HELPERS ---------------- #

def generate_qr_code(data):
    qr = qrcode.make(data)
    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()

def calculate_fee(entry, exit):
    minutes = int((exit - entry).total_seconds() / 60)
    if minutes <= 30:
        return 20
    return 20 + ((minutes - 30 + 29) // 30) * 10

# 🔹 YOUR NEW FEATURE (SLOT RECOMMENDATION)
def recommend_slot(vehicle_no=None):
    conn = get_db_connection()
    c = conn.cursor()

    c.execute(
        "SELECT * FROM slots WHERE status='available' ORDER BY slot_number LIMIT 1"
    )

    slot = c.fetchone()
    conn.close()
    return slot

# ---------------- ROUTES ---------------- #

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/entry')
def entry():
    return render_template('entry.html')

@app.route('/exit')
def exit_page():
    return render_template('exit.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/register')
def register():
    return render_template('register.html')

# ---------------- API ---------------- #

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json
    name = data.get('name')
    vehicle_no = data.get('vehicle_no')

    conn = get_db_connection()
    c = conn.cursor()

    qr_data = f"PARKING:{vehicle_no}:{datetime.now().timestamp()}"
    qr_code = generate_qr_code(qr_data)

    c.execute(
        "INSERT INTO users (name, vehicle_no, qr_code) VALUES (?, ?, ?)",
        (name, vehicle_no, qr_code)
    )

    conn.commit()
    conn.close()

    return jsonify({"success": True, "qr": qr_code})

@app.route('/api/entry', methods=['POST'])
def api_entry():
    data = request.json
    qr_data = data.get('qr_data')

    vehicle_no = qr_data.split(":")[1]

    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE vehicle_no=?", (vehicle_no,))
    user = c.fetchone()

    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404

    c.execute(
        "SELECT * FROM parking_logs WHERE vehicle_no=? AND status='active'",
        (vehicle_no,)
    )
    if c.fetchone():
        conn.close()
        return jsonify({"error": "Already parked"}), 400

    # 🔹 SLOT RECOMMENDATION USED HERE
    slot = recommend_slot(vehicle_no)

    if not slot:
        conn.close()
        return jsonify({"error": "No slots available"}), 400

    entry_time = datetime.now()

    c.execute(
        "UPDATE slots SET status='occupied', vehicle_no=?, entry_time=? WHERE id=?",
        (vehicle_no, entry_time, slot['id'])
    )

    c.execute(
        """INSERT INTO parking_logs
           (user_id, vehicle_no, slot_id, entry_time, status)
           VALUES (?, ?, ?, ?, ?)""",
        (user['id'], vehicle_no, slot['id'], entry_time, "active")
    )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "slot_number": slot['slot_number'],
        "entry_time": entry_time.isoformat(),
        "recommendation": "nearest available slot"
    })

@app.route('/api/exit', methods=['POST'])
def api_exit():
    data = request.json
    qr_data = data.get('qr_data')
    vehicle_no = qr_data.split(":")[1]

    conn = get_db_connection()
    c = conn.cursor()

    c.execute(
        "SELECT * FROM parking_logs WHERE vehicle_no=? AND status='active'",
        (vehicle_no,)
    )
    log = c.fetchone()

    if not log:
        conn.close()
        return jsonify({"error": "No active parking"}), 404

    exit_time = datetime.now()
    entry_time = datetime.fromisoformat(log['entry_time'])
    fee = calculate_fee(entry_time, exit_time)

    c.execute(
        "UPDATE parking_logs SET exit_time=?, fee=?, status='completed' WHERE id=?",
        (exit_time, fee, log['id'])
    )

    c.execute(
        "UPDATE slots SET status='available', vehicle_no=NULL, entry_time=NULL WHERE id=?",
        (log['slot_id'],)
    )

    conn.commit()
    conn.close()

    return jsonify({"success": True, "fee": fee})

# ---------------- RUN ---------------- #

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5001)
