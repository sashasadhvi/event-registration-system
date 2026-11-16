"""
Event / Workshop Registration System  (Product 8 - Mutex / Critical Section)
=============================================================================
Problem : Several users try to register when only one or two places remain.
Shared  : the remaining capacity of an event (registrations table / count).
Fix     : a single mutex (threading.Lock) protects the critical section
          "check capacity -> check duplicate -> insert registration".

Run:  python app.py      then open http://127.0.0.1:5000
"""
import collections
import os
import sqlite3
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "registration.db"))

app = Flask(__name__, static_folder="static", static_url_path="")

# --------------------------------------------------------------------------
# THE MUTEX  -  one lock guarding the shared capacity data
# --------------------------------------------------------------------------
registration_lock = threading.Lock()

# Activity log so the UI can show REQUEST -> ACQUIRE -> RELEASE in real time.
LOG = collections.deque(maxlen=300)
_log_seq = 0


def log(who, action, detail=""):
    global _log_seq
    _log_seq += 1
    LOG.append({
        "n": _log_seq,
        "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "thread": who,
        "action": action,
        "detail": detail,
    })


class LoggedMutex:
    """Context manager = Entry section / Exit section around the lock."""

    def __init__(self, lock):
        self.lock = lock

    def __enter__(self):
        self.who = threading.current_thread().name
        log(self.who, "REQUEST", "wants to enter the critical section")
        started = time.perf_counter()
        self.lock.acquire()                                   # ENTRY SECTION
        waited = (time.perf_counter() - started) * 1000
        log(self.who, "ACQUIRE", f"lock acquired (waited {waited:.0f} ms)")
        return self

    def __exit__(self, *exc):
        log(self.who, "RELEASE", "leaving critical section, lock released")
        self.lock.release()                                   # EXIT SECTION
        return False


mutex = LoggedMutex(registration_lock)


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(reset=False):
    conn = get_db()
    if reset:
        conn.executescript("DROP TABLE IF EXISTS registrations; DROP TABLE IF EXISTS events;")
    # NOTE: no UNIQUE(event_id,email) on purpose. Duplicate prevention is done
    # by the mutex-protected code so the 'unsafe' demo can really show duplicates.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            venue TEXT NOT NULL,
            date TEXT NOT NULL,
            capacity INTEGER NOT NULL CHECK (capacity > 0)
        );
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id),
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    if conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO events (title, venue, date, capacity) VALUES (?,?,?,?)",
            [
                ("Operating Systems Workshop", "Lab 2, CS Block", "2026-12-05 10:00", 30),
                ("Python Concurrency Bootcamp", "Seminar Hall A", "2026-12-12 14:00", 20),
                ("Last Chance: Mutex Masterclass", "Room 204", "2026-12-18 11:00", 2),
            ],
        )
        conn.commit()
    conn.close()


def event_row_to_dict(conn, ev):
    registered = conn.execute(
        "SELECT COUNT(*) FROM registrations WHERE event_id=?", (ev["id"],)
    ).fetchone()[0]
    return {
        "id": ev["id"],
        "title": ev["title"],
        "venue": ev["venue"],
        "date": ev["date"],
        "capacity": ev["capacity"],
        "registered": registered,
        "remaining": ev["capacity"] - registered,
        "overbooked": registered > ev["capacity"],
    }


# --------------------------------------------------------------------------
# CORE LOGIC  -  the critical section
# --------------------------------------------------------------------------
def _critical_section(conn, event_id, name, email, delay):
    """
    Check seats -> check duplicate -> (processing) -> write registration.
    This block reads AND modifies shared data, so two threads must never
    run it at the same time for the same event.
    """
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if ev is None:
        return {"ok": False, "code": "not_found", "message": "Event not found."}

    count = conn.execute(
        "SELECT COUNT(*) FROM registrations WHERE event_id=?", (event_id,)
    ).fetchone()[0]
    if count >= ev["capacity"]:
        return {"ok": False, "code": "full", "message": "Sorry, this event is full."}

    dup = conn.execute(
        "SELECT 1 FROM registrations WHERE event_id=? AND lower(email)=lower(?)",
        (event_id, email),
    ).fetchone()
    if dup:
        return {"ok": False, "code": "duplicate",
                "message": "This email is already registered for the event."}

    if delay:
        time.sleep(delay)   # models network / payment / email latency between CHECK and WRITE

    cur = conn.execute(
        "INSERT INTO registrations (event_id, name, email, created_at) VALUES (?,?,?,?)",
        (event_id, name, email, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return {
        "ok": True, "code": "registered",
        "message": "Registration confirmed!",
        "registration_id": cur.lastrowid,
        "ticket": f"EVT{event_id}-{cur.lastrowid:04d}",
    }


def register(event_id, name, email, safe=True, delay=0.0):
    conn = get_db()
    try:
        if safe:
            with mutex:                                   # <-- MUTEX PROTECTED
                return _critical_section(conn, event_id, name, email, delay)
        # UNSAFE: same code, no lock -> race condition (for demonstration only)
        who = threading.current_thread().name
        log(who, "NO-LOCK", "entering critical section WITHOUT a mutex")
        return _critical_section(conn, event_id, name, email, delay)
    finally:
        conn.close()


def cancel(registration_id, email):
    conn = get_db()
    try:
        with mutex:                                       # capacity changes -> protect it too
            row = conn.execute(
                "SELECT * FROM registrations WHERE id=?", (registration_id,)
            ).fetchone()
            if row is None:
                return {"ok": False, "message": "Registration not found."}
            if row["email"].lower() != email.lower():
                return {"ok": False, "message": "Email does not match this registration."}
            conn.execute("DELETE FROM registrations WHERE id=?", (registration_id,))
            conn.commit()
            return {"ok": True, "message": "Registration cancelled. The place is open again."}
    finally:
        conn.close()


# --------------------------------------------------------------------------
# REST API
# --------------------------------------------------------------------------
def err(message, status=400):
    return jsonify({"ok": False, "message": message}), status


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/events")
def list_events():
    conn = get_db()
    events = [event_row_to_dict(conn, e) for e in
              conn.execute("SELECT * FROM events ORDER BY date").fetchall()]
    conn.close()
    return jsonify(events)


@app.post("/api/events")
def create_event():
    d = request.get_json(silent=True) or {}
    title, venue, date = (d.get("title") or "").strip(), (d.get("venue") or "").strip(), (d.get("date") or "").strip()
    try:
        capacity = int(d.get("capacity", 0))
    except (TypeError, ValueError):
        capacity = 0
    if not (title and venue and date) or capacity < 1 or capacity > 10000:
        return err("Title, venue, date and a capacity between 1 and 10000 are required.")
    conn = get_db()
    cur = conn.execute("INSERT INTO events (title, venue, date, capacity) VALUES (?,?,?,?)",
                       (title, venue, date, capacity))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": cur.lastrowid}), 201


@app.post("/api/events/<int:event_id>/register")
def api_register(event_id):
    d = request.get_json(silent=True) or {}
    name, email = (d.get("name") or "").strip(), (d.get("email") or "").strip()
    if not name or "@" not in email:
        return err("A name and a valid email are required.")
    result = register(event_id, name, email, safe=True, delay=0.05)
    status = 200 if result["ok"] else (404 if result["code"] == "not_found" else 409)
    return jsonify(result), status


@app.get("/api/events/<int:event_id>/registrations")
def event_registrations(event_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, email, created_at FROM registrations WHERE event_id=? ORDER BY id",
        (event_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/registrations")
def my_registrations():
    email = (request.args.get("email") or "").strip()
    if not email:
        return err("Email is required.")
    conn = get_db()
    rows = conn.execute("""
        SELECT r.id, r.name, r.email, r.created_at, e.title, e.date, e.venue, r.event_id
        FROM registrations r JOIN events e ON e.id = r.event_id
        WHERE lower(r.email)=lower(?) ORDER BY e.date""", (email,)).fetchall()
    conn.close()
    return jsonify([dict(r) | {"ticket": f"EVT{r['event_id']}-{r['id']:04d}"} for r in rows])


@app.post("/api/registrations/<int:reg_id>/cancel")
def api_cancel(reg_id):
    d = request.get_json(silent=True) or {}
    result = cancel(reg_id, (d.get("email") or "").strip())
    return jsonify(result), (200 if result["ok"] else 400)


# ---- Race Lab -------------------------------------------------------------
@app.post("/api/simulate")
def simulate():
    """Fire N simultaneous registration requests at one event."""
    d = request.get_json(silent=True) or {}
    try:
        event_id = int(d.get("event_id"))
        users = max(2, min(int(d.get("users", 10)), 60))
    except (TypeError, ValueError):
        return err("event_id and users must be numbers.")
    safe = bool(d.get("safe", True))
    same_user = bool(d.get("same_user", False))   # duplicate-registration test
    delay = 0.05

    LOG.clear()
    results, res_lock = [], threading.Lock()
    barrier = threading.Barrier(users)            # make everybody start at the same instant

    def worker(i):
        barrier.wait()
        email = "double.click@sim.test" if same_user else f"user{i:02d}@sim.test"
        r = register(event_id, f"Sim User {i:02d}", email, safe=safe, delay=delay)
        with res_lock:
            results.append({"user": f"User-{i:02d}", "ok": r["ok"], "code": r["code"]})

    threads = [threading.Thread(target=worker, args=(i,), name=f"User-{i:02d}")
               for i in range(1, users + 1)]
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = (time.perf_counter() - t0) * 1000

    conn = get_db()
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if ev is None:
        conn.close()
        return err("Event not found.", 404)
    info = event_row_to_dict(conn, ev)
    dup_emails = conn.execute("""SELECT COUNT(*) FROM (SELECT lower(email) e FROM registrations
        WHERE event_id=? GROUP BY e HAVING COUNT(*)>1)""", (event_id,)).fetchone()[0]
    conn.close()
    results.sort(key=lambda r: r["user"])
    return jsonify({
        "mode": "safe (mutex)" if safe else "UNSAFE (no mutex)",
        "users": users,
        "accepted": sum(r["ok"] for r in results),
        "rejected_full": sum(r["code"] == "full" for r in results),
        "rejected_duplicate": sum(r["code"] == "duplicate" for r in results),
        "elapsed_ms": round(elapsed),
        "event": info,
        "duplicate_emails": dup_emails,
        "problem_detected": info["overbooked"] or dup_emails > 0,
        "results": results,
    })


@app.post("/api/events/<int:event_id>/clear-simulated")
def clear_simulated(event_id):
    conn = get_db()
    with mutex:
        conn.execute("DELETE FROM registrations WHERE event_id=? AND email LIKE '%@sim.test'", (event_id,))
        conn.commit()
    conn.close()
    LOG.clear()
    return jsonify({"ok": True})


@app.get("/api/log")
def get_log():
    return jsonify(list(LOG))


@app.post("/api/reset")
def reset():
    with mutex:
        init_db(reset=True)
    LOG.clear()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
