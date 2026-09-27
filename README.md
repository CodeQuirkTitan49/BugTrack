# BugTrack — DevOps-enabled Bug Tracking System

BugTrack is a Flask + SQLite bug tracking application designed as a practical DevOps demonstration project.

## Application features
- User registration and login
- Admin and Developer roles
- Bug creation, assignment and status lifecycle
- Search and filtering
- Bug detail pages
- Comments and status history
- Dashboard statistics

## DevOps features
- Git/GitHub workflow support
- Visible repository, branch and commit information
- Local CI pipeline that runs automated tests
- Jenkinsfile for CI integration
- Dockerfile for containerization
- Docker runtime detection
- Deployment history and environment status
- Configurable GitHub/Jenkins integration

## Run locally
```bash
python -m venv venv
venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

Default admin for a fresh database: `admin` / `admin123`.

## Test
```bash
python -m pytest -q
```
