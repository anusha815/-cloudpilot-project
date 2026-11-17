import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
DB = BASE / "cloudpilot.db"

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "cloudpilot-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

RENDER_API = "https://api.render.com/v1"


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS deployments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                app_url TEXT DEFAULT '',
                service_id TEXT DEFAULT '',
                dashboard_url TEXT DEFAULT ''
            )
        """)


def render_headers():
    token = os.getenv("RENDER_API_KEY", "").strip()

    if not token:
        raise RuntimeError(
            "Render API key is missing. "
            "Add RENDER_API_KEY in Render Environment settings."
        )

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }


def get_owner_id(headers):
    explicit = os.getenv("RENDER_OWNER_ID", "").strip()

    if explicit:
        return explicit

    response = requests.get(
        f"{RENDER_API}/services?limit=1",
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, list) and data:
        service = data[0].get("service", data[0])
        owner = service.get("ownerId")

        if owner:
            return owner

    raise RuntimeError(
        "Could not identify your Render workspace. "
        "Please configure RENDER_OWNER_ID."
    )


def valid_repo(value):
    value = value.strip()

    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
        value
    )

    if not match:
        raise ValueError(
            "Enter a valid public GitHub repository URL."
        )

    return (
        f"https://github.com/"
        f"{match.group(1)}/{match.group(2)}"
    )


def create_cloud_service(name, repo, branch):

    headers = render_headers()
    owner_id = get_owner_id(headers)

    slug = re.sub(
        r"[^a-z0-9-]+",
        "-",
        name.lower()
    ).strip("-")[:40] or "cloudpilot-app"

    service_name = f"{slug}-{uuid.uuid4().hex[:5]}"

    body = {
        "type": "web_service",
        "name": service_name,
        "ownerId": owner_id,
        "repo": repo,
        "branch": branch or "main",
        "rootDir": "",
        "autoDeployTrigger": "off",
        "serviceDetails": {
            "env": "production",
            "runtime": "python",
            "plan": "free",
            "region": "singapore",
            "numInstances": 1,
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "gunicorn app:app"
            }
        }
    }

    response = requests.post(
        f"{RENDER_API}/services",
        headers=headers,
        json=body,
        timeout=30
    )

    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text

        raise RuntimeError(
            f"Render deployment error "
            f"({response.status_code}): {detail}"
        )

    payload = response.json()
    service = payload.get("service", payload)

    service_id = service.get("id")

    if not service_id:
        raise RuntimeError(
            "Render did not return a service ID."
        )

    return (
        service_id,
        service.get("dashboardUrl", ""),
        service.get("serviceDetails", {}).get("url", "")
    )


@app.context_processor
def inject_globals():
    return {"cloudpilot_name": "CloudPilot"}


@app.route("/")
def index():

    with db() as con:
        rows = con.execute(
            "SELECT * FROM deployments ORDER BY created_at DESC"
        ).fetchall()

        total = len(rows)

        successful = sum(
            row["status"] == "Deployed" for row in rows
        )

        failed = sum(
            row["status"] == "Failed" for row in rows
        )

        running = sum(
            row["status"] in ("Deploying", "Running")
            for row in rows
        )

    return render_template(
        "index.html",
        total=total,
        successful=successful,
        failed=failed,
        running=running,
        recent=rows[:5]
    )


@app.route("/deploy", methods=["GET", "POST"])
def deploy():

    if request.method == "POST":

        name = request.form.get(
            "project_name", ""
        ).strip()

        branch = request.form.get(
            "branch", "main"
        ).strip() or "main"

        try:

            if not name:
                raise ValueError("Enter a project name.")

            repo = valid_repo(
                request.form.get("repo_url", "")
            )

            dep_id = uuid.uuid4().hex

            with db() as con:
                con.execute(
                    """
                    INSERT INTO deployments
                    (id, name, filename, status, created_at, summary)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dep_id,
                        name,
                        repo,
                        "Deploying",
                        datetime.now().strftime(
                            "%d %b %Y, %I:%M %p"
                        ),
                        "Sending deployment request to Render..."
                    )
                )

            service_id, dashboard_url, app_url = (
                create_cloud_service(name, repo, branch)
            )

            with db() as con:
                con.execute(
                    """
                    UPDATE deployments
                    SET service_id=?,
                        dashboard_url=?,
                        app_url=?,
                        summary=?
                    WHERE id=?
                    """,
                    (
                        service_id,
                        dashboard_url,
                        app_url,
                        "Render accepted the deployment request. "
                        "The application is being built.",
                        dep_id
                    )
                )

            return redirect(
                url_for("details", dep_id=dep_id)
            )

        except Exception as exc:

            flash(str(exc), "error")

            return redirect(url_for("deploy"))

    return render_template("deploy.html")


@app.route("/history")
def history():

    with db() as con:
        rows = con.execute(
            "SELECT * FROM deployments ORDER BY created_at DESC"
        ).fetchall()

    return render_template(
        "history.html",
        rows=rows
    )


@app.route("/deployment/<dep_id>")
def details(dep_id):

    with db() as con:
        row = con.execute(
            "SELECT * FROM deployments WHERE id=?",
            (dep_id,)
        ).fetchone()

    if not row:
        flash("Deployment not found.", "error")
        return redirect(url_for("history"))

    return render_template(
        "details.html",
        item=row
    )


@app.route("/how-it-works")
def about():
    return render_template("about.html")


@app.errorhandler(413)
def too_large(_):
    flash("Request is too large.", "error")
    return redirect(url_for("deploy"))


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        debug=False
    )
