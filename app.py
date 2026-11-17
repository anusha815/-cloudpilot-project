import os, re, sqlite3, uuid
from datetime import datetime
from pathlib import Path
import requests
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).resolve().parent
DB = BASE / 'cloudpilot.db'
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-this-secret-key')
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
RENDER_API = 'https://api.render.com/v1'


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, filename TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL,
            app_url TEXT DEFAULT '', service_id TEXT DEFAULT '', dashboard_url TEXT DEFAULT '')''')
        cols = {r['name'] for r in con.execute('PRAGMA table_info(deployments)')}
        for col, definition in [('service_id', "TEXT DEFAULT ''"), ('dashboard_url', "TEXT DEFAULT ''")]:
            if col not in cols:
                con.execute(f'ALTER TABLE deployments ADD COLUMN {col} {definition}')


def render_headers():
    token = os.getenv('RENDER_API_KEY', '').strip()
    if not token:
        raise RuntimeError('Render API key is not configured. Add RENDER_API_KEY in your Render service Environment settings.')
    return {'Authorization': f'Bearer {token}', 'Accept': 'application/json', 'Content-Type': 'application/json'}


def get_owner_id(headers):
    explicit = os.getenv('RENDER_OWNER_ID', '').strip()
    if explicit:
        return explicit
    response = requests.get(f'{RENDER_API}/services?limit=1', headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list) and data:
        first = data[0]
        service = first.get('service', first)
        owner = service.get('ownerId')
        if owner:
            return owner
    raise RuntimeError('Could not identify your Render workspace. Add RENDER_OWNER_ID in Render Environment settings.')


def valid_repo(value):
    value = value.strip()
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', value)
    if not match:
        raise ValueError('Enter a valid public GitHub repository URL, such as https://github.com/render-examples/flask-hello-world')
    return f'https://github.com/{match.group(1)}/{match.group(2)}'


def create_cloud_service(name, repo, branch):
    headers = render_headers()
    owner_id = get_owner_id(headers)
    slug = re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')[: forty()] or 'cloudpilot-app'
    # Use a unique suffix so repeated deployments never collide.
    service_name = f'{slug}-{uuid.uuid4().hex[:5]}'
    body = {
        'type': 'web_service', 'name': service_name, 'ownerId': owner_id,
        'repo': repo, 'branch': branch or 'main', 'autoDeployTrigger': 'off',
        'serviceDetails': {
            'runtime': 'python', 'buildCommand': 'pip install -r requirements.txt',
            'startCommand': 'gunicorn app:app', 'plan': 'free', 'region': 'singapore'
        }
    }
    response = requests.post(f'{RENDER_API}/services', headers=headers, json=body, timeout=30)
    if not response.ok:
        try: detail = response.json()
        except Exception: detail = response.text
        raise RuntimeError(f'Render could not create the service ({response.status_code}): {detail}')
    payload = response.json()
    service = payload.get('service', payload)
    service_id = service.get('id')
    if not service_id:
        raise RuntimeError(f'Render accepted the request but did not return a service ID: {payload}')
    return service_id, service.get('dashboardUrl', ''), service.get('serviceDetails', {}).get('url', '')


def forty():
    return 40


def refresh_status(row):
    if not row['service_id'] or row['status'] in ('Failed', 'Deployed'):
        return
    try:
        headers = render_headers()
        resp = requests.get(f"{RENDER_API}/services/{row['service_id']}/deploys?limit=1", headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        latest = data[0].get('deploy', data[0]) if isinstance(data, list) and data else {}
        state = (latest.get('status') or '').lower()
        mapping = {
            'live': 'Deployed', 'build_failed': 'Failed', 'update_failed': 'Failed',
            'canceled': 'Failed', 'pre_deploy_failed': 'Failed',
            'build_in_progress': 'Deploying', 'pre_deploy_in_progress': 'Deploying',
            'deploy_in_progress': 'Deploying', 'created': 'Deploying', 'queued': 'Deploying'
        }
        new_status = mapping.get(state)
        if new_status:
            summary = f"Render deployment status: {state.replace('_', ' ')}.\n\nOpen the Render dashboard for complete build and runtime logs."
            with db() as con:
                con.execute('UPDATE deployments SET status=?, summary=? WHERE id=?', (new_status, summary, row['id']))
    except Exception:
        pass


@app.context_processor
def inject_globals():
    return {'cloudpilot_name': 'CloudPilot'}


@app.route('/')
def index():
    with db() as con:
        rows = con.execute('SELECT * FROM deployments ORDER BY created_at DESC').fetchall()
        total = len(rows)
        successful = sum(r['status'] == 'Deployed' for r in rows)
        failed = sum(r['status'] == 'Failed' for r in rows)
        running = sum(r['status'] in ('Deploying', 'Running') for r in rows)
    return render_template('index.html', total=total, successful=successful, failed=failed, running=running, recent=rows[:5])


@app.route('/deploy', methods=['GET', 'POST'])
def deploy():
    if request.method == 'POST':
        name = request.form.get('project_name', '').strip()
        branch = request.form.get('branch', 'main').strip() or 'main'
        try:
            if not name:
                raise ValueError('Enter a project name.')
            repo = valid_repo(request.form.get('repo_url', ''))
            dep_id = uuid.uuid4().hex
            with db() as con:
                con.execute('INSERT INTO deployments (id,name,filename,status,created_at,summary) VALUES (?,?,?,?,?,?)',
                            (dep_id, name, repo, 'Deploying', datetime.now().strftime('%d %b %Y, %I:%M %p'), 'Sending deployment request to Render…'))
            service_id, dashboard_url, app_url = create_cloud_service(name, repo, branch)
            with db() as con:
                con.execute('UPDATE deployments SET service_id=?, dashboard_url=?, app_url=?, summary=? WHERE id=?',
                            (service_id, dashboard_url, app_url, 'Render accepted the deployment request. The cloud build and deployment are now running. Check the deployment details for live status and logs.', dep_id))
            return redirect(url_for('details', dep_id=dep_id))
        except Exception as exc:
            flash(str(exc), 'error')
            return redirect(url_for('deploy'))
    return render_template('deploy.html')


@app.route('/history')
def history():
    with db() as con: rows = con.execute('SELECT * FROM deployments ORDER BY created_at DESC').fetchall()
    for row in rows: refresh_status(row)
    with db() as con: rows = con.execute('SELECT * FROM deployments ORDER BY created_at DESC').fetchall()
    return render_template('history.html', rows=rows)


@app.route('/deployment/<dep_id>')
def details(dep_id):
    with db() as con: row = con.execute('SELECT * FROM deployments WHERE id=?', (dep_id,)).fetchone()
    if not row:
        flash('Deployment not found.', 'error')
        return redirect(url_for('history'))
    refresh_status(row)
    with db() as con: row = con.execute('SELECT * FROM deployments WHERE id=?', (dep_id,)).fetchone()
    return render_template('details.html', item=row)


@app.route('/how-it-works')
def about(): return render_template('about.html')


@app.errorhandler(413)
def too_large(_):
    flash('Request is too large.', 'error')
    return redirect(url_for('deploy'))


init_db()
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)), debug=False)
