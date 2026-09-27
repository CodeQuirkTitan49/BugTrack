from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "bugtrack-secret-key"

DATABASE = "bugtrack.db"


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()

    # Users table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'developer'
        )
    """)

    # Add role column to existing database if it doesn't exist
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'developer'"
        )
    except sqlite3.OperationalError:
        pass

    # Existing admin account becomes Admin
    conn.execute(
        "UPDATE users SET role = 'admin' WHERE username = 'admin'"
    )

    # Bugs table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bugs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            priority TEXT NOT NULL,
            status TEXT NOT NULL,
            assigned_to TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        conn = get_db_connection()

        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):

            session["username"] = user["username"]
            session["role"] = user["role"]

            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid username or password"
        )

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()

        try:
            conn.execute(
                """
                INSERT INTO users (username, password, role)
                VALUES (?, ?, ?)
                """,
                (username, hashed_password, "developer")
            )

            conn.commit()

        except sqlite3.IntegrityError:

            conn.close()

            return render_template(
                "register.html",
                error="Username already exists"
            )

        conn.close()

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/dashboard")
def dashboard():

    if "username" not in session:
        return redirect(url_for("login"))

    conn = get_db_connection()

    total = conn.execute(
        "SELECT COUNT(*) FROM bugs"
    ).fetchone()[0]

    open_bugs = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'Open'"
    ).fetchone()[0]

    in_progress = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'In Progress'"
    ).fetchone()[0]

    resolved = conn.execute(
        "SELECT COUNT(*) FROM bugs WHERE status = 'Resolved'"
    ).fetchone()[0]

    recent_bugs = conn.execute(
        "SELECT * FROM bugs ORDER BY id DESC LIMIT 5"
    ).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        username=session["username"],
        role=session["role"],
        total=total,
        open_bugs=open_bugs,
        in_progress=in_progress,
        resolved=resolved,
        recent_bugs=recent_bugs
    )


@app.route("/create_bug", methods=["GET", "POST"])
def create_bug():

    if "username" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":

        title = request.form["title"]
        description = request.form["description"]
        priority = request.form["priority"]
        assigned_to = request.form["assigned_to"]

        conn = get_db_connection()

        conn.execute("""
            INSERT INTO bugs
            (title, description, priority, status, assigned_to)
            VALUES (?, ?, ?, ?, ?)
        """, (
            title,
            description,
            priority,
            "Open",
            assigned_to
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("dashboard"))

    return render_template("create_bug.html")


@app.route("/bugs")
def bugs():

    if "username" not in session:
        return redirect(url_for("login"))

    conn = get_db_connection()

    all_bugs = conn.execute(
        "SELECT * FROM bugs ORDER BY id DESC"
    ).fetchall()

    conn.close()

    return render_template(
        "bugs.html",
        bugs=all_bugs,
        role=session["role"]
    )


@app.route("/update_status/<int:bug_id>", methods=["POST"])
def update_status(bug_id):

    if "username" not in session:
        return redirect(url_for("login"))

    status = request.form["status"]

    conn = get_db_connection()

    bug = conn.execute(
        "SELECT * FROM bugs WHERE id = ?",
        (bug_id,)
    ).fetchone()

    if bug is None:

        conn.close()

        return "Bug not found", 404

    # Admin can update any bug.
    # Developer can update only bugs assigned to them.
    if (
        session["role"] == "admin"
        or bug["assigned_to"] == session["username"]
    ):

        conn.execute(
            "UPDATE bugs SET status = ? WHERE id = ?",
            (status, bug_id)
        )

        conn.commit()
        conn.close()

        return redirect(url_for("bugs"))

    conn.close()

    return """
        <h2>Access Denied</h2>
        <p>You can only update bugs assigned to you.</p>
        <a href="/bugs">Back to Bugs</a>
    """, 403


@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)