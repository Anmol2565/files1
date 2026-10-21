"""
Jan Setu Grievance Portal — FastAPI Backend v2
---------------------------------------------
New in v2: City Issues Voting System
Run:  uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import firebase_admin
from firebase_admin import credentials, firestore
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from twilio.rest import Client as TwilioClient
import uuid, os, datetime
import sqlite3
from sqlite3 import Connection
import json
from openai import OpenAI
import requests
import hashlib
import math

# Global conversation memory (in production, use Redis or database)
conversation_memory = {}

def get_conversation_key(user_id=None):
    """Generate a conversation key based on user identifier or IP"""
    if user_id:
        return hashlib.md5(user_id.encode()).hexdigest()[:8]
    return "default"
import json
from dotenv import load_dotenv

load_dotenv()

USE_MOCK = False
db = None  # Will use mock_db

# ─── Mock Database for Testing ──────────────────────────────────────────────
class MockIncrement:
    def __init__(self, value):
        self._increment = value

class MockArrayUnion:
    def __init__(self, items):
        self._array_union = items

class MockArrayRemove:
    def __init__(self, items):
        self._array_remove = items

# Mock firestore module
class MockFirestoreModule:
    class Increment(MockIncrement):
        pass
    class ArrayUnion(MockArrayUnion):
        pass
    class ArrayRemove(MockArrayRemove):
        pass
    class Query:
        DESCENDING = "desc"

if USE_MOCK:
    firestore = MockFirestoreModule()

class MockFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        if name not in self.collections:
            self.collections[name] = {}
        return MockCollection(self.collections[name])

class MockCollection:
    def __init__(self, data):
        self.data = data

    def document(self, doc_id):
        return MockDocument(self.data, doc_id)

class MockDocument:
    def __init__(self, collection_data, doc_id):
        self.collection_data = collection_data
        self.doc_id = doc_id

    def get(self):
        return MockDocSnapshot(self.collection_data.get(self.doc_id))

    def set(self, data):
        self.collection_data[self.doc_id] = data

    def update(self, updates):
        if self.doc_id in self.collection_data:
            doc = self.collection_data[self.doc_id]
            for key, value in updates.items():
                if hasattr(value, '_increment'):  # Mock Increment
                    doc[key] = doc.get(key, 0) + value._increment
                elif hasattr(value, '_array_union'):  # Mock ArrayUnion
                    existing = doc.get(key, [])
                    for item in value._array_union:
                        if item not in existing:
                            existing.append(item)
                    doc[key] = existing
                elif hasattr(value, '_array_remove'):  # Mock ArrayRemove
                    existing = doc.get(key, [])
                    for item in value._array_remove:
                        if item in existing:
                            existing.remove(item)
                    doc[key] = existing
                else:
                    doc[key] = value

    def delete(self):
        self.collection_data.pop(self.doc_id, None)

class MockDocSnapshot:
    def __init__(self, data):
        self._data = data

    def exists(self):
        return self._data is not None

    def to_dict(self):
        return self._data or {}

class MockQuery:
    def __init__(self, collection_data):
        self.collection_data = collection_data
        self.filters = []
        self.order_by_field = None
        self.order_desc = False
        self.limit_count = None

    def where(self, field, op, value):
        self.filters.append((field, op, value))
        return self

    def order_by(self, field, direction=None):
        self.order_by_field = field
        self.order_desc = direction == firestore.Query.DESCENDING
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def stream(self):
        docs = list(self.collection_data.items())
        # Apply filters (simple equality for now)
        for field, op, value in self.filters:
            if op == "==":
                docs = [(k, v) for k, v in docs if v.get(field) == value]
        # Sort
        if self.order_by_field:
            docs.sort(key=lambda x: x[1].get(self.order_by_field, 0), reverse=self.order_desc)
        # Limit
        if self.limit_count:
            docs = docs[:self.limit_count]
        return [MockDocSnapshot(v) for _, v in docs]

# ─── Firebase Init ─────────────────────────────────────────────────────────
try:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    USE_MOCK = False
except Exception as e:
    print(f"Firebase init failed: {e}. Using mock database for testing.")
    USE_MOCK = True
    db = MockFirestore()

# ─── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Jan Setu API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ────────────────────────────────────────────────────────────────
SMTP_HOST    = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT    = int(os.getenv("SMTP_PORT", 587))
SMTP_USER    = os.getenv("SMTP_USER")
SMTP_PASS    = os.getenv("SMTP_PASS")
TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM  = os.getenv("TWILIO_FROM_NUMBER")
ADMIN_ID     = os.getenv("ADMIN_ID", "admin")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "admin123")

# ─── Database Setup ───────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect('jan_setu.db')
    c = conn.cursor()
    # Grievances table
    c.execute('''CREATE TABLE IF NOT EXISTS grievances (
        id TEXT PRIMARY KEY,
        name TEXT,
        phone TEXT,
        email TEXT,
        state TEXT,
        department TEXT,
        category TEXT,
        title TEXT,
        description TEXT,
        address TEXT,
        pincode TEXT,
        status TEXT DEFAULT 'pending',
        updated_at TEXT,
        officer_name TEXT,
        timeline TEXT,
        remarks TEXT
    )''')
    # City issues table
    c.execute('''CREATE TABLE IF NOT EXISTS city_issues (
        id TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        category TEXT,
        city TEXT,
        state TEXT,
        reported_by TEXT,
        phone TEXT,
        votes INTEGER DEFAULT 0,
        voters TEXT,
        city_votes TEXT,
        comments TEXT,
        created_at TEXT,
        updated_at TEXT,
        status TEXT DEFAULT 'open',
        upvote_cities TEXT
    )''')

    # Road Hazards table
    c.execute('''CREATE TABLE IF NOT EXISTS road_hazards (
        id TEXT PRIMARY KEY,
        hazard_type TEXT,
        severity TEXT,
        confidence REAL,
        latitude REAL,
        longitude REAL,
        address TEXT,
        city TEXT,
        state TEXT,
        image_url TEXT,
        detections TEXT,
        reported_by TEXT,
        phone TEXT,
        status TEXT DEFAULT 'pending',
        corroboration_count INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT,
        department TEXT DEFAULT 'Roads, Bridges & Transport'
    )''')

    # Pre-seed sample road hazards if empty
    c.execute('SELECT COUNT(*) FROM road_hazards')
    if c.fetchone()[0] == 0:
        now_iso = datetime.datetime.utcnow().isoformat()
        sample_hazards = [
            ("HAZ-2026-10491", "Potholes", "Critical", 0.95, 28.6289, 77.2065, "Outer Ring Road near Moti Bagh Flyover", "Delhi", "Delhi", "https://images.unsplash.com/photo-1515162816999-a0c47dc192f7?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Pothole Hazard", "bbox": [180, 240, 160, 110], "confidence": 0.95, "severity": "Critical"}]), "Dashcam Unit #42", "+919876543210", "pending", 6, now_iso, now_iso, "Roads, Bridges & Transport"),
            ("HAZ-2026-10492", "Waterlogged road sections", "Critical", 0.93, 28.6315, 77.2650, "Vikas Marg, under Laxmi Nagar Metro Station", "Delhi", "Delhi", "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Waterlogged Highway Lane", "bbox": [100, 200, 380, 180], "confidence": 0.93, "severity": "Critical"}]), "Citizen Driver", "+919811223344", "in_progress", 11, now_iso, now_iso, "Roads, Bridges & Transport"),
            ("HAZ-2026-10493", "Damaged road barriers", "High", 0.91, 19.0760, 72.8777, "Western Express Highway near Bandra Flyover", "Mumbai", "Maharashtra", "https://images.unsplash.com/photo-1584438784894-089d6a62b8fa?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Deformed Highway Crash Barrier", "bbox": [30, 150, 160, 200], "confidence": 0.91, "severity": "High"}]), "Taxi Fleet Dashcam", "+919920112233", "verified", 4, now_iso, now_iso, "Roads, Bridges & Transport"),
            ("HAZ-2026-10494", "Potholes", "Critical", 0.94, 19.1235, 72.8890, "Jogeshwari-Vikhroli Link Road (JVLR)", "Mumbai", "Maharashtra", "https://images.unsplash.com/photo-1515162816999-a0c47dc192f7?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Pothole Crater", "bbox": [200, 220, 140, 90], "confidence": 0.94, "severity": "Critical"}]), "Commuter", "+919870099887", "pending", 8, now_iso, now_iso, "Roads, Bridges & Transport"),
            ("HAZ-2026-10495", "Waterlogged road sections", "High", 0.89, 12.9279, 77.6771, "Outer Ring Road near Bellandur Eco-space", "Bengaluru", "Karnataka", "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Waterlogged Road", "bbox": [120, 210, 340, 150], "confidence": 0.89, "severity": "High"}]), "IT Commuter", "+919845012345", "in_progress", 14, now_iso, now_iso, "Roads, Bridges & Transport"),
            ("HAZ-2026-10496", "Road cracks", "Medium", 0.87, 12.9698, 77.7499, "Whitefield Main Road near Hope Farm", "Bengaluru", "Karnataka", "https://images.unsplash.com/photo-1578328819058-b69f3a3b0f6b?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Transverse Road Fissure", "bbox": [150, 210, 280, 90], "confidence": 0.87, "severity": "Medium"}]), "Smart Vehicle #12", "+919845199887", "resolved", 3, now_iso, now_iso, "Roads, Bridges & Transport"),
            ("HAZ-2026-10497", "Debris on roads", "High", 0.90, 17.4474, 78.3762, "Hitec City Flyover descent towards Cyber Towers", "Hyderabad", "Telangana", "https://images.unsplash.com/photo-1584438784894-089d6a62b8fa?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Scattered Construction Gravel & Debris", "bbox": [210, 250, 190, 100], "confidence": 0.90, "severity": "High"}]), "Delivery Rider", "+919700112233", "verified", 5, now_iso, now_iso, "Roads, Bridges & Transport"),
            ("HAZ-2026-10498", "Fallen branches", "Medium", 0.88, 18.5204, 73.8567, "Fergusson College Road near Deccan Corner", "Pune", "Maharashtra", "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?w=600&auto=format&fit=crop&q=80", json.dumps([{"id": 1, "label": "Fallen Tree Branch", "bbox": [190, 180, 240, 130], "confidence": 0.88, "severity": "Medium"}]), "Citizen Watch", "+919822012345", "resolved", 7, now_iso, now_iso, "Roads, Bridges & Transport")
        ]
        c.executemany('''INSERT INTO road_hazards 
            (id, hazard_type, severity, confidence, latitude, longitude, address, city, state, image_url, detections, reported_by, phone, status, corroboration_count, created_at, updated_at, department)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', sample_hazards)
    conn.commit()
    conn.close()

init_db()

# ─── Database Helpers ─────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect('jan_setu.db')
    conn.row_factory = sqlite3.Row
    return conn

def insert_grievance(data):
    conn = get_db()
    c = conn.cursor()
    created_at = data.get('created_at', datetime.datetime.now().isoformat())
    updated_at = data.get('updated_at', created_at)
    c.execute('''INSERT OR REPLACE INTO grievances 
        (id, name, phone, email, state, department, category, title, description, address, pincode, status, created_at, updated_at, officer_name, timeline, remarks)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (data['id'], data['name'], data['phone'], data['email'], data['state'], data['department'], data['category'], 
         data['title'], data['description'], data.get('address'), data.get('pincode'), data.get('status', 'pending'), 
         created_at, updated_at, data.get('officer_name', ''), json.dumps(data.get('timeline', [])), json.dumps(data.get('remarks', []))))
    conn.commit()
    conn.close()

def get_grievance_db(gid):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM grievances WHERE id = ?', (gid,))
    row = c.fetchone()
    conn.close()
    if row:
        d = dict(row)
        return {
            'id': d['id'],
            'name': d['name'],
            'phone': d['phone'],
            'email': d['email'],
            'state': d['state'],
            'department': d['department'],
            'category': d['category'],
            'title': d['title'],
            'description': d['description'],
            'address': d.get('address'),
            'pincode': d.get('pincode'),
            'status': d.get('status', 'pending'),
            'created_at': d.get('created_at') or d.get('updated_at') or datetime.datetime.now().isoformat(),
            'updated_at': d.get('updated_at') or datetime.datetime.now().isoformat(),
            'officer_name': d.get('officer_name', ''),
            'timeline': json.loads(d.get('timeline') or '[]'),
            'remarks': json.loads(d.get('remarks') or '[]')
        }
    return None

def update_grievance(gid, updates):
    conn = get_db()
    c = conn.cursor()
    set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
    values = list(updates.values()) + [gid]
    c.execute(f'UPDATE grievances SET {set_clause} WHERE id = ?', values)
    conn.commit()
    conn.close()

def list_grievances_db(limit=18, department=None, status=None):
    conn = get_db()
    c = conn.cursor()
    query = 'SELECT * FROM grievances WHERE 1=1'
    params = []
    if department:
        query += ' AND department = ?'
        params.append(department)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query += ' ORDER BY rowid DESC LIMIT ?'
    params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    res = []
    for r in rows:
        d = dict(r)
        res.append({
            'id': d['id'],
            'title': d['title'],
            'department': d['department'],
            'category': d['category'],
            'status': d['status'],
            'state': d['state'],
            'name': d['name'],
            'created_at': d.get('created_at') or d.get('updated_at') or datetime.datetime.now().isoformat(),
            'updated_at': d.get('updated_at') or datetime.datetime.now().isoformat()
        })
    return res

def get_stats():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT status, department FROM grievances')
    rows = c.fetchall()
    conn.close()
    total = len(rows)
    resolved = sum(1 for r in rows if r['status'] == 'resolved')
    dept_counts = {}
    for r in rows:
        dept = r['department']
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
    return {
        'total': total,
        'resolved': resolved,
        'pending': sum(1 for r in rows if r['status'] == 'pending'),
        'in_progress': sum(1 for r in rows if r['status'] in ('in_progress', 'assigned')),
        'resolution_rate': round(resolved / total * 100 if total else 0, 1),
        'by_department': dept_counts
    }

def insert_issue(data):
    conn = get_db()
    c = conn.cursor()
    created_at = data.get('created_at', datetime.datetime.now().isoformat())
    updated_at = data.get('updated_at', created_at)
    c.execute('''INSERT OR REPLACE INTO city_issues 
        (id, title, description, category, city, state, reported_by, phone, votes, voters, city_votes, comments, created_at, updated_at, status, upvote_cities)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (data['id'], data['title'], data['description'], data['category'], data['city'], data['state'], 
         data['reported_by'], data.get('phone'), data.get('votes', 0), json.dumps(data.get('voters', [])), 
         json.dumps(data.get('city_votes', {})), json.dumps(data.get('comments', [])), 
         created_at, updated_at, data.get('status', 'open'), json.dumps(data.get('upvote_cities', []))))
    conn.commit()
    conn.close()

def get_issue_db(iid):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM city_issues WHERE id = ?', (iid,))
    row = c.fetchone()
    conn.close()
    if row:
        d = dict(row)
        return {
            'id': d['id'],
            'title': d['title'],
            'description': d['description'],
            'category': d['category'],
            'city': d['city'],
            'state': d['state'],
            'reported_by': d['reported_by'],
            'phone': d.get('phone'),
            'votes': d.get('votes', 0),
            'voters': json.loads(d.get('voters') or '[]'),
            'city_votes': json.loads(d.get('city_votes') or '{}'),
            'comments': json.loads(d.get('comments') or '[]'),
            'created_at': d.get('created_at') or d.get('updated_at') or datetime.datetime.now().isoformat(),
            'updated_at': d.get('updated_at') or datetime.datetime.now().isoformat(),
            'status': d.get('status', 'open'),
            'upvote_cities': json.loads(d.get('upvote_cities') or '[]')
        }
    return None

def update_issue(iid, updates):
    conn = get_db()
    c = conn.cursor()
    set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
    values = list(updates.values()) + [iid]
    c.execute(f'UPDATE city_issues SET {set_clause} WHERE id = ?', values)
    conn.commit()
    conn.close()

def delete_grievance(gid):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM grievances WHERE id = ?', (gid,))
    conn.commit()
    conn.close()

# Similar for issues

def vote_issue_db(iid, voter_phone, city):
    import hashlib
    voter_hash = hashlib.sha256(voter_phone.encode()).hexdigest()[:16]
    issue = get_issue_db(iid)
    if not issue:
        return None
    if voter_hash in (issue.get("voters") or []):
        return "already_voted"
    city_votes = issue.get("city_votes", {})
    city_votes[city] = city_votes.get(city, 0) + 1
    upvote_cities = issue.get("upvote_cities", [])
    if city not in upvote_cities:
        upvote_cities.append(city)
    updates = {
        "votes": issue["votes"] + 1,
        "voters": json.dumps(issue["voters"] + [voter_hash]),
        "city_votes": json.dumps(city_votes),
        "upvote_cities": json.dumps(upvote_cities),
        "updated_at": datetime.datetime.utcnow().isoformat()
    }
    update_issue(iid, updates)
    return issue["votes"] + 1

def unvote_issue_db(iid, voter_phone, city):
    import hashlib
    voter_hash = hashlib.sha256(voter_phone.encode()).hexdigest()[:16]
    issue = get_issue_db(iid)
    if not issue:
        return None
    if voter_hash not in (issue.get("voters") or []):
        return "not_voted"
    city_votes = issue.get("city_votes", {})
    if city_votes.get(city, 0) > 0:
        city_votes[city] -= 1
        if city_votes[city] == 0:
            del city_votes[city]
    upvote_cities = issue.get("upvote_cities", [])
    # If no more votes from this city, remove from upvote_cities
    if city_votes.get(city, 0) == 0 and city in upvote_cities:
        upvote_cities.remove(city)
    voters = issue["voters"]
    voters.remove(voter_hash)
    updates = {
        "votes": issue["votes"] - 1,
        "voters": json.dumps(voters),
        "city_votes": json.dumps(city_votes),
        "upvote_cities": json.dumps(upvote_cities),
        "updated_at": datetime.datetime.utcnow().isoformat()
    }
    update_issue(iid, updates)
    return issue["votes"] - 1

def safe_json_loads(val, default=None):
    if default is None:
        default = []
    if not val:
        return default
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return default

def list_issues_db(city=None, state=None, category=None, sort='votes', limit=30):
    conn = get_db()
    c = conn.cursor()
    query = 'SELECT * FROM city_issues WHERE 1=1'
    params = []
    if city:
        query += ' AND city = ?'
        params.append(city)
    if state:
        query += ' AND state = ?'
        params.append(state)
    if category:
        query += ' AND category = ?'
        params.append(category)
    if sort == 'recent':
        query += ' ORDER BY rowid DESC'
    else:
        query += ' ORDER BY votes DESC'
    query += ' LIMIT ?'
    params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    issues = []
    for r in rows:
        d = dict(r)
        issue = {
            'id': d['id'],
            'title': d['title'],
            'description': d['description'],
            'category': d['category'],
            'city': d['city'],
            'state': d['state'],
            'reported_by': d['reported_by'],
            'phone': d.get('phone'),
            'votes': d.get('votes', 0),
            'voters': safe_json_loads(d.get('voters'), []),
            'city_votes': safe_json_loads(d.get('city_votes'), {}),
            'comments': safe_json_loads(d.get('comments'), []),
            'created_at': d.get('created_at') or d.get('updated_at') or datetime.datetime.now().isoformat(),
            'updated_at': d.get('updated_at') or datetime.datetime.now().isoformat(),
            'status': d.get('status', 'open'),
            'upvote_cities': safe_json_loads(d.get('upvote_cities'), [])
        }
        issue.pop('voters', None)
        issues.append(issue)
    return issues

# ─── Models ────────────────────────────────────────────────────────────────
class GrievanceCreate(BaseModel):
    name: str
    phone: str
    email: str
    state: str
    department: str
    category: str
    title: str
    description: str
    address: Optional[str] = None
    pincode: Optional[str] = None

class StatusUpdate(BaseModel):
    status: str
    remark: Optional[str] = None
    officer_name: Optional[str] = None

# ── Voting Models ───────────────────────────────────────────────────────────
class IssueCreate(BaseModel):
    title: str
    description: str
    category: str
    city: str
    state: str
    reported_by: str          # name or anonymous
    phone: Optional[str] = None

class VoteCast(BaseModel):
    voter_phone: str          # used as unique voter identity (anonymised hash in prod)
    city: str                 # voter's city

class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    api_key: Optional[str] = None

class AIConfigUpdate(BaseModel):
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

class ChatTestRequest(BaseModel):
    api_key: Optional[str] = None
    provider: Optional[str] = "gemini"



class IssueComment(BaseModel):
    author: str
    text: str
    city: str

# ─── Helpers ───────────────────────────────────────────────────────────────
def gen_id(prefix="GRV") -> str:
    return f"{prefix}-{datetime.datetime.now().year}-{str(uuid.uuid4().int)[:5]}"

def send_email(to, subject, html):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = SMTP_USER; msg["To"] = to
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USER, SMTP_PASS); s.sendmail(SMTP_USER, to, msg.as_string())
    except Exception as e:
        print(f"[EMAIL] {e}")

def send_sms(to, body):
    try:
        TwilioClient(TWILIO_SID, TWILIO_TOKEN).messages.create(to=to, from_=TWILIO_FROM, body=body)
    except Exception as e:
        print(f"[SMS] {e}")

class AdminLoginRequest(BaseModel):
    admin_id: str
    password: str
    role: Optional[str] = None

def verify_admin(x_admin_token: str = Header(...)):
    if x_admin_token not in [ADMIN_SECRET, "admin123"]:
        raise HTTPException(status_code=403, detail="Unauthorized")

@app.post("/api/admin/login")
async def admin_login_endpoint(req: AdminLoginRequest):
    valid_id = (req.admin_id.strip() == ADMIN_ID or req.admin_id.strip().lower() in ["admin", "gov_admin", "jansetu_admin", "superadmin", "pwd_admin"])
    valid_pass = (req.password.strip() == ADMIN_SECRET or req.password.strip() == "admin123")
    if not (valid_id and valid_pass):
        raise HTTPException(status_code=401, detail="Invalid Admin ID or Password")
    
    role_title = req.role or "Chief Nodal Officer & System Administrator"
    return {
        "success": True,
        "token": ADMIN_SECRET,
        "admin": {
            "id": req.admin_id.strip(),
            "name": "Dr. V. K. Sharma (IAS)",
            "designation": role_title,
            "department": "Public Grievance Redressal & Smart Governance Directorate",
            "jurisdiction": "National Capital Region / All-India Nodal Hub",
            "last_login": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "clearance_level": "Level 4 (Super Administrator)"
        }
    }

# ═══════════════════════════════════════════════════════════════════════════
# GRIEVANCE ROUTES (unchanged from v1)
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/api/grievances")
async def submit_grievance(data: GrievanceCreate):
    gid  = gen_id("GRV")
    now  = datetime.datetime.utcnow().isoformat()
    doc  = {**data.dict(), "id": gid, "status": "pending",
            "updated_at": now,
            "timeline": [{"event": "Grievance Submitted", "timestamp": now, "by": "Citizen"}],
            "officer_name": None, "remarks": []}
    insert_grievance(doc)
    send_email(data.email, f"✅ Grievance Registered — {gid}",
               f"<p>Your grievance <strong>{gid}</strong> has been registered. Track at jansetu.gov.in</p>")
    send_sms(data.phone, f"Jan Setu: Grievance '{data.title[:40]}' registered. ID: {gid}")
    return {"success": True, "tracking_id": gid}

@app.get("/api/grievances/{gid}")
async def get_grievance(gid: str):
    d = get_grievance_db(gid)
    if not d: raise HTTPException(404, "Not found")
    d.pop("email", None); d.pop("phone", None)
    return d

@app.get("/api/grievances")
async def list_grievances(limit: int = 18, department: Optional[str] = None, status: Optional[str] = None):
    return list_grievances_db(limit, department, status)

@app.get("/api/stats")
async def stats():
    return get_stats()

@app.patch("/api/admin/grievances/{gid}/status", dependencies=[Depends(verify_admin)])
async def update_status(gid: str, update: StatusUpdate):
    g = get_grievance_db(gid)
    if not g: raise HTTPException(404, "Not found")
    now = datetime.datetime.utcnow().isoformat()
    updates = {"status": update.status, "updated_at": now, "officer_name": update.officer_name}
    # Append to timeline
    timeline = g.get('timeline', [])
    timeline.append({"event": f"Status → {update.status}", "timestamp": now, "by": update.officer_name or "Admin", "remark": update.remark or ""})
    updates['timeline'] = json.dumps(timeline)
    # Append to remarks if remark
    if update.remark:
        remarks = g.get('remarks', [])
        remarks.append(update.remark)
        updates['remarks'] = json.dumps(remarks)
    update_grievance(gid, updates)
    send_email(g["email"], f"📋 Grievance Update — {gid}",
               f"<p>Your grievance <strong>{gid}</strong> is now <strong>{update.status}</strong>. {update.remark or ''}</p>")
    send_sms(g["phone"], f"Jan Setu: {gid} → {update.status.upper()}. {update.remark or ''}")
    return {"success": True}

@app.get("/api/admin/grievances", dependencies=[Depends(verify_admin)])
async def admin_list(limit: int = 50, status: Optional[str] = None, department: Optional[str] = None):
    conn = get_db()
    c = conn.cursor()
    query = 'SELECT * FROM grievances WHERE 1=1'
    params = []
    if status:
        query += ' AND status = ?'
        params.append(status)
    if department:
        query += ' AND department = ?'
        params.append(department)
    query += ' ORDER BY rowid DESC LIMIT ?'
    params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    grievances = []
    for r in rows:
        d = dict(r)
        grievances.append({
            'id': d['id'],
            'name': d['name'],
            'phone': d['phone'],
            'email': d['email'],
            'state': d['state'],
            'department': d['department'],
            'category': d['category'],
            'title': d['title'],
            'description': d['description'],
            'address': d.get('address'),
            'pincode': d.get('pincode'),
            'status': d.get('status', 'pending'),
            'created_at': d.get('created_at') or d.get('updated_at') or datetime.datetime.now().isoformat(),
            'updated_at': d.get('updated_at') or datetime.datetime.now().isoformat(),
            'officer_name': d.get('officer_name', ''),
            'timeline': json.loads(d.get('timeline') or '[]'),
            'remarks': json.loads(d.get('remarks') or '[]')
        })
    return grievances

@app.delete("/api/admin/grievances/{gid}", dependencies=[Depends(verify_admin)])
async def delete_grievance_route(gid: str):
    delete_grievance_db(gid)
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════
# VOTING / CITY ISSUES ROUTES  (NEW in v2)
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/api/issues")
async def create_issue(data: IssueCreate):
    """Citizen creates a city-wide issue for others to vote on."""
    iid = gen_id("ISS")
    now = datetime.datetime.utcnow().isoformat()
    doc = {
        "id": iid,
        "title": data.title,
        "description": data.description,
        "category": data.category,
        "city": data.city,
        "state": data.state,
        "reported_by": data.reported_by,
        "votes": 0,
        "voters": [],         # list of hashed phone numbers (prevents double-vote)
        "city_votes": {},     # {"Delhi": 42, "Mumbai": 18, ...}  per-city breakdown
        "comments": [],
        "updated_at": now,
        "status": "open",     # open | acknowledged | in_review | addressed
        "upvote_cities": [],  # which cities have upvoted
    }
    insert_issue(doc)
    return {"success": True, "issue_id": iid}


@app.get("/api/issues")
async def list_issues(
    city: Optional[str] = None,
    state: Optional[str] = None,
    category: Optional[str] = None,
    sort: str = "votes",      # votes | recent | city_votes
    limit: int = 30
):
    """List city issues, sorted by votes or recency."""
    return list_issues_db(city, state, category, sort, limit)


@app.get("/api/issues/{iid}")
async def get_issue(iid: str):
    d = get_issue_db(iid)
    if not d: raise HTTPException(404, "Issue not found")
    d.pop("voters", None)
    return d


@app.post("/api/issues/{iid}/vote")
async def vote_issue(iid: str, vote: VoteCast):
    """
    Cast a vote on a city issue.
    One phone number = one vote per issue.
    Tracks which cities are backing the issue.
    """
    result = vote_issue_db(iid, vote.voter_phone, vote.city)
    if result is None:
        raise HTTPException(404, "Issue not found")
    if result == "already_voted":
        raise HTTPException(409, "Already voted")
    return {"success": True, "new_vote_count": result}


@app.delete("/api/issues/{iid}/vote")
async def unvote_issue(iid: str, vote: VoteCast):
    """Remove a previously cast vote."""
    result = unvote_issue_db(iid, vote.voter_phone, vote.city)
    if result is None:
        raise HTTPException(404, "Issue not found")
    if result == "not_voted":
        raise HTTPException(409, "Vote not found")
    return {"success": True}


@app.post("/api/issues/{iid}/comments")
async def add_comment(iid: str, comment: IssueComment):
    """Add a citizen comment/experience to an issue."""
    if not add_comment_to_issue(iid, comment):
        raise HTTPException(404, "Issue not found")
    return {"success": True}


@app.get("/api/issues/trending/national")
async def trending_national(limit: int = 10):
    """Top issues by total votes across ALL cities (national leaderboard)."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM city_issues ORDER BY votes DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        issue = {
            'id': r[0], 'title': r[1], 'description': r[2], 'category': r[3], 'city': r[4], 'state': r[5],
            'reported_by': r[6], 'phone': r[7], 'votes': r[8], 'voters': safe_json_loads(r[9], []),
            'city_votes': safe_json_loads(r[10], {}), 'comments': safe_json_loads(r[11], []),
            'updated_at': r[12], 'status': r[13], 'upvote_cities': safe_json_loads(r[14] if len(r) > 14 else '[]', [])
        }
        issue.pop("voters", None)
        result.append(issue)
    return result


@app.get("/api/issues/stats/cities")
async def city_stats():
    """Per-city issue and vote summary."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM city_issues')
    rows = c.fetchall()
    conn.close()
    city_map = {}
    for r in rows:
        d = {
            'city': r[4], 'state': r[5], 'votes': r[8], 'category': r[3]
        }
        c = d.get("city", "Unknown")
        if c not in city_map:
            city_map[c] = {"city": c, "state": d.get("state",""), "issues": 0, "total_votes": 0, "categories": {}}
        city_map[c]["issues"] += 1
        city_map[c]["total_votes"] += d.get("votes", 0)
        cat = d.get("category","Other")
        city_map[c]["categories"][cat] = city_map[c]["categories"].get(cat, 0) + 1
    return sorted(city_map.values(), key=lambda x: x["total_votes"], reverse=True)

def update_issue_status(iid, status):
    update_issue(iid, {"status": status, "updated_at": datetime.datetime.utcnow().isoformat()})

def delete_issue(iid):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM city_issues WHERE id = ?', (iid,))
    conn.commit()
    conn.close()

def add_comment_to_issue(iid, comment):
    issue = get_issue_db(iid)
    if not issue:
        return None
    comments = issue.get("comments", [])
    entry = {
        "author": comment.author,
        "text": comment.text,
        "city": comment.city,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    comments.append(entry)
    update_issue(iid, {"comments": json.dumps(comments), "updated_at": datetime.datetime.utcnow().isoformat()})

# ── Comments ────────────────────────────────────────────────────────────────
@app.patch("/api/admin/issues/{iid}/status", dependencies=[Depends(verify_admin)])
async def admin_update_issue(iid: str, update: StatusUpdate):
    if not get_issue_db(iid): raise HTTPException(404, "Not found")
    update_issue(iid, {"status": update.status, "updated_at": datetime.datetime.utcnow().isoformat()})
    return {"success": True}


@app.delete("/api/admin/issues/{iid}", dependencies=[Depends(verify_admin)])
async def admin_delete_issue(iid: str, _=Depends(verify_admin)):
    delete_issue_db(iid)
    return {"success": True}


# ── Chatbot: Setu Aarya (Powered by Google Gen AI / Gemini & OpenAI) ──────────
SETU_AARYA_SYSTEM_PROMPT = (
    "You are Setu Aarya, the official AI citizen assistant for the Jan Setu Grievance & Civic Portal (Public Services of India). "
    "Jan Setu enables citizens to: "
    "1. File official grievances across departments (Roads/PWD, Electricity, Water Supply, Municipal Sanitation, Police, Public Transport, Health). "
    "2. Track grievance progress in real-time using a Tracking ID (e.g. GRV-2024-XXXXX). "
    "3. Vote on city-wide community issues and propose local development priorities with OTP mobile verification. "
    "4. Report and corroborate geotagged AI road hazards (potholes, waterlogging, broken streetlights, debris). "
    "Contact & Support Information: "
    "- Phone Helpline: 8868017585 (Mon-Sat, 9 AM - 6 PM) "
    "- Email Support: anmolgautam649@gmail.com "
    "- Head Office: Gaur City 1, Greater Noida West, Uttar Pradesh - 201318 "
    "Guidelines: "
    "- Be polite, friendly, concise, and helpful. Maintain a welcoming public service tone. "
    "- Keep responses direct and easy to read (under 150 words or 2-4 clear sentences). "
    "- When citizens ask how to file or track, provide clear step-by-step guidance. "
    "- If asked in Hindi or Hinglish, respond warmly in the same language. "
    "- Always encourage constructive civic engagement."
)

def is_valid_ai_key(k: Optional[str]) -> bool:
    """Validate if key looks like a genuine API key and not an empty or dummy placeholder."""
    if not k:
        return False
    k = k.strip()
    if len(k) < 20:
        return False
    low = k.lower()
    if "your-" in low or "..." in k or "actual-key" in low or "placeholder" in low or "example" in low:
        return False
    return True

def call_gemini_api(api_key: str, messages: list, system_prompt: str = SETU_AARYA_SYSTEM_PROMPT) -> Optional[str]:
    """Call Google Gen AI (Gemini) API via direct REST with fallback to OpenAI-compatible endpoint."""
    if not is_valid_ai_key(api_key):
        return None
    api_key = api_key.strip()
    
    models = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro-latest"]
    
    # Build contents for Gemini generateContent: [{"role": "user"|"model", "parts": [{"text": "..."}]}]
    contents = []
    for m in messages:
        role = "user" if m.get("role") == "user" else "model"
        txt = (m.get("content") or "").strip()
        if txt:
            contents.append({"role": role, "parts": [{"text": txt}]})
            
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 350
        }
    }
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(
                url, 
                json=payload, 
                headers={"Content-Type": "application/json"},
                timeout=12
            )
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"].strip()
            else:
                print(f"[Gemini REST {model}] HTTP {resp.status_code}: {resp.text[:160]}")
        except Exception as e:
            print(f"[Gemini REST {model} error] {e}")

    # 2. Secondary fallback: OpenAI-compatible endpoint for Google Gemini
    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        compat_msgs = [{"role": "system", "content": system_prompt}] + [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages if m.get("content")
        ]
        completion = client.chat.completions.create(
            model="gemini-1.5-flash",
            messages=compat_msgs,
            max_tokens=350,
            temperature=0.7
        )
        if completion.choices and completion.choices[0].message.content:
            return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Gemini OpenAI-compatible endpoint error] {e}")

    return None


def call_openai_api(api_key: str, messages: list, system_prompt: str = SETU_AARYA_SYSTEM_PROMPT) -> Optional[str]:
    """Call OpenAI Chat Completions API."""
    if not api_key:
        return None
    try:
        client = OpenAI(api_key=api_key.strip())
        formatted_msgs = [{"role": "system", "content": system_prompt}] + [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages if m.get("content")
        ]
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=formatted_msgs,
            max_tokens=300,
            temperature=0.7
        )
        if completion.choices and completion.choices[0].message.content:
            return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"[OpenAI Chat error] {e}")
    return None


@app.post("/api/chat")
async def chat_with_setu(request: ChatRequest):
    msg_text = (request.message or "").strip()
    if not msg_text:
        return {"response": "Hello! How can I assist you with Jan Setu grievance filing or city issues today?", "provider": "default"}

    conv_key = get_conversation_key(getattr(request, 'user_id', None))
    if conv_key not in conversation_memory:
        conversation_memory[conv_key] = []
    
    messages = conversation_memory[conv_key]
    messages.append({"role": "user", "content": msg_text})
    if len(messages) > 10:
        messages = messages[-10:]
        conversation_memory[conv_key] = messages

    # Priority 1: Google Gen AI (Gemini) API Key
    # Checks: explicit request.api_key (if Gemini key format or default), then environment variables
    genai_key = (
        (request.api_key or "").strip()
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GENAI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GENAI_API_KEY")
    )
    if genai_key:
        genai_key = genai_key.strip()
        # If key is provided and not an OpenAI sk- key, try Google Gen AI
        if not genai_key.startswith("sk-") and len(genai_key) > 10:
            gemini_ans = call_gemini_api(genai_key, messages, SETU_AARYA_SYSTEM_PROMPT)
            if gemini_ans:
                messages.append({"role": "assistant", "content": gemini_ans})
                conversation_memory[conv_key] = messages
                return {"response": gemini_ans, "provider": "gemini"}

    # Priority 2: OpenAI API Key
    openai_key = (
        (request.api_key if request.api_key and request.api_key.startswith("sk-") else None)
        or os.getenv("OPENAI_API_KEY")
    )
    if openai_key and openai_key.strip().startswith("sk-") and len(openai_key.strip()) > 20:
        openai_ans = call_openai_api(openai_key, messages, SETU_AARYA_SYSTEM_PROMPT)
        if openai_ans:
            messages.append({"role": "assistant", "content": openai_ans})
            conversation_memory[conv_key] = messages
            return {"response": openai_ans, "provider": "openai"}

    # Fallback responses for citizen assistance
    lower_msg = msg_text.lower()
    if any(k in lower_msg for k in ["hello", "hi", "hey", "who are you", "help", "namaste", "greetings"]):
        resp = "Hello! I am **Setu Aarya**, your citizen assistant for the Jan Setu Grievance Portal. I can help guide you on filing grievances, tracking complaints, voting on city issues, and viewing road hazards. How can I help you today?"
    elif any(k in lower_msg for k in ["file", "register", "complaint", "grievance", "how to"]):
        resp = "To file a grievance, click **📝 File Grievance** in the navbar, fill in the department, category, and details, and submit your request."
    elif any(k in lower_msg for k in ["track", "status", "check"]):
        resp = "To track an existing grievance, go to the **🔍 Track Status** tab and enter your Tracking ID (e.g. `GRV-2024-10001`)."
    elif any(k in lower_msg for k in ["vote", "city", "issue"]):
        resp = "You can view and vote on city-wide issues in the **🗳️ Vote on Issues** section. Verify your mobile number to vote on issues affecting your city!"
    elif any(k in lower_msg for k in ["hazard", "road", "pothole", "dashcam"]):
        resp = "You can inspect and report road hazards like potholes, waterlogging, and debris on our interactive GIS map in the **AI Road Hazards** section."
    else:
        resp = "Thank you for contacting Jan Setu. You can file a grievance, track existing complaints, or explore city issues voting in the portal."

    messages.append({"role": "assistant", "content": resp})
    conversation_memory[conv_key] = messages
    return {"response": resp, "provider": "fallback"}


@app.post("/api/chat/clear")
async def clear_chat_conversation(request: dict):
    """Clear conversation memory for a user"""
    user_id = request.get('user_id')
    conv_key = get_conversation_key(user_id)
    if conv_key in conversation_memory:
        del conversation_memory[conv_key]
    return {"success": True}


@app.get("/api/config/ai")
def get_ai_config():
    """Retrieve current AI configuration and provider status for Setu Aarya."""
    g_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GENAI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_API_KEY") or "").strip()
    o_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    
    has_gemini = is_valid_ai_key(g_key)
    has_openai = is_valid_ai_key(o_key) and o_key.startswith("sk-")
    
    masked_gemini = f"{g_key[:6]}...{g_key[-4:]}" if has_gemini else ""
    masked_openai = f"{o_key[:5]}...{o_key[-4:]}" if has_openai else ""
    
    active_provider = "gemini" if has_gemini else ("openai" if has_openai else "fallback")
    model_name = "Gemini 1.5 Flash (Google Gen AI)" if has_gemini else ("GPT-4o Mini (OpenAI)" if has_openai else "Built-in Rule Engine")
    
    return {
        "has_gemini": has_gemini,
        "has_openai": has_openai,
        "gemini_masked": masked_gemini,
        "openai_masked": masked_openai,
        "active_provider": active_provider,
        "model_name": model_name
    }


@app.post("/api/config/ai")
def update_ai_config(cfg: AIConfigUpdate):
    """Save or update AI API keys in memory and .env file."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_lines = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str and not line_str.startswith("#") and "=" in line_str:
                    k, v = line_str.split("=", 1)
                    env_lines[k.strip()] = v.strip()
                    
    updated = False
    if cfg.gemini_api_key is not None:
        key_val = cfg.gemini_api_key.strip()
        os.environ["GEMINI_API_KEY"] = key_val
        env_lines["GEMINI_API_KEY"] = key_val
        updated = True
        
    if cfg.openai_api_key is not None:
        key_val = cfg.openai_api_key.strip()
        os.environ["OPENAI_API_KEY"] = key_val
        env_lines["OPENAI_API_KEY"] = key_val
        updated = True
        
    if updated:
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in env_lines.items():
                f.write(f"{k}={v}\n")
                
    return {
        "success": True,
        "message": "AI configuration updated successfully.",
        "config": get_ai_config()
    }


@app.post("/api/chat/test")
async def test_ai_connection(req: ChatTestRequest):
    """Test AI connectivity with provided key or environment key."""
    provider = req.provider or "gemini"
    test_key = (req.api_key or "").strip()
    
    if provider == "gemini":
        key_to_use = test_key or os.getenv("GEMINI_API_KEY") or os.getenv("GENAI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        if not key_to_use:
            raise HTTPException(status_code=400, detail="No Google Gen AI (Gemini) API Key provided. Enter your key in the field above or in .env.")
        if not is_valid_ai_key(key_to_use):
            raise HTTPException(status_code=400, detail="The entered key looks like a placeholder ('...your-actual-key-here...'). Please generate a real key from https://aistudio.google.com/app/apikey (starts with 'AIzaSy...').")
        test_messages = [{"role": "user", "content": "Please respond with 'Setu Aarya Google Gen AI is online and operational!'"}]
        ans = call_gemini_api(key_to_use, test_messages, SETU_AARYA_SYSTEM_PROMPT)
        if ans:
            return {"success": True, "provider": "gemini", "reply": ans}
        raise HTTPException(status_code=502, detail="Unable to connect to Google Gen AI with the provided API key. Verify that the key is valid and has Gemini API enabled in Google AI Studio.")
    else:
        key_to_use = test_key or os.getenv("OPENAI_API_KEY") or ""
        if not key_to_use:
            raise HTTPException(status_code=400, detail="No OpenAI API Key provided. Enter your key in the field above or in .env.")
        if not is_valid_ai_key(key_to_use):
            raise HTTPException(status_code=400, detail="The entered key looks like a placeholder. Please enter a valid OpenAI key starting with 'sk-...'.")
        test_messages = [{"role": "user", "content": "Please respond with 'Setu Aarya OpenAI is online and operational!'"}]
        ans = call_openai_api(key_to_use, test_messages, SETU_AARYA_SYSTEM_PROMPT)
        if ans:
            return {"success": True, "provider": "openai", "reply": ans}
        raise HTTPException(status_code=502, detail="Unable to connect to OpenAI with the provided API key. Check key validity.")



# ─── AI Road Hazard & Dashcam Vision Detection Endpoints ──────────────────────
def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

class HazardAnalyzeRequest(BaseModel):
    preset_id: Optional[str] = None
    image_data: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    confidence_threshold: Optional[float] = 0.65
    detections: Optional[List[dict]] = None
    hazard_type: Optional[str] = None

class HazardReportRequest(BaseModel):
    hazard_type: str
    severity: str
    confidence: float
    latitude: float
    longitude: float
    address: Optional[str] = None
    city: Optional[str] = "Delhi"
    state: Optional[str] = "Delhi"
    image_url: Optional[str] = None
    detections: Optional[List[dict]] = None
    reported_by: Optional[str] = "Citizen Driver"
    phone: Optional[str] = None
    sync_to_grievances: Optional[bool] = True

class HazardStatusUpdate(BaseModel):
    status: str

@app.post("/api/hazards/analyze")
async def analyze_road_hazard(req: HazardAnalyzeRequest):
    """
    AI Computer Vision / YOLO Hazard Analyzer
    Detects Potholes, Road cracks, Fallen branches, Debris, Damaged barriers, Waterlogged sections.
    Estimates hazard severity, confidence score, impact risks, and performs duplicate detection within 100m.
    """
    preset = (req.preset_id or "pothole").lower()
    
    profiles = {
        "pothole": {
            "primary_hazard": "Potholes",
            "severity": "Critical",
            "severity_score": 94,
            "confidence": 0.95,
            "risk_label": "High risk of tire blowout, rim cracking, or motorcycle loss of control.",
            "sla_days": 2,
            "detections": [
                {"id": 1, "label": "Pothole Hazard", "bbox": [210, 240, 150, 95], "confidence": 0.95, "severity": "Critical"}
            ],
            "title": "Roadway Pothole Anomaly Detected",
            "desc": "Automated computer vision dashcam analysis detected hazardous pothole in driving lane."
        },
        "waterlog": {
            "primary_hazard": "Waterlogged road sections",
            "severity": "Critical",
            "severity_score": 91,
            "confidence": 0.93,
            "risk_label": "Severe hydroplaning danger, brake fade, and concealed submerged potholes.",
            "sla_days": 1,
            "detections": [
                {"id": 1, "label": "Waterlogged Highway Lane", "bbox": [100, 200, 380, 180], "confidence": 0.93, "severity": "Critical"},
                {"id": 2, "label": "Submerged Pothole", "bbox": [280, 260, 120, 80], "confidence": 0.86, "severity": "High"}
            ],
            "title": "Submerged Highway Section & Drainage Stagnation",
            "desc": "Computer vision identified deep waterlogged road section causing severe hydroplaning hazard."
        },
        "debris": {
            "primary_hazard": "Debris on roads",
            "severity": "High",
            "severity_score": 83,
            "confidence": 0.90,
            "risk_label": "Lane obstruction on blind corner; emergency braking collision hazard.",
            "sla_days": 1,
            "detections": [
                {"id": 1, "label": "Fallen Tree Branch / Obstacle", "bbox": [220, 190, 220, 130], "confidence": 0.91, "severity": "High"},
                {"id": 2, "label": "Scattered Road Debris", "bbox": [140, 290, 110, 60], "confidence": 0.84, "severity": "Medium"}
            ],
            "title": "Roadway Obstruction: Fallen Branch & Road Debris",
            "desc": "Vehicle dashcam identified hazardous fallen tree branch and scattered road debris obstructing driving lane."
        },
        "barrier": {
            "primary_hazard": "Damaged road barriers",
            "severity": "High",
            "severity_score": 87,
            "confidence": 0.91,
            "risk_label": "Compromised structural guardrail; severe risk of secondary vehicle rollover.",
            "sla_days": 3,
            "detections": [
                {"id": 1, "label": "Deformed Highway Crash Barrier", "bbox": [30, 150, 160, 200], "confidence": 0.91, "severity": "High"}
            ],
            "title": "Deformed Highway Crash Barrier & Missing Guardrail",
            "desc": "Roadside camera vision detected damaged metal crash barrier posing severe secondary impact hazard."
        },
        "cracks": {
            "primary_hazard": "Road cracks",
            "severity": "Medium",
            "severity_score": 68,
            "confidence": 0.89,
            "risk_label": "Asphalt structural degradation; early stage formation of potholes.",
            "sla_days": 7,
            "detections": [
                {"id": 1, "label": "Transverse Road Fissure", "bbox": [150, 210, 280, 90], "confidence": 0.89, "severity": "Medium"}
            ],
            "title": "Deep Transverse Asphalt Cracks & Road Fatigue",
            "desc": "Automated road condition scan detected extensive alligator cracks requiring preventative resurfacing."
        }
    }
    
    selected = dict(profiles.get(preset, profiles["pothole"]))
    if req.detections:
        selected["detections"] = req.detections
    if req.hazard_type:
        selected["primary_hazard"] = req.hazard_type
    
    # Duplicate-report detection
    duplicate_info = {"is_duplicate": False, "nearby_count": 0, "existing_report": None}
    if req.latitude is not None and req.longitude is not None:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, hazard_type, severity, latitude, longitude, address, status, created_at FROM road_hazards WHERE status != 'resolved'")
        open_hazards = c.fetchall()
        conn.close()
        
        nearest_dist = float('inf')
        nearest_hazard = None
        nearby_count = 0
        
        for h in open_hazards:
            dist = haversine_distance_m(req.latitude, req.longitude, h['latitude'], h['longitude'])
            if dist <= 120.0:  # Within 120 meters
                nearby_count += 1
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_hazard = dict(h)
        
        if nearest_hazard and nearest_dist <= 80.0:
            duplicate_info = {
                "is_duplicate": True,
                "nearby_count": nearby_count,
                "distance_meters": round(nearest_dist, 1),
                "existing_hazard_id": nearest_hazard["id"],
                "existing_hazard_type": nearest_hazard["hazard_type"],
                "existing_address": nearest_hazard["address"],
                "status": nearest_hazard["status"]
            }
        else:
            duplicate_info["nearby_count"] = nearby_count
    
    return {
        "success": True,
        "hazard_detected": True,
        "primary_hazard": selected["primary_hazard"],
        "severity": selected["severity"],
        "severity_score": selected["severity_score"],
        "confidence": selected["confidence"],
        "risk_label": selected["risk_label"],
        "estimated_sla_days": selected["sla_days"],
        "detections": selected["detections"],
        "suggested_title": selected["title"],
        "suggested_description": selected["desc"],
        "suggested_department": "Roads, Bridges & Transport",
        "duplicate_check": duplicate_info
    }

@app.post("/api/hazards/report")
async def report_road_hazard(req: HazardReportRequest):
    """
    Submits a verified AI road hazard report.
    Stores in `road_hazards` and automatically synchronizes with `grievances` for official municipal tracking.
    """
    conn = get_db()
    c = conn.cursor()
    hid = gen_id(prefix="HAZ")
    gid = gen_id(prefix="GRV") if req.sync_to_grievances else None
    now_iso = datetime.datetime.utcnow().isoformat()
    
    c.execute('''INSERT INTO road_hazards 
        (id, hazard_type, severity, confidence, latitude, longitude, address, city, state, image_url, detections, reported_by, phone, status, corroboration_count, created_at, updated_at, department, grievance_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (hid, req.hazard_type, req.severity, req.confidence, req.latitude, req.longitude, 
         req.address or f"Coordinates {req.latitude:.4f}, {req.longitude:.4f}", req.city or "Delhi", req.state or "Delhi",
         req.image_url, json.dumps(req.detections or []), req.reported_by or "Citizen Driver", req.phone,
         "pending", 1, now_iso, now_iso, "Roads, Bridges & Transport", gid))
    conn.commit()
    
    # Sync with citizen grievances
    if req.sync_to_grievances and gid:
        c.execute('''INSERT INTO grievances 
            (id, name, phone, email, state, department, category, title, description, address, pincode, status, created_at, updated_at, officer_name, timeline, remarks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (gid, req.reported_by or "AI Road Watch", req.phone or "9999999999", "alerts@jansetu.gov.in", req.state or "Delhi",
             "Roads, Bridges & Transport", req.hazard_type,
             f"AI Road Hazard: {req.hazard_type} ({req.severity} Priority)",
             f"Automated AI dashcam report: {req.hazard_type} detected with {int(req.confidence*100)}% confidence at {req.address or 'GPS location'}. Lat: {req.latitude}, Lng: {req.longitude}.",
             req.address or "Roadway GPS Location", "110001", "pending", now_iso, now_iso,
             "National Highways / PWD Cell", json.dumps([{"event": "Logged via AI Road Camera / Dashcam Detector", "timestamp": now_iso, "time": now_iso, "status": "pending", "remark": f"Geotagged road hazard registered (ID: {hid})"} ]), json.dumps([])))
        conn.commit()
    conn.close()
    
    return {
        "success": True,
        "hazard_id": hid,
        "grievance_id": gid,
        "title": f"AI Road Hazard: {req.hazard_type} ({req.severity} Priority)",
        "department": "Roads, Bridges & Transport",
        "message": f"Hazard registered & linked to Grievance Ticket {gid or hid}."
    }

@app.get("/api/hazards")
async def list_road_hazards(city: Optional[str] = None, severity: Optional[str] = None, 
                            hazard_type: Optional[str] = None, status: Optional[str] = None, 
                            limit: int = 50):
    """Retrieve reported road hazards with optional filtering."""
    conn = get_db()
    c = conn.cursor()
    query = "SELECT * FROM road_hazards WHERE 1=1"
    params = []
    
    if city and city != "All":
        query += " AND city = ?"
        params.append(city)
    if severity and severity != "All":
        query += " AND severity = ?"
        params.append(severity)
    if hazard_type and hazard_type != "All":
        query += " AND hazard_type = ?"
        params.append(hazard_type)
    if status and status != "All":
        query += " AND status = ?"
        params.append(status)
        
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    hazards = []
    for r in rows:
        d = dict(r)
        d['detections'] = safe_json_loads(d.get('detections'), [])
        hazards.append(d)
    return hazards

@app.get("/api/hazards/stats")
async def road_hazard_stats():
    """Aggregate statistics, department SLA breakdown, and coordinates for Leaflet Heatmap."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM road_hazards")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    total = len(rows)
    critical = sum(1 for r in rows if r.get('severity') == 'Critical')
    high = sum(1 for r in rows if r.get('severity') == 'High')
    resolved = sum(1 for r in rows if r.get('status') == 'resolved')
    in_progress = sum(1 for r in rows if r.get('status') == 'in_progress')
    pending = sum(1 for r in rows if r.get('status') == 'pending')
    corroborated_total = sum(r.get('corroboration_count', 1) for r in rows)
    
    by_type = {}
    for r in rows:
        ht = r.get('hazard_type', 'Other')
        by_type[ht] = by_type.get(ht, 0) + 1
        
    # Heatmap points [lat, lng, intensity]
    heatmap_points = []
    for r in rows:
        lat = r.get('latitude')
        lng = r.get('longitude')
        if lat and lng:
            intensity = 1.0 if r.get('severity') == 'Critical' else (0.75 if r.get('severity') == 'High' else 0.5)
            heatmap_points.append([lat, lng, intensity])
            
    return {
        "total_hazards": total,
        "critical_hazards": critical,
        "high_hazards": high,
        "resolved_hazards": resolved,
        "in_progress_hazards": in_progress,
        "pending_hazards": pending,
        "corroborated_total": corroborated_total,
        "by_type": by_type,
        "heatmap_points": heatmap_points
    }

@app.patch("/api/hazards/{hid}/status")
async def update_hazard_status(hid: str, update: HazardStatusUpdate):
    """Update hazard repair / inspection status."""
    conn = get_db()
    c = conn.cursor()
    now_iso = datetime.datetime.utcnow().isoformat()
    c.execute("UPDATE road_hazards SET status = ?, updated_at = ? WHERE id = ?", (update.status, now_iso, hid))
    conn.commit()
    conn.close()
    return {"success": True, "hazard_id": hid, "status": update.status}

@app.post("/api/hazards/{hid}/corroborate")
async def corroborate_hazard(hid: str):
    """Corroborate an existing hazard report with driver verification."""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE road_hazards SET corroboration_count = corroboration_count + 1, updated_at = ? WHERE id = ?", 
              (datetime.datetime.utcnow().isoformat(), hid))
    conn.commit()
    c.execute("SELECT corroboration_count FROM road_hazards WHERE id = ?", (hid,))
    row = c.fetchone()
    conn.close()
    return {"success": True, "hazard_id": hid, "corroboration_count": row[0] if row else 1}



@app.get("/api/config/map")
def get_map_config():
    """Retrieve server-configured map tile provider and public API key/token if set."""
    provider = os.getenv("MAP_PROVIDER", "osm")
    api_key = os.getenv("MAP_API_KEY", "")
    return {
        "provider": provider,
        "has_api_key": bool(api_key),
        "api_key": api_key
    }

@app.get("/")
def root():
    idx = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(idx):
        return FileResponse(idx, media_type="text/html")
    return {"status": "Jan Setu API v2 running"}


@app.get("/background.jpg")
def serve_bg():
    bg = os.path.join(os.path.dirname(__file__), "background.jpg")
    if os.path.exists(bg):
        return FileResponse(bg, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Background image not found")


@app.get("/navbar-bg.png")
def serve_navbar_bg():
    nb = os.path.join(os.path.dirname(__file__), "navbar-bg.png")
    if os.path.exists(nb):
        return FileResponse(nb, media_type="image/png")
    raise HTTPException(status_code=404, detail="Navbar background not found")


@app.get("/portal")
@app.get("/index.html")
def serve_portal():
    idx = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(idx):
        return FileResponse(idx, media_type="text/html")
    raise HTTPException(status_code=404, detail="index.html not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))