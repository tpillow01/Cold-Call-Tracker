import os, shutil
import sqlite3
from datetime import datetime, timedelta, date as py_date
from calendar import monthrange
from collections import defaultdict
from functools import wraps

from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# ── Persistent DB path (Render Disk) ─────────────────────────────────────────────
# Prefer an explicit DB_PATH=/your/mount/calls.db. If not set, build one from DATA_DIR.
DISK_DIR = os.environ.get("DATA_DIR")  # e.g., /var/data  (set this to your Disk mount path)
DB_PATH = os.environ.get("DB_PATH")    # e.g., /var/data/calls.db

if not DB_PATH:
    if DISK_DIR:
        DB_PATH = os.path.join(DISK_DIR, "calls.db")
    else:
        # Fallback: project folder (ephemeral). Use only if you don't have a Disk yet.
        DB_PATH = os.path.join(os.getcwd(), "calls.db")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# One-time migration: copy any legacy DB into the new persistent path
LEGACY_CANDIDATES = [
    os.path.join(os.getcwd(), "calls.db"),
    "/opt/render/project/src/calls.db",
]
if not os.path.exists(DB_PATH):
    for cand in LEGACY_CANDIDATES:
        if os.path.exists(cand):
            try:
                shutil.copy2(cand, DB_PATH)
                print(f"✅ Migrated legacy DB {cand} -> {DB_PATH}")
                break
            except Exception as e:
                print(f"⚠️ Migration failed from {cand}: {e}")

print(f"SQLite path in use: {DB_PATH}")

DB = DB_PATH  # keep using DB everywhere below

# ─── DB HELPERS ─────────────────────────
def get_db_connection():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ─── Admin migration + seeding ──────────────────────────────────────────────────
def _users_has_is_admin(conn):
    cols = conn.execute("PRAGMA table_info(users)").fetchall()
    return any(c["name"] == "is_admin" for c in cols)

def _ensure_is_admin_column(conn):
    if not _users_has_is_admin(conn):
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        conn.commit()

def _ensure_seed_admin(conn):
    """
    Seeds or updates an admin user from env:
      ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_NAME (optional)
    """
    admin_user = (os.getenv("ADMIN_USERNAME") or "").strip()
    admin_pass = (os.getenv("ADMIN_PASSWORD") or "").strip()
    admin_name = (os.getenv("ADMIN_NAME") or "Admin").strip()
    if not (admin_user and admin_pass):
        return
    row = conn.execute("SELECT id FROM users WHERE username = ?", (admin_user,)).fetchone()
    hashed = generate_password_hash(admin_pass)
    if row:
        conn.execute(
            "UPDATE users SET password = ?, name = ?, is_admin = 1 WHERE id = ?",
            (hashed, admin_name, row["id"])
        )
    else:
        conn.execute(
            "INSERT INTO users (username, password, name, is_admin) VALUES (?, ?, ?, 1)",
            (admin_user, hashed, admin_name)
        )
    conn.commit()

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL
            -- is_admin added via migration below
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS cold_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT,
            contact_name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            rep_id INTEGER,
            date_called TEXT,
            FOREIGN KEY (rep_id) REFERENCES users (id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            date TEXT NOT NULL,   -- YYYY-MM-DD
            time TEXT,            -- HH:MM
            type TEXT,            -- call | visit | demo
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    # Admin column + seed admin
    _ensure_is_admin_column(conn)
    _ensure_seed_admin(conn)

    conn.commit()
    conn.close()

# Ensure tables exist even when imported by gunicorn
with app.app_context():
    try:
        init_db()
    except Exception as e:
        print("init_db note:", e)

# Make is_admin available in templates
@app.context_processor
def inject_globals():
    return dict(is_admin=bool(session.get('is_admin')), current_user=session.get('name'))

# ─── AUTH ───────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        conn = get_db_connection()
        user = conn.execute(
            'SELECT id, username, password, name, COALESCE(is_admin,0) AS is_admin FROM users WHERE username = ?',
            (username,)
        ).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['is_admin'] = bool(user['is_admin'])
            return redirect('/')
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        if not name or not username or not password:
            return render_template('signup.html', error='All fields are required.')
        hashed = generate_password_hash(password)
        conn = get_db_connection()
        try:
            # regular users only
            conn.execute('INSERT INTO users (username, password, name, is_admin) VALUES (?, ?, ?, 0)', (username, hashed, name))
            conn.commit()
            return redirect('/login')
        except sqlite3.IntegrityError:
            return render_template('signup.html', error='Username already exists.')
        finally:
            conn.close()
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ─── DASHBOARD ──────────────────────────
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    conn = get_db_connection()
    raw_calls = conn.execute(
        'SELECT * FROM cold_calls WHERE rep_id = ? ORDER BY date_called DESC',
        (user_id,)
    ).fetchall()
    conn.close()

    calls_by_month = defaultdict(list)
    reminders = []
    today = py_date.today()

    for call in raw_calls:
        try:
            call_date = datetime.strptime(call['date_called'], '%Y-%m-%d').date()
        except Exception:
            try:
                call_date = datetime.fromisoformat(call['date_called']).date()
            except Exception:
                call_date = today
        month_key = call_date.strftime('%B %Y')
        calls_by_month[month_key].append(call)

        days_ago = (today - call_date).days
        if days_ago >= 7:
            suggestion = "Call again" if days_ago < 14 else "Schedule a site visit"
            reminders.append({"company": call['company'], "days_ago": days_ago, "action": suggestion})

    return render_template(
        'index.html',
        calls_by_month=calls_by_month,
        today=today,
        user=session['name'],
        datetime=datetime,
        reminders=reminders
    )

# ─── CALLS CRUD ─────────────────────────
@app.route('/add', methods=['POST'])
def add_call():
    if 'user_id' not in session:
        return redirect('/login')

    rep_id       = session['user_id']
    company      = (request.form.get('company') or '').strip()
    contact_name = (request.form.get('contact_name') or '').strip()
    phone        = (request.form.get('phone') or '').strip()
    email        = (request.form.get('email') or '').strip()
    address      = (request.form.get('address') or '').strip()
    notes        = (request.form.get('notes') or '').strip()
    date_called  = py_date.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    conn.execute('''
        INSERT INTO cold_calls (rep_id, company, contact_name, phone, email, address, notes, date_called)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (rep_id, company, contact_name, phone, email, address, notes, date_called))
    conn.commit()
    conn.close()
    return redirect('/')

@app.route('/edit_call/<int:id>', methods=['GET', 'POST'])
def edit_call(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    call = conn.execute('SELECT * FROM cold_calls WHERE id = ? AND rep_id = ?', (id, session['user_id'])).fetchone()
    if not call:
        conn.close()
        return redirect('/')
    if request.method == 'POST':
        conn.execute('''
            UPDATE cold_calls SET company = ?, contact_name = ?, phone = ?, email = ?, address = ?, notes = ?
            WHERE id = ? AND rep_id = ?
        ''', (
            request.form['company'], request.form['contact_name'], request.form['phone'],
            request.form['email'], request.form['address'], request.form['notes'],
            id, session['user_id']
        ))
        conn.commit()
        conn.close()
        return redirect('/')
    conn.close()
    return render_template('edit_call.html', call=call)

@app.route('/delete_call/<int:id>')
def delete_call(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    conn.execute('DELETE FROM cold_calls WHERE id = ? AND rep_id = ?', (id, session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/')

# ─── SCHEDULE (view + legacy POST) ───────
@app.route('/schedule', methods=['GET', 'POST'])
def schedule():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    today = py_date.today()
    if not year or not month:
        year = today.year
        month = today.month

    # Legacy: accept POST to /schedule
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        description = (request.form.get('description') or '').strip()
        date_val = (request.form.get('date') or '').strip()
        time_val = (request.form.get('time') or '').strip()
        event_type = (request.form.get('type') or 'call').strip()
        if title and date_val:
            conn = get_db_connection()
            conn.execute('''
                INSERT INTO schedule (user_id, title, description, date, time, type)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, title, description, date_val, time_val, event_type))
            conn.commit()
            conn.close()
            try:
                y, m, _ = map(int, date_val.split('-'))
                return redirect(f'/schedule?year={y}&month={m}')
            except Exception:
                return redirect('/schedule')

    first_day = py_date(year, month, 1)
    _, total_days = monthrange(year, month)
    last_day = py_date(year, month, total_days)

    conn = get_db_connection()
    rows = conn.execute('''
        SELECT id, user_id, title, description, date, time, type
          FROM schedule
         WHERE user_id = ? AND date BETWEEN ? AND ?
         ORDER BY date, time
    ''', (user_id, first_day.isoformat(), last_day.isoformat())).fetchall()
    conn.close()

    # Shape for JS: { "YYYY-MM-DD": [ {title,time,notes,type,id} ] }
    event_map = {}
    for e in rows:
        iso = e['date']
        event_map.setdefault(iso, []).append({
            'id': e['id'],
            'title': e['title'],
            'time': e['time'],
            'notes': e['description'],
            'type': e['type'],
        })

    # basic suggestions
    keywords = {"demo": 0, "visit": 0, "call": 0}
    for e in rows:
        t = (e['title'] or '').lower()
        for k in keywords:
            if k in t:
                keywords[k] += 1
    suggestions = [k.capitalize() for k, _ in sorted(keywords.items(), key=lambda x: -x[1])[:3]]

    return render_template(
        'schedule.html',
        user=session['name'],
        today=today,
        year=year,
        month=month,
        total_days=total_days,
        event_map=event_map,
        timedelta=timedelta,
        date=py_date,
        datetime=datetime,
        suggestions=suggestions
    )

# POST from the Add Event modal
@app.route('/add_event', methods=['POST'])
def add_event():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    title = (request.form.get('title') or '').strip()
    description = (request.form.get('notes') or request.form.get('description') or '').strip()
    date_val = (request.form.get('date') or '').strip()  # yyyy-mm-dd
    time_val = (request.form.get('time') or '').strip()
    event_type = (request.form.get('type') or 'call').strip()

    if not title or not date_val:
        return redirect('/schedule')

    conn = get_db_connection()
    conn.execute('''
        INSERT INTO schedule (user_id, title, description, date, time, type)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, title, description, date_val, time_val, event_type))
    conn.commit()
    conn.close()

    try:
        y, m, _ = map(int, date_val.split('-'))
        return redirect(f'/schedule?year={y}&month={m}')
    except Exception:
        return redirect('/schedule')

@app.route('/edit_event/<int:id>', methods=['GET', 'POST'])
def edit_event(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    event = conn.execute('SELECT * FROM schedule WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    if not event:
        conn.close()
        return redirect('/schedule')
    if request.method == 'POST':
        conn.execute('''
            UPDATE schedule SET title = ?, description = ?, date = ?, time = ?, type = ?
            WHERE id = ? AND user_id = ?
        ''', (
            request.form.get('title'),
            request.form.get('description'),
            request.form.get('date'),
            request.form.get('time'),
            request.form.get('type') or event['type'],
            id, session['user_id']
        ))
        conn.commit()
        conn.close()
        try:
            y, m, _ = map(int, (request.form.get('date') or '').split('-'))
            return redirect(f'/schedule?year={y}&month={m}')
        except Exception:
            return redirect('/schedule')
    conn.close()
    return render_template('edit_event.html', event=event)

@app.route('/delete_event/<int:id>', methods=['POST', 'GET'])
def delete_event(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    conn.execute('DELETE FROM schedule WHERE id = ? AND user_id = ?', (id, session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/schedule')

# ─── ADMIN GUARD + VIEW ─────────────────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect('/')
        return f(*args, **kwargs)
    return wrapper

@app.route('/admin')
@admin_required
def admin_home():
    q = (request.args.get('q') or '').strip()
    params, where = [], "1=1"
    if q:
        like = f"%{q}%"
        where += """ AND (
            cc.company LIKE ? OR cc.contact_name LIKE ? OR cc.email LIKE ? OR
            cc.phone LIKE ? OR cc.address LIKE ? OR cc.notes LIKE ? OR
            u.name LIKE ? OR u.username LIKE ?
        )"""
        params.extend([like]*8)

    conn = get_db_connection()
    rows = conn.execute(f'''
        SELECT cc.*,
               COALESCE(u.name, '')     AS rep_name,
               COALESCE(u.username, '') AS rep_username
          FROM cold_calls cc
          LEFT JOIN users u ON u.id = cc.rep_id
         WHERE {where}
         ORDER BY
           CASE WHEN cc.date_called GLOB '[0-9][0-9][0-9][0-9]-*' THEN 0 ELSE 1 END,
           cc.date_called DESC,
           cc.id DESC
    ''', params).fetchall()
    conn.close()

    return render_template('admin.html', rows=rows, q=q)

# ─── DEV ENTRY ───────────────────────────
if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5008)
