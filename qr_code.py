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
app.config['SECRET_KEY'] = 'secret'

os.makedirs('static', exist_ok=True)


# ===============================
# DATABASE
# ===============================

def get_db_connection():
    conn = sqlite3.connect('parking.db')
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            vehicle_no TEXT UNIQUE,
            qr_code TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS slots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_number TEXT UNIQUE,
            status TEXT DEFAULT 'available'
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS parking_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_no TEXT,
            slot_id INTEGER,
            entry_time TIMESTAMP,
            exit_time TIMESTAMP,
            duration_minutes INTEGER,
            fee REAL,
            status TEXT DEFAULT 'active'
        )
    ''')

    # create 50 slots first time
    c.execute("SELECT COUNT(*) FROM slots")
    if c.fetchone()[0] == 0:
        for i in range(1, 51):
            c.execute(
                "INSERT INTO slots(slot_number) VALUES(?)",
                (f"SLOT-{i:03d}",)
            )

    conn.commit()
    conn.close()


# ===============================
# SMART RECOMMENDATION LOGIC
# ===============================

def recommend_slot(duration):
    conn = get_db_connection()
    c = conn.cursor()

    # ranges
    if duration <= 20:
        start, end = 1, 15
    elif duration <= 50:
        start, end = 16, 40
    else:
        start, end = 41, 50

    c.execute("""
        SELECT * FROM slots
        WHERE status='available'
        AND CAST(SUBSTR(slot_number,6) AS INTEGER) BETWEEN ? AND ?
        ORDER BY slot_number
        LIMIT 1
    """, (start, end))

    slot = c.fetchone()
    conn.close()
    return slot


# ===============================
# ROUTES
# ===============================

@app.route('/')
def home():
    return render_template('entry.html')


@app.route('/api/entry', methods=['POST'])
def api_entry():
    data = request.json
    qr_data = data.get('qr_data')
    duration = int(data.get('duration'))

    if not qr_data:
        return jsonify({'error': 'QR required'}), 400

    try:
        vehicle_no = qr_data.split(':')[1]
    except:
        return jsonify({'error': 'Invalid QR'}), 400

    slot = recommend_slot(duration)

    if not slot:
        return jsonify({'error': 'No slots available'}), 400

    conn = get_db_connection()
    c = conn.cursor()

    entry_time = datetime.now()

    c.execute("""
        INSERT INTO parking_logs(vehicle_no, slot_id, entry_time, status)
        VALUES (?, ?, ?, 'active')
    """, (vehicle_no, slot['id'], entry_time))

    c.execute("UPDATE slots SET status='occupied' WHERE id=?",
              (slot['id'],))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "slot_number": slot['slot_number'],
        "entry_time": entry_time.isoformat()
    })


@app.route('/api/exit', methods=['POST'])
def api_exit():
    data = request.json
    qr_data = data.get('qr_data')

    vehicle_no = qr_data.split(':')[1]

    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT * FROM parking_logs
        WHERE vehicle_no=? AND status='active'
    """, (vehicle_no,))
    log = c.fetchone()

    if not log:
        return jsonify({'error': 'No active parking'}), 400

    exit_time = datetime.now()
    duration = int((exit_time - datetime.fromisoformat(log['entry_time'])).total_seconds() / 60)
    fee = 20 + (duration // 30) * 10

    c.execute("""
        UPDATE parking_logs
        SET exit_time=?, duration_minutes=?, fee=?, status='completed'
        WHERE id=?
    """, (exit_time, duration, fee, log['id']))

    c.execute("UPDATE slots SET status='available' WHERE id=?",
              (log['slot_id'],))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "fee": fee})


# ===============================
# RUN
# ===============================

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5001)