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
            "created_at": now, "updated_at": now,
            "timeline": [{"event": "Grievance Submitted", "timestamp": now, "by": "Citizen"}],
            "officer_name": None, "remarks": []}
    db.collection("grievances").document(gid).set(doc)
    send_email(data.email, f"✅ Grievance Registered — {gid}",
               f"<p>Your grievance <strong>{gid}</strong> has been registered. Track at jansetu.gov.in</p>")
    send_sms(data.phone, f"Jan Setu: Grievance '{data.title[:40]}' registered. ID: {gid}")
    return {"success": True, "tracking_id": gid}

@app.get("/api/grievances/{gid}")
async def get_grievance(gid: str):
    doc = db.collection("grievances").document(gid).get()
    if not doc.exists: raise HTTPException(404, "Not found")
    d = doc.to_dict(); d.pop("email", None); d.pop("phone", None)
    return d

@app.get("/api/grievances")
async def list_grievances(limit: int = 18, department: Optional[str] = None, status: Optional[str] = None):
    q = db.collection("grievances").order_by("created_at", direction=firestore.Query.DESCENDING)
    if department: q = q.where("department", "==", department)
    if status: q = q.where("status", "==", status)
    return [{"id":d["id"],"title":d["title"],"department":d["department"],"category":d["category"],
             "status":d["status"],"state":d["state"],"created_at":d["created_at"]}
            for d in [x.to_dict() for x in q.limit(limit).stream()]]

@app.get("/api/stats")
async def stats():
    all_d = [x.to_dict() for x in db.collection("grievances").stream()]
    total = len(all_d)
    resolved = sum(1 for d in all_d if d.get("status") == "resolved")
    dept_counts = {}
    for d in all_d:
        k = d.get("department","Other"); dept_counts[k] = dept_counts.get(k,0)+1
    return {"total": total, "resolved": resolved,
            "pending": sum(1 for d in all_d if d.get("status")=="pending"),
            "in_progress": sum(1 for d in all_d if d.get("status")=="in_progress"),
            "resolution_rate": round(resolved/total*100 if total else 0, 1),
            "by_department": dept_counts}

@app.patch("/api/admin/grievances/{gid}/status", dependencies=[Depends(verify_admin)])
async def update_status(gid: str, update: StatusUpdate):
    ref = db.collection("grievances").document(gid)
    doc = ref.get()
    if not doc.exists: raise HTTPException(404, "Not found")
    g = doc.to_dict(); now = datetime.datetime.utcnow().isoformat()
    ref.update({"status": update.status, "updated_at": now,
                "officer_name": update.officer_name,
                "timeline": firestore.ArrayUnion([{"event": f"Status → {update.status}",
                    "timestamp": now, "by": update.officer_name or "Admin", "remark": update.remark or ""}]),
                "remarks": firestore.ArrayUnion([update.remark] if update.remark else [])})
    send_email(g["email"], f"📋 Grievance Update — {gid}",
               f"<p>Your grievance <strong>{gid}</strong> is now <strong>{update.status}</strong>. {update.remark or ''}</p>")
    send_sms(g["phone"], f"Jan Setu: {gid} → {update.status.upper()}. {update.remark or ''}")
    return {"success": True}

@app.get("/api/admin/grievances", dependencies=[Depends(verify_admin)])
async def admin_list(limit: int = 50, status: Optional[str] = None, department: Optional[str] = None):
    q = db.collection("grievances").order_by("created_at", direction=firestore.Query.DESCENDING)
    if status: q = q.where("status", "==", status)
    if department: q = q.where("department", "==", department)
    return [d.to_dict() for d in q.limit(limit).stream()]

@app.delete("/api/admin/grievances/{gid}", dependencies=[Depends(verify_admin)])
async def delete_grievance(gid: str):
    db.collection("grievances").document(gid).delete()
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
        "created_at": now,
        "updated_at": now,
        "status": "open",     # open | acknowledged | in_review | addressed
        "upvote_cities": [],  # which cities have upvoted
    }
    db.collection("city_issues").document(iid).set(doc)
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
    q = db.collection("city_issues")
    if city:     q = q.where("city", "==", city)
    if state:    q = q.where("state", "==", state)
    if category: q = q.where("category", "==", category)

    if sort == "recent":
        q = q.order_by("created_at", direction=firestore.Query.DESCENDING)
    else:
        q = q.order_by("votes", direction=firestore.Query.DESCENDING)

    docs = [d.to_dict() for d in q.limit(limit).stream()]
    # Strip voter phone list from public response
    for d in docs:
        d.pop("voters", None)
    return docs


@app.get("/api/issues/{iid}")
async def get_issue(iid: str):
    doc = db.collection("city_issues").document(iid).get()
    if not doc.exists: raise HTTPException(404, "Issue not found")
    d = doc.to_dict(); d.pop("voters", None)
    return d


@app.post("/api/issues/{iid}/vote")
async def vote_issue(iid: str, vote: VoteCast):
    """
    Cast a vote on a city issue.
    One phone number = one vote per issue.
    Tracks which cities are backing the issue.
    """
    import hashlib
    voter_hash = hashlib.sha256(vote.voter_phone.encode()).hexdigest()[:16]
    ref = db.collection("city_issues").document(iid)
    doc = ref.get()
    if not doc.exists: raise HTTPException(404, "Issue not found")
    d = doc.to_dict()

    if voter_hash in (d.get("voters") or []):
        raise HTTPException(409, "Already voted")

    city_votes = d.get("city_votes", {})
    city_votes[vote.city] = city_votes.get(vote.city, 0) + 1

    upvote_cities = d.get("upvote_cities", [])
    if vote.city not in upvote_cities:
        upvote_cities.append(vote.city)

    ref.update({
        "votes": firestore.Increment(1),
        "voters": firestore.ArrayUnion([voter_hash]),
        "city_votes": city_votes,
        "upvote_cities": upvote_cities,
        "updated_at": datetime.datetime.utcnow().isoformat(),
    })
    return {"success": True, "new_vote_count": d["votes"] + 1}


@app.delete("/api/issues/{iid}/vote")
async def unvote_issue(iid: str, vote: VoteCast):
    """Remove a previously cast vote."""
    import hashlib
    voter_hash = hashlib.sha256(vote.voter_phone.encode()).hexdigest()[:16]
    ref = db.collection("city_issues").document(iid)
    doc = ref.get()
    if not doc.exists: raise HTTPException(404, "Issue not found")
    d = doc.to_dict()
    if voter_hash not in (d.get("voters") or []):
        raise HTTPException(409, "Vote not found")

    city_votes = d.get("city_votes", {})
    if city_votes.get(vote.city, 0) > 0:
        city_votes[vote.city] -= 1

    ref.update({
        "votes": firestore.Increment(-1),
        "voters": firestore.ArrayRemove([voter_hash]),
        "city_votes": city_votes,
        "updated_at": datetime.datetime.utcnow().isoformat(),
    })
    return {"success": True}


@app.post("/api/issues/{iid}/comments")
async def add_comment(iid: str, comment: IssueComment):
    """Add a citizen comment/experience to an issue."""
    ref = db.collection("city_issues").document(iid)
    if not ref.get().exists: raise HTTPException(404, "Issue not found")
    entry = {
        "author": comment.author,
        "text": comment.text,
        "city": comment.city,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    ref.update({"comments": firestore.ArrayUnion([entry]),
                "updated_at": datetime.datetime.utcnow().isoformat()})
    return {"success": True}


@app.get("/api/issues/trending/national")
async def trending_national(limit: int = 10):
    """Top issues by total votes across ALL cities (national leaderboard)."""
    docs = db.collection("city_issues")\
        .order_by("votes", direction=firestore.Query.DESCENDING)\
        .limit(limit).stream()
    result = []
    for d in docs:
        g = d.to_dict(); g.pop("voters", None)
        result.append(g)
    return result


@app.get("/api/issues/stats/cities")
async def city_stats():
    """Per-city issue and vote summary."""
    docs = [d.to_dict() for d in db.collection("city_issues").stream()]
    city_map = {}
    for d in docs:
        c = d.get("city", "Unknown")
        if c not in city_map:
            city_map[c] = {"city": c, "state": d.get("state",""), "issues": 0, "total_votes": 0, "categories": {}}
        city_map[c]["issues"] += 1
        city_map[c]["total_votes"] += d.get("votes", 0)
        cat = d.get("category","Other")
        city_map[c]["categories"][cat] = city_map[c]["categories"].get(cat, 0) + 1
    return sorted(city_map.values(), key=lambda x: x["total_votes"], reverse=True)


# ── Admin: update issue status ───────────────────────────────────────────────
@app.patch("/api/admin/issues/{iid}/status", dependencies=[Depends(verify_admin)])
async def admin_update_issue(iid: str, update: StatusUpdate):
    ref = db.collection("city_issues").document(iid)
    if not ref.get().exists: raise HTTPException(404, "Not found")
    ref.update({"status": update.status, "updated_at": datetime.datetime.utcnow().isoformat()})
    return {"success": True}


@app.delete("/api/admin/issues/{iid}", dependencies=[Depends(verify_admin)])
async def admin_delete_issue(iid: str, _=Depends(verify_admin)):
    db.collection("city_issues").document(iid).delete()
    return {"success": True}


@app.get("/")
def root():
    return {"status": "Jan Setu API v2 running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))