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
    
    # Minimum 1 minute for testing
    if minutes < 1:
        minutes = 1
    
    if minutes <= 30:
        return 20
    
    # For every additional 30 minutes, add ₹10
    additional_slots = ((minutes - 30) // 30) + 1
    return 20 + (additional_slots * 10)

# SLOT RECOMMENDATION
def recommend_slot(duration_minutes):
    conn = get_db_connection()
    c = conn.cursor()

    # ===== SLOT ZONES =====
    if duration_minutes <= 30:
        start, end = 1, 15        # short stay
    elif duration_minutes <= 180:
        start, end = 16, 40      # medium stay
    else:
        start, end = 41, 50      # long stay

    c.execute("""
        SELECT * FROM slots
        WHERE status='available'
        AND CAST(SUBSTR(slot_number, 6) AS INTEGER) BETWEEN ? AND ?
        ORDER BY slot_number
        LIMIT 1
    """, (start, end))

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
    phone = data.get('phone')
    email = data.get('email')

    if not name or not vehicle_no:
        return jsonify({"error": "Missing data"}), 400

    conn = get_db_connection()
    c = conn.cursor()

    # QR text
    qr_data = f"PARKING:{vehicle_no}:{datetime.now().timestamp()}"

    # Generate QR image (base64)
    img = qrcode.make(qr_data)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    qr_image = base64.b64encode(buffer.getvalue()).decode()

    # Save user with phone and email
    try:
        c.execute(
            "INSERT INTO users (name, vehicle_no, qr_code, phone, email) VALUES (?, ?, ?, ?, ?)",
            (name, vehicle_no, qr_data, phone, email)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Vehicle number already registered"}), 400

    conn.close()

    return jsonify({
        "success": True,
        "qr_data": qr_data,
        "qr_image": qr_image
    })


@app.route('/api/entry', methods=['POST'])
def api_entry():
    data = request.json
    qr_data = data.get('qr_data')
    duration = int(data.get('duration', 30))

    if not qr_data or ":" not in qr_data:
        return jsonify({"error": "Invalid QR code"}), 400

    vehicle_no = qr_data.split(":")[1]

    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE vehicle_no=?", (vehicle_no,))
    user = c.fetchone()

    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404

    # Check if vehicle already has active parking
    c.execute("SELECT * FROM parking_logs WHERE vehicle_no=? AND status='active'", (vehicle_no,))
    active_parking = c.fetchone()
    
    if active_parking:
        conn.close()
        return jsonify({"error": "Vehicle already parked"}), 400

    slot = recommend_slot(duration)

    if not slot:
        conn.close()
        return jsonify({"error": "No slots available"}), 400

    # store as string
    entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        UPDATE slots
        SET status='occupied', vehicle_no=?, entry_time=?
        WHERE id=?
    """, (vehicle_no, entry_time, slot['id']))

    c.execute("""
        INSERT INTO parking_logs
        (user_id, vehicle_no, slot_id, entry_time, status)
        VALUES (?, ?, ?, ?, ?)
    """, (user['id'], vehicle_no, slot['id'], entry_time, "active"))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "slot_number": slot['slot_number'],
        "entry_time": entry_time,
        "zone": f"{duration} minutes zone"
    })


@app.route('/api/exit', methods=['POST'])
def api_exit():
    data = request.json
    qr_data = data.get('qr_data')
    
    if not qr_data or ":" not in qr_data:
        return jsonify({"error": "Invalid QR code"}), 400
    
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
        return jsonify({"error": "No active parking found for this vehicle"}), 404

    # Get slot information
    c.execute("SELECT slot_number FROM slots WHERE id=?", (log['slot_id'],))
    slot = c.fetchone()

    exit_time = datetime.now()

    # parse correctly
    entry_time = datetime.strptime(log['entry_time'], "%Y-%m-%d %H:%M:%S")

    # Calculate duration and fee
    duration_minutes = int((exit_time - entry_time).total_seconds() / 60)
    
    # Minimum 1 minute for calculation
    if duration_minutes < 1:
        duration_minutes = 1
        
    fee = calculate_fee(entry_time, exit_time)

    # Update parking log
    c.execute(
        "UPDATE parking_logs SET exit_time=?, duration_minutes=?, fee=?, status='completed' WHERE id=?",
        (exit_time.strftime("%Y-%m-%d %H:%M:%S"), duration_minutes, fee, log['id'])
    )

    # Free the slot
    c.execute(
        "UPDATE slots SET status='available', vehicle_no=NULL, entry_time=NULL WHERE id=?",
        (log['slot_id'],)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "slot_number": slot['slot_number'],
        "entry_time": log['entry_time'],
        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes": duration_minutes,
        "fee": fee
    })


# ================= ADMIN APIs =================

@app.route('/api/users')
def get_users():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = c.fetchall()

    users = [dict(r) for r in rows]

    conn.close()

    return jsonify({"users": users})


@app.route('/api/slots')
def get_slots():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM slots ORDER BY slot_number")
    rows = c.fetchall()

    slots = [dict(r) for r in rows]

    conn.close()

    return jsonify({"slots": slots})


@app.route('/api/logs')
def get_logs():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT p.*, s.slot_number
        FROM parking_logs p
        LEFT JOIN slots s ON p.slot_id = s.id
        ORDER BY p.id DESC
    """)

    rows = c.fetchall()
    logs = [dict(r) for r in rows]

    conn.close()

    return jsonify({"logs": logs})


@app.route('/api/stats')
def get_stats():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT SUM(fee) FROM parking_logs WHERE status='completed'")
    total = c.fetchone()[0] or 0

    c.execute("""
        SELECT SUM(fee) FROM parking_logs
        WHERE DATE(exit_time)=DATE('now')
    """)
    today = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM parking_logs WHERE status='active'")
    active = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM slots WHERE status='available'")
    available = c.fetchone()[0]

    conn.close()

    return jsonify({
        "total_revenue": total,
        "today_revenue": today,
        "active_parkings": active,
        "available_slots": available
    })


# ================= NEW: CLEAR SLOT (ADMIN) =================
@app.route('/api/clear-slot/<int:slot_id>', methods=['POST'])
def clear_slot(slot_id):
    conn = get_db_connection()
    c = conn.cursor()

    # Get slot info
    c.execute("SELECT * FROM slots WHERE id=?", (slot_id,))
    slot = c.fetchone()

    if not slot:
        conn.close()
        return jsonify({"error": "Slot not found"}), 404

    # If slot is occupied, cancel the active parking log
    if slot['status'] == 'occupied' and slot['vehicle_no']:
        c.execute("""
            UPDATE parking_logs 
            SET status='cancelled', exit_time=? 
            WHERE vehicle_no=? AND status='active'
        """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), slot['vehicle_no']))

    # Clear the slot
    c.execute("""
        UPDATE slots 
        SET status='available', vehicle_no=NULL, entry_time=NULL 
        WHERE id=?
    """, (slot_id,))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": f"Slot {slot['slot_number']} cleared"})


# ---------------- RUN ---------------- #

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5001)