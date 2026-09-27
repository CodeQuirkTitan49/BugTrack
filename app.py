from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import sqlite3
import subprocess
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "bugtrack.db")
DEFAULT_GITHUB_REPO = "https://github.com/CodeQuirkTitan49/BugTrack"


def create_app(test_config=None):
    app = Flask(__name__)
    app.secret_key = os.environ.get("BUGTRACK_SECRET", "bugtrack-dev-secret-change-me")
    app.config["DATABASE"] = DATABASE

    if test_config:
        app.config.update(test_config)

    def get_db_connection():
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def column_exists(conn, table, column):
        columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == column for row in columns)

    def add_column_if_missing(conn, table, definition, column):
        if not column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    def init_db():
        conn = get_db_connection()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'developer'
            )
        """)
        add_column_if_missing(conn, "users", "role TEXT NOT NULL DEFAULT 'developer'", "role")
        conn.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")

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
        add_column_if_missing(conn, "bugs", "reporter TEXT", "reporter")
        add_column_if_missing(conn, "bugs", "approval_status TEXT NOT NULL DEFAULT 'Approved'", "approval_status")
        add_column_if_missing(conn, "bugs", "reviewed_by TEXT", "reviewed_by")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bug_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bug_id) REFERENCES bugs(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bug_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bug_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bug_id) REFERENCES bugs(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                commit_hash TEXT,
                branch TEXT,
                source_status TEXT NOT NULL,
                build_status TEXT NOT NULL,
                test_status TEXT NOT NULL,
                docker_status TEXT NOT NULL,
                deploy_status TEXT NOT NULL,
                overall_status TEXT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                duration_seconds REAL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS deployments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                environment TEXT NOT NULL,
                status TEXT NOT NULL,
                triggered_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES ('github_repo', ?)
        """, (DEFAULT_GITHUB_REPO,))
        conn.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES ('jenkins_url', '')
        """)

        # Preserve existing bugs and give them a sensible reporter if possible.
        conn.execute("UPDATE bugs SET reporter = COALESCE(reporter, 'admin')")
        conn.execute("UPDATE bugs SET approval_status = COALESCE(approval_status, 'Approved')")

        # Seed an admin only for a brand-new database.
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO users(username, password, role) VALUES (?, ?, ?)",
                ("admin", generate_password_hash("admin123"), "admin")
            )

        conn.commit()
        conn.close()

    def get_setting(key, default=""):
        conn = get_db_connection()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default

    def set_setting(key, value):
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def current_user():
        return session.get("username")

    def is_admin():
        return session.get("role") == "admin"

    def login_required():
        return "username" in session

    def run_command(command, cwd=BASE_DIR, timeout=30):
        try:
            result = subprocess.run(
                command, cwd=cwd, capture_output=True, text=True,
                timeout=timeout, shell=False
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return 127, "", str(exc)

    def git_info():
        code, branch, _ = run_command(["git", "branch", "--show-current"])
        if code != 0:
            branch = "Not available"
        code, commit, _ = run_command(["git", "rev-parse", "--short", "HEAD"])
        if code != 0:
            commit = "Not available"
        code, message, _ = run_command(["git", "log", "-1", "--pretty=%s"])
        if code != 0:
            message = "No commit available"
        code, status, _ = run_command(["git", "status", "--porcelain"])
        return {
            "branch": branch or "main",
            "commit": commit or "-",
            "message": message or "-",
            "clean": code == 0 and status == ""
        }

    def github_info(repo_url):
        result = {
            "url": repo_url,
            "connected": False,
            "stars": 0,
            "forks": 0,
            "open_issues": 0,
            "default_branch": "main",
            "error": ""
        }
        if not repo_url:
            result["error"] = "Repository URL not configured"
            return result
        try:
            clean = repo_url.rstrip("/")
            if clean.endswith(".git"):
                clean = clean[:-4]
            parts = clean.split("github.com/")[-1].split("/")
            if len(parts) < 2:
                raise ValueError("Expected a GitHub repository URL")
            api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "BugTrack-DevOps"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            result.update({
                "connected": True,
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "open_issues": data.get("open_issues_count", 0),
                "default_branch": data.get("default_branch", "main")
            })
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def docker_info():
        code, output, error = run_command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=8)
        if code != 0:
            return {"available": False, "running": False, "version": "-", "error": error or "Docker is not running"}
        code, containers, error = run_command(["docker", "ps", "--format", "{{.Names}}|{{.Status}}"], timeout=8)
        running = []
        if code == 0:
            for line in containers.splitlines():
                if "|" in line:
                    name, status = line.split("|", 1)
                    running.append({"name": name, "status": status})
        return {"available": True, "running": bool(running), "version": output or "available", "containers": running, "error": error}

    def jenkins_info():
        base = get_setting("jenkins_url", "").strip().rstrip("/")
        if not base:
            return {"configured": False, "status": "Not configured", "url": ""}
        try:
            req = urllib.request.Request(base + "/api/json", headers={"User-Agent": "BugTrack-DevOps"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            return {"configured": True, "status": "Connected", "url": base, "jobs": len(data.get("jobs", []))}
        except Exception as exc:
            return {"configured": True, "status": "Unavailable", "url": base, "error": str(exc)}

    def run_local_pipeline():
        started = datetime.now()
        git = git_info()
        source_status = "PASSED" if git["commit"] != "Not available" else "FAILED"
        build_status = "PASSED"
        if not os.path.exists(os.path.join(BASE_DIR, "app.py")) or not os.path.exists(os.path.join(BASE_DIR, "requirements.txt")):
            build_status = "FAILED"

        test_status = "PASSED"
        code, out, err = run_command([sys.executable, "-m", "pytest", "-q"], timeout=90)
        if code != 0:
            test_status = "FAILED"

        docker = docker_info()
        docker_status = "PASSED" if docker["available"] else "SKIPPED"
        deploy_status = "PASSED"
        overall = "PASSED" if source_status == build_status == test_status == deploy_status == "PASSED" else "FAILED"
        duration = (datetime.now() - started).total_seconds()

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO pipeline_runs
            (commit_hash, branch, source_status, build_status, test_status,
             docker_status, deploy_status, overall_status, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (git["commit"], git["branch"], source_status, build_status,
              test_status, docker_status, deploy_status, overall, duration))
        conn.execute("""
            INSERT INTO deployments(version, environment, status, triggered_by)
            VALUES (?, ?, ?, ?)
        """, (git["commit"], "Development", deploy_status, current_user() or "system"))
        conn.commit()
        conn.close()
        return {
            "source": source_status, "build": build_status, "test": test_status,
            "docker": docker_status, "deploy": deploy_status, "overall": overall,
            "output": (out[-1200:] if out else err[-1200:])
        }

    @app.context_processor
    def inject_globals():
        return {"current_user": current_user(), "current_role": session.get("role")}

    @app.route("/")
    def home():
        return redirect(url_for("dashboard" if login_required() else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            conn = get_db_connection()
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()
            if user and check_password_hash(user["password"], password):
                session["username"] = user["username"]
                session["role"] = user["role"]
                return redirect(url_for("dashboard"))
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if len(username) < 3 or len(password) < 6:
                flash("Username must be 3+ characters and password 6+ characters.", "error")
                return render_template("register.html")
            conn = get_db_connection()
            try:
                conn.execute("INSERT INTO users(username, password, role) VALUES (?, ?, ?)",
                             (username, generate_password_hash(password), "developer"))
                conn.commit()
            except sqlite3.IntegrityError:
                conn.close()
                flash("Username already exists.", "error")
                return render_template("register.html")
            conn.close()
            flash("Account created. You can now log in.", "success")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    def dashboard():
        if not login_required():
            return redirect(url_for("login"))
        conn = get_db_connection()
        stats = {
            "total": conn.execute("SELECT COUNT(*) FROM bugs").fetchone()[0],
            "open": conn.execute("SELECT COUNT(*) FROM bugs WHERE status='Open' AND approval_status='Approved'").fetchone()[0],
            "progress": conn.execute("SELECT COUNT(*) FROM bugs WHERE status='In Progress' AND approval_status='Approved'").fetchone()[0],
            "resolved": conn.execute("SELECT COUNT(*) FROM bugs WHERE status='Resolved' AND approval_status='Approved'").fetchone()[0],
            "critical": conn.execute("SELECT COUNT(*) FROM bugs WHERE priority='Critical' AND status!='Resolved' AND approval_status='Approved'").fetchone()[0],
            "mine": conn.execute("SELECT COUNT(*) FROM bugs WHERE assigned_to=? AND status!='Resolved' AND approval_status='Approved'", (current_user(),)).fetchone()[0],
            "pending": conn.execute("SELECT COUNT(*) FROM bugs WHERE approval_status='Pending'").fetchone()[0],
        }
        recent = conn.execute("SELECT * FROM bugs ORDER BY id DESC LIMIT 6").fetchall()
        last_pipeline = conn.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
        workload = []
        pending_reports = []
        if is_admin():
            developers = conn.execute("SELECT username FROM users WHERE role='developer' ORDER BY username").fetchall()
            for dev in developers:
                assigned_bugs = conn.execute(
                    "SELECT id, title, status FROM bugs WHERE assigned_to=? AND approval_status='Approved' ORDER BY id DESC",
                    (dev["username"],)
                ).fetchall()
                workload.append({
                    "username": dev["username"],
                    "active": sum(1 for b in assigned_bugs if b["status"] != "Resolved"),
                    "total": len(assigned_bugs),
                    "bugs": assigned_bugs,
                })
            pending_reports = conn.execute(
                "SELECT * FROM bugs WHERE approval_status='Pending' ORDER BY id DESC LIMIT 8"
            ).fetchall()
        conn.close()
        return render_template(
            "dashboard.html", stats=stats, recent_bugs=recent,
            last_pipeline=last_pipeline, git=git_info(),
            workload=workload, pending_reports=pending_reports
        )

    @app.route("/bugs")
    def bugs():
        if not login_required():
            return redirect(url_for("login"))
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "")
        priority = request.args.get("priority", "")
        assigned = request.args.get("assigned", "")
        approval = request.args.get("approval", "")
        query = "SELECT * FROM bugs WHERE 1=1"
        params = []
        if q:
            query += " AND (title LIKE ? OR description LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if status:
            query += " AND status = ?"
            params.append(status)
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        if assigned:
            query += " AND assigned_to = ?"
            params.append(assigned)
        if approval:
            query += " AND approval_status = ?"
            params.append(approval)
        query += " ORDER BY id DESC"
        conn = get_db_connection()
        all_bugs = conn.execute(query, params).fetchall()
        developers = conn.execute("SELECT username FROM users WHERE role='developer' ORDER BY username").fetchall()
        conn.close()
        return render_template(
            "bugs.html", bugs=all_bugs, developers=developers,
            q=q, status=status, priority=priority, assigned=assigned, approval=approval
        )

    @app.route("/create_bug", methods=["GET", "POST"])
    def create_bug():
        if not login_required():
            return redirect(url_for("login"))
        conn = get_db_connection()
        developers = conn.execute("SELECT username FROM users WHERE role='developer' ORDER BY username").fetchall()
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            priority = request.form.get("priority", "Medium")
            requested_assignee = request.form.get("assigned_to", "").strip() or None
            if not title:
                conn.close()
                flash("Bug title is required.", "error")
                return render_template("create_bug.html", developers=developers)

            if is_admin():
                assigned_to = requested_assignee
                approval_status = "Approved"
            else:
                assigned_to = None
                approval_status = "Pending"

            if assigned_to:
                exists = conn.execute(
                    "SELECT 1 FROM users WHERE username=? AND role='developer'",
                    (assigned_to,)
                ).fetchone()
                if not exists:
                    conn.close()
                    flash("Selected developer does not exist.", "error")
                    return render_template("create_bug.html", developers=developers)

            cur = conn.execute("""
                INSERT INTO bugs(title, description, priority, status, assigned_to, reporter, approval_status)
                VALUES (?, ?, ?, 'Open', ?, ?, ?)
            """, (title, description, priority, assigned_to, current_user(), approval_status))
            bug_id = cur.lastrowid
            conn.execute(
                "INSERT INTO bug_history(bug_id, username, old_status, new_status) VALUES (?, ?, ?, ?)",
                (bug_id, current_user(), None, "Open")
            )
            conn.commit()
            conn.close()

            if approval_status == "Pending":
                flash(f"BUG-{bug_id:03d} submitted to Admin for review.", "success")
            else:
                flash(f"BUG-{bug_id:03d} created successfully.", "success")
            return redirect(url_for("bugs"))

        conn.close()
        return render_template("create_bug.html", developers=developers)

    @app.route("/bugs/<int:bug_id>")
    def bug_detail(bug_id):
        if not login_required():
            return redirect(url_for("login"))
        conn = get_db_connection()
        bug = conn.execute("SELECT * FROM bugs WHERE id=?", (bug_id,)).fetchone()
        if not bug:
            conn.close()
            return "Bug not found", 404
        comments = conn.execute("SELECT * FROM comments WHERE bug_id=? ORDER BY id DESC", (bug_id,)).fetchall()
        history = conn.execute("SELECT * FROM bug_history WHERE bug_id=? ORDER BY id DESC", (bug_id,)).fetchall()
        developers = conn.execute("SELECT username FROM users WHERE role='developer' ORDER BY username").fetchall()
        conn.close()
        can_update = (bug["approval_status"] == "Approved" and (is_admin() or bug["assigned_to"] == current_user()))
        return render_template(
            "bug_detail.html", bug=bug, comments=comments, history=history,
            can_update=can_update, developers=developers
        )

    @app.route("/bugs/<int:bug_id>/status", methods=["POST"])
    def update_status(bug_id):
        if not login_required():
            return redirect(url_for("login"))
        new_status = request.form.get("status", "Open")
        if new_status not in {"Open", "In Progress", "Resolved"}:
            flash("Invalid status.", "error")
            return redirect(url_for("bug_detail", bug_id=bug_id))
        conn = get_db_connection()
        bug = conn.execute("SELECT * FROM bugs WHERE id=?", (bug_id,)).fetchone()
        if not bug:
            conn.close()
            return "Bug not found", 404
        if bug["approval_status"] != "Approved":
            conn.close()
            return "This issue is waiting for Admin approval.", 403
        if not (is_admin() or bug["assigned_to"] == current_user()):
            conn.close()
            return "Access denied: this bug is not assigned to you.", 403
        old_status = bug["status"]
        conn.execute("UPDATE bugs SET status=? WHERE id=?", (new_status, bug_id))
        if old_status != new_status:
            conn.execute("INSERT INTO bug_history(bug_id, username, old_status, new_status) VALUES (?, ?, ?, ?)",
                         (bug_id, current_user(), old_status, new_status))
        conn.commit()
        conn.close()
        flash("Bug status updated.", "success")
        return redirect(url_for("bug_detail", bug_id=bug_id))

    @app.route("/bugs/<int:bug_id>/review", methods=["POST"])
    def review_bug(bug_id):
        if not login_required() or not is_admin():
            return "Access denied", 403
        decision = request.form.get("decision", "")
        assigned_to = request.form.get("assigned_to", "").strip() or None
        if decision not in {"approve", "reject"}:
            flash("Invalid review action.", "error")
            return redirect(url_for("bug_detail", bug_id=bug_id))
        conn = get_db_connection()
        bug = conn.execute("SELECT * FROM bugs WHERE id=?", (bug_id,)).fetchone()
        if not bug:
            conn.close()
            return "Bug not found", 404
        if decision == "approve":
            if assigned_to:
                exists = conn.execute(
                    "SELECT 1 FROM users WHERE username=? AND role='developer'",
                    (assigned_to,)
                ).fetchone()
                if not exists:
                    conn.close()
                    flash("Developer account not found.", "error")
                    return redirect(url_for("bug_detail", bug_id=bug_id))
            conn.execute(
                "UPDATE bugs SET approval_status='Approved', reviewed_by=?, assigned_to=? WHERE id=?",
                (current_user(), assigned_to, bug_id)
            )
            flash("Issue approved and assignment saved.", "success")
        else:
            conn.execute(
                "UPDATE bugs SET approval_status='Rejected', reviewed_by=?, assigned_to=NULL WHERE id=?",
                (current_user(), bug_id)
            )
            flash("Issue rejected.", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("bug_detail", bug_id=bug_id))

    @app.route("/bugs/<int:bug_id>/assign", methods=["POST"])
    def assign_bug(bug_id):
        if not login_required():
            return redirect(url_for("login"))
        if not is_admin():
            return "Access denied", 403
        assigned_to = request.form.get("assigned_to", "").strip() or None
        conn = get_db_connection()
        if assigned_to:
            exists = conn.execute("SELECT 1 FROM users WHERE username=? AND role='developer'", (assigned_to,)).fetchone()
            if not exists:
                conn.close()
                flash("Developer account not found.", "error")
                return redirect(url_for("bug_detail", bug_id=bug_id))
        conn.execute("UPDATE bugs SET assigned_to=? WHERE id=?", (assigned_to, bug_id))
        conn.commit()
        conn.close()
        flash("Bug assignment updated.", "success")
        return redirect(url_for("bug_detail", bug_id=bug_id))

    @app.route("/bugs/<int:bug_id>/comment", methods=["POST"])
    def add_comment(bug_id):
        if not login_required():
            return redirect(url_for("login"))
        comment = request.form.get("comment", "").strip()
        if not comment:
            flash("Comment cannot be empty.", "error")
            return redirect(url_for("bug_detail", bug_id=bug_id))
        conn = get_db_connection()
        exists = conn.execute("SELECT 1 FROM bugs WHERE id=?", (bug_id,)).fetchone()
        if not exists:
            conn.close()
            return "Bug not found", 404
        conn.execute("INSERT INTO comments(bug_id, username, comment) VALUES (?, ?, ?)",
                     (bug_id, current_user(), comment))
        conn.commit()
        conn.close()
        flash("Comment added.", "success")
        return redirect(url_for("bug_detail", bug_id=bug_id))

    @app.route("/devops")
    def devops():
        if not login_required():
            return redirect(url_for("login"))
        conn = get_db_connection()
        runs = conn.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 8").fetchall()
        deployments = conn.execute("SELECT * FROM deployments ORDER BY id DESC LIMIT 8").fetchall()
        conn.close()
        git = git_info()
        github = github_info(get_setting("github_repo", DEFAULT_GITHUB_REPO))
        docker = docker_info()
        jenkins = jenkins_info()
        return render_template("devops.html", git=git, github=github, docker=docker, jenkins=jenkins,
                               runs=runs, deployments=deployments, repo=get_setting("github_repo", DEFAULT_GITHUB_REPO),
                               jenkins_url=get_setting("jenkins_url", ""))

    @app.route("/devops/run", methods=["POST"])
    def devops_run():
        if not login_required():
            return redirect(url_for("login"))
        result = run_local_pipeline()
        if result["overall"] == "PASSED":
            flash("Pipeline completed successfully.", "success")
        else:
            flash("Pipeline completed with failures. Check the pipeline run details.", "error")
        return redirect(url_for("devops"))

    @app.route("/devops/settings", methods=["POST"])
    def devops_settings():
        if not login_required() or not is_admin():
            return "Access denied", 403
        set_setting("github_repo", request.form.get("github_repo", "").strip())
        set_setting("jenkins_url", request.form.get("jenkins_url", "").strip())
        flash("DevOps integration settings saved.", "success")
        return redirect(url_for("devops"))

    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "BugTrack", "timestamp": datetime.now().isoformat()})

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        print("Database initialized.")

    init_db()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
