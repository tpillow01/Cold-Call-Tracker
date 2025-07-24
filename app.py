# app.py
from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
import calendar
from datetime import datetime, timedelta, date as py_date
from calendar import monthrange
from collections import defaultdict
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your_secret_key'
DB = 'calls.db'

# ─── DB CONNECTION ─────────────────────────
def get_db_connection():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ─── INIT TABLES ───────────────────────────
def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL
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
            date TEXT NOT NULL,
            time TEXT,
            type TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

# ─── LOGIN ─────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['name'] = user['name']
            return redirect('/')
        else:
            return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

# ─── SIGNUP ────────────────────────────────
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not name or not username or not password:
            return render_template('signup.html', error='All fields are required.')

        hashed = generate_password_hash(password)
        conn = get_db_connection()
        try:
            conn.execute('INSERT INTO users (username, password, name) VALUES (?, ?, ?)', (username, hashed, name))
            conn.commit()
            return redirect('/login')
        except sqlite3.IntegrityError:
            return render_template('signup.html', error='Username already exists.')
        finally:
            conn.close()

    return render_template('signup.html')

# ─── LOGOUT ────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ─── DASHBOARD ─────────────────────────────
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')
    user_id = session['user_id']
    conn = get_db_connection()
    raw_calls = conn.execute('SELECT * FROM cold_calls WHERE rep_id = ? ORDER BY date_called DESC', (user_id,)).fetchall()

    calls_by_month = defaultdict(list)
    reminders = []
    today = datetime.today().date()

    for call in raw_calls:
        call_date = datetime.strptime(call['date_called'], '%Y-%m-%d').date()
        month_key = call_date.strftime('%B %Y')
        calls_by_month[month_key].append(call)

        days_ago = (today - call_date).days
        if days_ago >= 7:
            suggestion = "Call again" if days_ago < 14 else "Schedule a site visit"
            reminders.append({
                "company": call['company'],
                "days_ago": days_ago,
                "suggestion": suggestion
            })

    conn.close()
    return render_template('index.html', calls_by_month=calls_by_month, today=today, user=session['name'], datetime=datetime, reminders=reminders)

# ─── EDIT CALL ─────────────────────────────
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
            request.form['company'],
            request.form['contact_name'],
            request.form['phone'],
            request.form['email'],
            request.form['address'],
            request.form['notes'],
            id,
            session['user_id']
        ))
        conn.commit()
        conn.close()
        return redirect('/')
    conn.close()
    return render_template('edit_call.html', call=call)

# ─── DELETE CALL ───────────────────────────
@app.route('/delete_call/<int:id>')
def delete_call(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    conn.execute('DELETE FROM cold_calls WHERE id = ? AND rep_id = ?', (id, session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/')

# ─── ADD CALL ──────────────────────────────
@app.route('/add', methods=['POST'])
def add_call():
    if 'user_id' not in session:
        return redirect('/login')

    rep_id       = session['user_id']
    company      = request.form.get('company', '').strip()
    contact_name = request.form.get('contact_name', '').strip()
    phone        = request.form.get('phone', '').strip()
    email        = request.form.get('email', '').strip()
    address      = request.form.get('address', '').strip()
    notes        = request.form.get('notes', '').strip()
    date_called  = datetime.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    conn.execute('''
        INSERT INTO cold_calls (rep_id, company, contact_name, phone, email, address, notes, date_called)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (rep_id, company, contact_name, phone, email, address, notes, date_called))
    conn.commit()
    conn.close()

    return redirect('/')

# ─── SCHEDULE ──────────────────────────────
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

    first_day = py_date(year, month, 1)
    _, total_days = monthrange(year, month)
    last_day = py_date(year, month, total_days)

    conn = get_db_connection()

    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        date_val = request.form.get('date')
        time = request.form.get('time')
        event_type = request.form.get('type', 'call')

        if title and date_val:
            conn.execute('''
                INSERT INTO schedule (user_id, title, description, date, time, type)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, title, description, date_val, time, event_type))
            conn.commit()

    events = conn.execute('''
        SELECT * FROM schedule
        WHERE user_id = ? AND date BETWEEN ? AND ?
    ''', (user_id, first_day.isoformat(), last_day.isoformat())).fetchall()
    conn.close()

    event_map = {}
    for event in events:
        event_date = event['date']
        if event_date not in event_map:
            event_map[event_date] = []
        event_map[event_date].append(dict(event))

    keywords = {"demo": 0, "visit": 0, "call": 0}
    for e in events:
        title = e['title'].lower()
        for key in keywords:
            if key in title:
                keywords[key] += 1

    top_3 = sorted(keywords.items(), key=lambda x: -x[1])[:3]
    suggestions = [label.capitalize() for label, _ in top_3]

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

# ─── EDIT EVENT ────────────────────────────
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
            UPDATE schedule SET title = ?, description = ?, date = ?, time = ?
            WHERE id = ? AND user_id = ?
        ''', (
            request.form['title'],
            request.form['description'],
            request.form['date'],
            request.form['time'],
            id,
            session['user_id']
        ))
        conn.commit()
        conn.close()
        return redirect('/schedule')
    conn.close()
    return render_template('edit_event.html', event=event)

# ─── DELETE EVENT ──────────────────────────
@app.route('/delete_event/<int:id>')
def delete_event(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    conn.execute('DELETE FROM schedule WHERE id = ? AND user_id = ?', (id, session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/schedule')

# ─── START APP ─────────────────────────────
if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5008)
