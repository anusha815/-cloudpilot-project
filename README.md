# CloudPilot — Automated Cloud Application Deployment Platform

CloudPilot is a Flask dashboard that requests real Render web-service deployments from public GitHub repositories using the Render REST API. Render performs the build and hosts each submitted application.

## What this version does

- Hosts the CloudPilot dashboard.
- Accepts a project name, public GitHub repository URL, and branch.
- Requests Render to create a separate Python web service on the Free plan.
- Records deployment requests and checks the latest Render deployment state.
- Links to the deployed app and its Render dashboard when those URLs are returned.

## Important change from the earlier version

This version deploys from a **public GitHub repository URL**. It does not deploy an uploaded ZIP file directly. The submitted app repository must contain `app.py` with a Flask application object named `app`, plus `requirements.txt`. Its default start command is `gunicorn app:app`.

## Configure the existing CloudPilot service on Render

1. Create a Render API key in Render Account Settings → API Keys.
2. In the existing CloudPilot web service, open **Environment** and add `RENDER_API_KEY` with that key as its value. Never put the key in this repository or share it in screenshots or chat.
3. `RENDER_OWNER_ID` is optional. CloudPilot tries to detect the workspace from the services visible to the API key. If detection fails, add the workspace ID from Render.
4. Deploy this repository to the existing CloudPilot service using the Docker runtime / Dockerfile. Keep the service's existing public URL.

## Run locally

```bash
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:10000`.

## Sample application

`sample-python-app/` contains a small Flask app and a test file. To use CloudPilot's deployment form, first put that sample app in its own public GitHub repository, then submit that repository URL.

## Limitations and cost warning

- Only public GitHub repositories are supported in this version.
- The first version targets Python/Flask web apps.
- It creates a separate Render service for each deployment request. The code requests the `free` plan, but service availability and account limits are controlled by Render. Do not proceed if Render displays a paid plan or asks for payment.
- Free Render services can sleep when inactive.
- The dashboard database uses local SQLite storage; on a hosting platform without a persistent disk, records may not survive a rebuild or restart.
- This is a student project prototype, not a secure multi-tenant production deployment platform. Do not deploy untrusted repositories or repositories containing secrets.
