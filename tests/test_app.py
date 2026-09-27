import os
import tempfile
import pytest
from app import create_app

@pytest.fixture
def app():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    app = create_app({"TESTING": True, "DATABASE": db_path, "SECRET_KEY": "test-secret"})
    yield app
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass

@pytest.fixture
def client(app):
    return app.test_client()

def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=True)

def register(client, username, password):
    return client.post("/register", data={"username": username, "password": password}, follow_redirects=True)

def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"

def test_default_admin_login(client):
    response = login(client, "admin", "admin123")
    assert response.status_code == 200
    assert b"PROJECT CONTROL CENTER" in response.data

def test_register_developer(client):
    response = register(client, "devone", "dev123")
    assert response.status_code == 200
    assert b"Account created" in response.data
    response = login(client, "devone", "dev123")
    assert b"Developer" in response.data

def test_admin_create_and_view_bug(client):
    login(client, "admin", "admin123")
    response = client.post("/create_bug", data={
        "title": "Login button broken",
        "description": "The login button does not respond.",
        "priority": "High",
        "assigned_to": ""
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Login button broken" in response.data
    assert b"Approved" in response.data

def test_developer_report_requires_admin_review(client):
    register(client, "devreporter", "dev123")
    login(client, "devreporter", "dev123")
    response = client.post("/create_bug", data={
        "title": "Developer reported issue",
        "description": "Needs triage.",
        "priority": "High",
        "assigned_to": ""
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"submitted to Admin" in response.data
    assert b"Pending review" in response.data

def test_admin_can_approve_and_assign(client):
    register(client, "devapprove", "dev123")
    login(client, "devapprove", "dev123")
    client.post("/create_bug", data={
        "title": "Needs approval",
        "description": "Admin should triage this.",
        "priority": "Medium",
        "assigned_to": ""
    })
    client.get("/logout")
    login(client, "admin", "admin123")
    response = client.post("/bugs/1/review", data={
        "decision": "approve",
        "assigned_to": "devapprove"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Approved" in response.data
    assert b"devapprove" in response.data

def test_unassigned_developer_cannot_update(client):
    login(client, "admin", "admin123")
    client.post("/create_bug", data={
        "title": "Permission test",
        "description": "Not assigned to devtwo.",
        "priority": "Medium",
        "assigned_to": ""
    })
    client.get("/logout")
    register(client, "devtwo", "dev123")
    login(client, "devtwo", "dev123")
    response = client.post("/bugs/1/status", data={"status": "Resolved"})
    assert response.status_code == 403

def test_assigned_developer_can_update(client):
    login(client, "admin", "admin123")
    register(client, "devthree", "dev123")
    client.post("/create_bug", data={
        "title": "Assigned bug",
        "description": "Permission test",
        "priority": "High",
        "assigned_to": "devthree"
    })
    client.get("/logout")
    login(client, "devthree", "dev123")
    response = client.post("/bugs/1/status", data={"status": "In Progress"})
    assert response.status_code == 302

def test_filters(client):
    login(client, "admin", "admin123")
    client.post("/create_bug", data={"title": "Critical login", "description": "x", "priority": "Critical", "assigned_to": ""})
    client.post("/create_bug", data={"title": "Low UI", "description": "y", "priority": "Low", "assigned_to": ""})
    response = client.get("/bugs?priority=Critical")
    assert b"Critical login" in response.data
    assert b"Low UI" not in response.data
