"""
Jan Setu Grievance Portal — FastAPI Backend v2
---------------------------------------------
New in v2: City Issues Voting System
Run:  uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Depends, Header
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
import hashlib

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
        voters TEXT,  -- JSON list of hashed phones
        city_votes TEXT,  -- JSON dict
        comments TEXT,  -- JSON list
        updated_at TEXT,
        status TEXT DEFAULT 'open',
        upvote_cities TEXT  -- JSON list
    )''')
    conn.commit()
    conn.close()

init_db()

# ─── Database Helpers ─────────────────────────────────────────────────────────
def get_db():
    return sqlite3.connect('jan_setu.db')

def insert_grievance(data):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO grievances 
        (id, name, phone, email, state, department, category, title, description, address, pincode, status, updated_at, timeline, remarks)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (data['id'], data['name'], data['phone'], data['email'], data['state'], data['department'], data['category'], 
         data['title'], data['description'], data.get('address'), data.get('pincode'), data['status'], 
         data['updated_at'], json.dumps(data['timeline']), json.dumps(data['remarks'])))
    conn.commit()
    conn.close()

def get_grievance_db(gid):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM grievances WHERE id = ?', (gid,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'id': row[0], 'name': row[1], 'phone': row[2], 'email': row[3], 'state': row[4], 'department': row[5],
            'category': row[6], 'title': row[7], 'description': row[8], 'address': row[9], 'pincode': row[10],
            'status': row[11], 'updated_at': row[12], 'officer_name': row[13],
            'timeline': json.loads(row[14] or '[]'), 'remarks': json.loads(row[15] or '[]')
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
    query = 'SELECT id, title, department, category, status, state, updated_at FROM grievances WHERE 1=1'
    params = []
    if department:
        query += ' AND department = ?'
        params.append(department)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query += ' ORDER BY updated_at DESC LIMIT ?'
    params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'title': r[1], 'department': r[2], 'category': r[3], 'status': r[4], 'state': r[5], 'updated_at': r[6]} for r in rows]

def get_stats():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT status, department FROM grievances')
    rows = c.fetchall()
    conn.close()
    total = len(rows)
    resolved = sum(1 for r in rows if r[0] == 'resolved')
    dept_counts = {}
    for r in rows:
        dept_counts[r[1]] = dept_counts.get(r[1], 0) + 1
    return {
        'total': total,
        'resolved': resolved,
        'pending': sum(1 for r in rows if r[0] == 'pending'),
        'in_progress': sum(1 for r in rows if r[0] == 'in_progress'),
        'resolution_rate': round(resolved / total * 100 if total else 0, 1),
        'by_department': dept_counts
    }

# Similar for issues
def insert_issue(data):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO city_issues 
        (id, title, description, category, city, state, reported_by, phone, votes, voters, city_votes, comments, updated_at, status, upvote_cities)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (data['id'], data['title'], data['description'], data['category'], data['city'], data['state'], 
         data['reported_by'], data.get('phone'), data['votes'], json.dumps(data['voters']), 
         json.dumps(data['city_votes']), json.dumps(data['comments']), 
         data['updated_at'], data['status'], json.dumps(data['upvote_cities'])))
    conn.commit()
    conn.close()

def get_issue_db(iid):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM city_issues WHERE id = ?', (iid,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'id': row[0], 'title': row[1], 'description': row[2], 'category': row[3], 'city': row[4], 'state': row[5],
            'reported_by': row[6], 'phone': row[7], 'votes': row[8], 'voters': json.loads(row[9] or '[]'),
            'city_votes': json.loads(row[10] or '{}'), 'comments': json.loads(row[11] or '[]'),
            'updated_at': row[12], 'status': row[13], 'upvote_cities': json.loads(row[14] or '[]')
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
        query += ' ORDER BY updated_at DESC'
    else:
        query += ' ORDER BY votes DESC'
    query += ' LIMIT ?'
    params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    issues = []
    for r in rows:
        issue = {
            'id': r[0], 'title': r[1], 'description': r[2], 'category': r[3], 'city': r[4], 'state': r[5],
            'reported_by': r[6], 'phone': r[7], 'votes': r[8], 'voters': json.loads(r[9] or '[]'),
            'city_votes': json.loads(r[10] or '{}'), 'comments': json.loads(r[11] or '[]'),
            'updated_at': r[12], 'status': r[13], 'upvote_cities': json.loads(r[14] or '[]')
        }
        # Remove voters for public
        issue.pop('voters', None)
        issues.append(issue)
    return issues

# ─── Models ────────────────────────────────────────────────────────────────
class GrievanceCreate(BaseModel):
    name: str
    phone: str
    email: EmailStr
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

def verify_admin(x_admin_token: str = Header(...)):
    if x_admin_token != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")

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
    # For admin, return full list without limit on fields
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
    query += ' ORDER BY updated_at DESC LIMIT ?'
    params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    grievances = []
    for r in rows:
        g = {
            'id': r[0], 'name': r[1], 'phone': r[2], 'email': r[3], 'state': r[4], 'department': r[5],
            'category': r[6], 'title': r[7], 'description': r[8], 'address': r[9], 'pincode': r[10],
            'status': r[11], 'updated_at': r[12], 'officer_name': r[13],
            'timeline': json.loads(r[14] or '[]'), 'remarks': json.loads(r[15] or '[]')
        }
        grievances.append(g)
    return grievances

@app.delete("/api/admin/grievances/{gid}", dependencies=[Depends(verify_admin)])
async def delete_grievance_route(gid: str):
    delete_grievance(gid)
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
            'reported_by': r[6], 'phone': r[7], 'votes': r[8], 'voters': json.loads(r[9] or '[]'),
            'city_votes': json.loads(r[10] or '{}'), 'comments': json.loads(r[11] or '[]'),
            'updated_at': r[12], 'status': r[13], 'upvote_cities': json.loads(r[14] or '[]')
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
    if not get_issue(iid): raise HTTPException(404, "Not found")
    update_issue_status(iid, update.status)
    return {"success": True}


@app.delete("/api/admin/issues/{iid}", dependencies=[Depends(verify_admin)])
async def admin_delete_issue(iid: str, _=Depends(verify_admin)):
    delete_issue(iid)
    return {"success": True}


# ── Chatbot: Setu Aarya ─────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat_with_setu(request: ChatRequest):
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
        
        # Get or create conversation history
        conv_key = get_conversation_key(getattr(request, 'user_id', None))
        if conv_key not in conversation_memory:
            conversation_memory[conv_key] = [
                {"role": "system", "content": "You are Setu Aarya, a helpful AI assistant for the Jan Setu citizen grievance portal. Help users with filing grievances, understanding voting on city issues, tracking complaints, and general citizen services. Be friendly, concise, and accurate. Always encourage positive civic engagement. Keep responses under 200 words."}
            ]
        
        messages = conversation_memory[conv_key]
        
        # Add user message
        messages.append({"role": "user", "content": request.message})
        
        # Keep only last 10 messages to avoid token limits
        if len(messages) > 11:  # system + 10 exchanges
            messages = messages[:1] + messages[-10:]
            conversation_memory[conv_key] = messages
        
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=300,
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content.strip()
        
        # Add AI response to memory
        messages.append({"role": "assistant", "content": ai_response})
        conversation_memory[conv_key] = messages
        
        return {"response": ai_response}
    except Exception as e:
        print(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat service unavailable: {str(e)}")

@app.post("/api/chat/clear")
async def clear_chat_conversation(request: dict):
    """Clear conversation memory for a user"""
    user_id = request.get('user_id')
    conv_key = get_conversation_key(user_id)
    if conv_key in conversation_memory:
        del conversation_memory[conv_key]
    return {"success": True}


@app.get("/")
def root():
    return {"status": "Jan Setu API v2 running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))