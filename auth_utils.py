import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

DB = 'calls.db'

def create_user(username, password, name):
    hashed = generate_password_hash(password)
    conn = sqlite3.connect(DB)
    conn.execute('INSERT INTO users (username, password, name) VALUES (?, ?, ?)', (username, hashed, name))
    conn.commit()
    conn.close()

def verify_user(username, password):
    conn = sqlite3.connect(DB)
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    if user and check_password_hash(user[2], password):
        return user
    return None
