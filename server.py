import os
import re
import base64
import threading
import time
from datetime import timedelta

import cloudinary
from cloudinary import uploader as cloudinary_uploader
from flask import (
    Flask, request, jsonify, send_from_directory, abort,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from flask_cors import CORS
from sqlalchemy import or_, func, inspect, text
from werkzeug.exceptions import HTTPException

# ─────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)


def resolve_frontend_dist():
    """Locate the built front-end (CRA outputs to 'build', config may use 'dist')."""
    for name in ("dist", "build"):
        candidate = os.path.join(PROJECT_ROOT, "frontend", name)
        if os.path.isfile(os.path.join(candidate, "index.html")):
            return candidate
    return os.path.join(PROJECT_ROOT, "frontend", "dist")


FRONTEND_DIST = resolve_frontend_dist()


# ─────────────────────────────────────────────────────────────
# Minimal .env loader (no external dependency)
# ─────────────────────────────────────────────────────────────
def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_dotenv(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = os.environ.get("SECRET_KEY")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not SECRET_KEY or SECRET_KEY.startswith("replace-with-"):
    raise RuntimeError("SECRET_KEY must be configured in the environment")
if not ADMIN_PASSWORD or ADMIN_PASSWORD.startswith("replace-with-"):
    raise RuntimeError("ADMIN_PASSWORD must be configured in the environment")
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",") if o.strip()
]
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "8"))
MAX_PHOTO_BYTES = int(os.environ.get("MAX_PHOTO_BYTES", "5242880"))
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True,
)
# Demo/seed records are opt-in. Keep the production database blank so the
# admin starts from a clean table: set SEED_DEMO=true to re-enable seeding.
SEED_DEMO = os.environ.get("SEED_DEMO", "false").lower()
PORT = int(os.environ.get("PORT", "8000"))
ID_PREFIX = os.environ.get("ID_PREFIX", "JT")

# ─────────────────────────────────────────────────────────────
# App + extensions
# ─────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=None)
app.config["SECRET_KEY"] = SECRET_KEY
# Retain the local SQLite database unless a deployment provides DATABASE_URL.
DB_PATH = os.environ.get("VOTERS_DB", os.path.join(BASE_DIR, "voters.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or (
    "sqlite:///" + DB_PATH.replace("\\", "/")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Cross-site sessions require SameSite=None and Secure; browsers only send
# these cookies over HTTPS.
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=SESSION_HOURS)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)


# ─────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)


# Full voter schema (mirrors bulk_generate_50k.py / voters table)
class Voter(db.Model):
    __tablename__ = "voters"
    id = db.Column(db.String(64), primary_key=True)
    idNumber = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(120))
    surname = db.Column(db.String(120))
    dob = db.Column(db.String(20))
    sex = db.Column(db.String(20))
    contact = db.Column(db.String(40))
    residence = db.Column(db.String(200))
    village = db.Column(db.String(120))
    wd = db.Column(db.String(40))
    llg = db.Column(db.String(40))
    district = db.Column(db.String(120))
    maritalStatus = db.Column(db.String(40))
    spouseName = db.Column(db.String(120))
    spouseSex = db.Column(db.String(20))
    siblings = db.Column(db.String(20))
    spouseVillage = db.Column(db.String(120))
    spouseDistrict = db.Column(db.String(120))
    spouseLLG = db.Column(db.String(40))
    spouseContact = db.Column(db.String(40))
    pollingPlace = db.Column(db.String(120))
    ballotPapers = db.Column(db.String(20))
    capacityVotes1 = db.Column(db.String(20))
    capacityVotes2 = db.Column(db.String(20))
    expectedVotes1 = db.Column(db.String(20))
    expectedVotes2 = db.Column(db.String(20))
    estimatedVotes1 = db.Column(db.String(20))
    estimatedVotes2 = db.Column(db.String(20))
    verifiedBy = db.Column(db.String(120))
    referredBy = db.Column(db.String(120))
    officialWd = db.Column(db.String(40))
    officialVillage = db.Column(db.String(120))
    officialLLG = db.Column(db.String(40))
    officialDistrict = db.Column(db.String(120))
    signed = db.Column(db.String(120))
    witness = db.Column(db.String(120))
    declarationDate = db.Column(db.String(20))
    vote = db.Column(db.String(20))
    photo_filename = db.Column(db.String(255))
    photo_url = db.Column(db.String(1000))
    photo_public_id = db.Column(db.String(255))


VOTER_COLUMNS = [
    c.name for c in Voter.__table__.columns if c.name != "photo_public_id"
]

# Fields that may be supplied by the front-end form
FORM_FIELDS = [
    "name", "surname", "idNumber", "district", "contact", "dob", "sex",
    "residence", "village", "llg", "wd", "maritalStatus", "spouseName",
    "spouseSex", "siblings", "spouseVillage", "spouseDistrict", "spouseLLG",
    "spouseContact", "pollingPlace", "ballotPapers", "capacityVotes1",
    "capacityVotes2", "expectedVotes1", "expectedVotes2", "estimatedVotes1",
    "estimatedVotes2", "verifiedBy", "referredBy", "officialWd",
    "officialVillage", "officialLLG", "officialDistrict", "signed",
    "witness", "declarationDate", "vote",
]
REQUIRED_FIELDS = ["name", "surname", "district", "contact", "dob", "sex"]

# Allowed sort columns for /api/getForms, keyed by column name.
SORT_COLUMNS = {
    "name": Voter.name,
    "idNumber": Voter.idNumber,
    "district": Voter.district,
    "llg": Voter.llg,
    "wd": Voter.wd,
    "residence": Voter.residence,
    "sex": Voter.sex,
}


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
id_lock = threading.Lock()


def generate_voter_id():
    """Sequential, collision-free 'V####' id with a thread lock."""
    with id_lock:
        max_n = 0
        for (vid,) in db.session.query(Voter.id).filter(Voter.id.like("V%")).all():
            try:
                n = int(vid[1:])
                if n > max_n:
                    max_n = n
            except (ValueError, IndexError):
                pass
        while True:
            max_n += 1
            candidate = f"V{max_n:04d}"
            if not db.session.get(Voter, candidate):
                return candidate


id_number_lock = threading.Lock()
ID_PATTERN = re.compile(r"^([A-Za-z]+)(\d+)$")


def generate_id_number():
    """Sequential, collision-free '<PREFIX>###' id number with a thread lock.

    Continues from the highest existing '<PREFIX><number>' value. Existing
    records keep their current IDs; new records use the configured prefix.
    """
    with id_number_lock:
        max_n = 0
        width = 3
        prefix_like = ID_PREFIX.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = db.session.query(Voter.idNumber).filter(
            Voter.idNumber.like(f"{prefix_like}%", escape="\\")
        )
        for (num,) in rows:
            m = ID_PATTERN.match(num or "")
            if m and m.group(1) == ID_PREFIX:
                n = int(m.group(2))
                width = max(width, len(m.group(2)))
                if n > max_n:
                    max_n = n
        while True:
            max_n += 1
            candidate = f"{ID_PREFIX}{max_n:0{width}d}"
            if not Voter.query.filter_by(idNumber=candidate).first():
                return candidate


def voter_to_dict(v):
    d = {col: getattr(v, col) for col in VOTER_COLUMNS}
    if v.photo_url:
        d["photo_filename"] = None
    elif v.photo_filename:
        d["photo_url"] = f"/api/photo/{v.photo_filename}"
    else:
        d["photo_filename"] = None
        d["photo_url"] = None
    return d


class CloudinaryConfigurationError(RuntimeError):
    pass


class CloudinaryUploadError(RuntimeError):
    pass


ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def save_photo(data_url):
    """Validate and upload a base64 data URL. Returns Cloudinary metadata or None."""
    if not data_url:
        return None
    m = re.match(r"^data:(?P<type>[^;]+);base64,(?P<data>.+)$", data_url)
    if not m:
        return None
    mime = m.group("type")
    if mime not in ALLOWED_MIME:
        return None
    try:
        raw = base64.b64decode(m.group("data"), validate=True)
    except Exception:
        return None
    if len(raw) > MAX_PHOTO_BYTES:
        return None
    if not all((CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET)):
        raise CloudinaryConfigurationError("Cloudinary credentials are not configured")

    try:
        result = cloudinary_uploader.upload(
            data_url, folder="js2027", resource_type="image"
        )
    except Exception as e:
        raise CloudinaryUploadError("Cloudinary photo upload failed") from e
    secure_url = result.get("secure_url")
    public_id = result.get("public_id")
    if not secure_url or not public_id:
        raise RuntimeError("Cloudinary upload response omitted secure_url or public_id")
    return {"url": secure_url, "public_id": public_id}


def delete_stored_photo(photo_filename=None, photo_public_id=None):
    if photo_public_id:
        result = cloudinary_uploader.destroy(
            photo_public_id, resource_type="image", invalidate=True
        )
        if result.get("result") not in ("ok", "not found"):
            raise RuntimeError("Cloudinary did not confirm photo deletion")
    elif photo_filename:
        os.remove(os.path.join(PHOTOS_DIR, photo_filename))


def cleanup_uploaded_photo(uploaded_photo):
    if not uploaded_photo:
        return
    try:
        delete_stored_photo(photo_public_id=uploaded_photo["public_id"])
    except Exception:
        app.logger.exception("Failed to clean up an uncommitted Cloudinary photo")


def find_voter(identifier):
    """Look up a voter by primary id OR idNumber."""
    v = db.session.get(Voter, identifier)
    if v:
        return v
    return Voter.query.filter_by(idNumber=identifier).first()


def validate_dob(value):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value or ""):
        return False
    try:
        time.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


# Simple in-memory login rate limiting
login_attempts = {}
RATE_LIMIT = 5
RATE_WINDOW = 60


def rate_limited(ip):
    now = time.time()
    attempts = login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < RATE_WINDOW]
    login_attempts[ip] = attempts
    return len(attempts) >= RATE_LIMIT


def register_attempt(ip):
    login_attempts.setdefault(ip, []).append(time.time())


# ─────────────────────────────────────────────────────────────
# Auth endpoints
# ─────────────────────────────────────────────────────────────
@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"authenticated": False, "message": "Authentication required"}), 401


@app.route("/api/auth/status", methods=["GET"])
def auth_status():
    return jsonify({
        "authenticated": bool(current_user.is_authenticated),
        "username": current_user.username if current_user.is_authenticated else None,
    })


@app.route("/api/login", methods=["POST"])
def login():
    ip = request.remote_addr
    if rate_limited(ip):
        return jsonify({"success": False, "message": "Too many attempts. Try again later."}), 429
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")
    user = User.query.filter_by(username=username).first()
    if user and user.password == password:
        login_user(user)
        return jsonify({"success": True, "message": "Login successful"})
    register_attempt(ip)
    return jsonify({"success": False, "message": "Invalid credentials"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    logout_user()
    return jsonify({"success": True, "message": "Logged out"})


# ─────────────────────────────────────────────────────────────
# Voter (form) API
# ─────────────────────────────────────────────────────────────
@app.route("/api/saveForm", methods=["POST"])
def save_form():
    uploaded_photo = None
    try:
        data = request.get_json(silent=True) or {}
        missing = [f for f in REQUIRED_FIELDS if not str(data.get(f, "")).strip()]
        if missing:
            return jsonify({"success": False, "message": f"Missing required fields: {', '.join(missing)}"}), 400
        if not validate_dob(data.get("dob")):
            return jsonify({"success": False, "message": "Invalid date of birth (expected YYYY-MM-DD)"}), 400

        id_number = str(data.get("idNumber") or "").strip()
        if id_number:
            if len(id_number) > 64 or not ID_PATTERN.match(id_number):
                return jsonify({"success": False, "message": "Invalid ID number format (expected letters followed by numbers, max 64 characters)"}), 400
        else:
            id_number = generate_id_number()
        if Voter.query.filter_by(idNumber=id_number).first():
            return jsonify({"success": False, "message": f"ID number '{id_number}' already exists"}), 409

        uploaded_photo = save_photo(data.get("photo"))
        if data.get("photo") and uploaded_photo is None:
            return jsonify({"success": False, "message": "Invalid or too-large photo"}), 400

        voter = Voter(id=generate_voter_id(), idNumber=id_number)
        for f in FORM_FIELDS:
            if f == "idNumber" or f not in data:
                continue
            setattr(voter, f, str(data[f]) if data[f] is not None else None)
        voter.photo_filename = None
        voter.photo_url = uploaded_photo["url"] if uploaded_photo else None
        voter.photo_public_id = uploaded_photo["public_id"] if uploaded_photo else None
        db.session.add(voter)
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Voter record saved successfully",
            "id": voter.id,
            "idNumber": voter.idNumber,
        })
    except CloudinaryConfigurationError as e:
        db.session.rollback()
        app.logger.error("saveForm configuration error: %s", e)
        return jsonify({"success": False, "message": "Photo storage is not configured"}), 503
    except CloudinaryUploadError:
        db.session.rollback()
        cleanup_uploaded_photo(uploaded_photo)
        app.logger.exception("saveForm Cloudinary upload failed")
        return jsonify({"success": False, "message": "Photo upload failed"}), 502
    except Exception as e:
        db.session.rollback()
        cleanup_uploaded_photo(uploaded_photo)
        app.logger.error("saveForm error: %s", e)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@app.route("/api/getForms", methods=["GET"])
def get_forms():
    try:
        try:
            page = max(1, int(request.args.get("page", 1)))
            per_page = max(1, min(100, int(request.args.get("perPage", 10))))
        except ValueError:
            page, per_page = 1, 10

        search = (request.args.get("search") or "").strip()
        query = Voter.query
        if search:
            like = f"%{search}%"
            query = query.filter(
                or_(
                    Voter.name.ilike(like),
                    Voter.surname.ilike(like),
                    Voter.idNumber.ilike(like),
                    Voter.district.ilike(like),
                    Voter.village.ilike(like),
                    Voter.contact.ilike(like),
                    Voter.id.ilike(like),
                    Voter.llg.ilike(like),
                    Voter.wd.ilike(like),
                )
            )

        total = query.count()
        total_pages = (total + per_page - 1) // per_page if total else 0
        offset = (page - 1) * per_page

        # Sort format: "<column>_<asc|desc>" (e.g. name_asc). Unknown values
        # fall back to the default ordering.
        sort_key = (request.args.get("sort") or "").strip()
        column_name, _, direction = sort_key.partition("_")
        sort_column = SORT_COLUMNS.get(column_name)
        if sort_column is None:
            order_by = Voter.id
        else:
            order_by = func.lower(sort_column)
            if direction == "desc":
                order_by = order_by.desc()
        records = query.order_by(order_by).offset(offset).limit(per_page).all()

        return jsonify({
            "records": [voter_to_dict(v) for v in records],
            "total": total,
            "totalPages": total_pages,
            "page": page,
            "perPage": per_page,
        })
    except Exception as e:
        app.logger.error("getForms error: %s", e)
        return jsonify({"records": [], "total": 0, "totalPages": 0, "message": "Internal server error"}), 500


@app.route("/api/getForm/<identifier>", methods=["GET"])
def get_form(identifier):
    try:
        v = find_voter(identifier)
        if not v:
            return jsonify({"message": "Record not found"}), 404
        return jsonify(voter_to_dict(v))
    except Exception as e:
        app.logger.error("getForm error: %s", e)
        return jsonify({"message": "Internal server error"}), 500


@app.route("/api/updateForm/<identifier>", methods=["PUT"])
def update_form(identifier):
    uploaded_photo = None
    try:
        v = find_voter(identifier)
        if not v:
            return jsonify({"message": "Record not found"}), 404
        data = request.get_json(silent=True) or {}

        if "dob" in data and data["dob"] and not validate_dob(data["dob"]):
            return jsonify({"success": False, "message": "Invalid date of birth"}), 400

        old_photo_filename = v.photo_filename
        old_photo_public_id = v.photo_public_id
        uploaded_photo = None
        if "photo" in data:
            uploaded_photo = save_photo(data.get("photo"))
            if data.get("photo") and uploaded_photo is None:
                return jsonify({"success": False, "message": "Invalid or too-large photo"}), 400
            v.photo_filename = None
            v.photo_url = uploaded_photo["url"] if uploaded_photo else None
            v.photo_public_id = uploaded_photo["public_id"] if uploaded_photo else None

        # The ID number is auto-generated and never editable; any idNumber in
        # the request body is ignored so record sequencing stays intact.
        for f in FORM_FIELDS:
            if f in data and f != "idNumber":
                setattr(v, f, str(data[f]) if data[f] is not None else None)

        db.session.commit()
        response = {
            "success": True,
            "message": "Record updated successfully",
            "record": voter_to_dict(v),
        }
        if "photo" in data and (old_photo_filename or old_photo_public_id):
            try:
                delete_stored_photo(old_photo_filename, old_photo_public_id)
            except Exception:
                app.logger.exception("Failed to remove replaced photo")
                response["warning"] = "Record updated, but its previous photo could not be removed"
        return jsonify(response)
    except CloudinaryConfigurationError as e:
        db.session.rollback()
        app.logger.error("updateForm configuration error: %s", e)
        return jsonify({"success": False, "message": "Photo storage is not configured"}), 503
    except CloudinaryUploadError:
        db.session.rollback()
        cleanup_uploaded_photo(uploaded_photo)
        app.logger.exception("updateForm Cloudinary upload failed")
        return jsonify({"success": False, "message": "Photo upload failed"}), 502
    except Exception as e:
        db.session.rollback()
        cleanup_uploaded_photo(uploaded_photo)
        app.logger.error("updateForm error: %s", e)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@app.route("/api/deleteForm/<identifier>", methods=["DELETE"])
def delete_form(identifier):
    try:
        v = find_voter(identifier)
        if not v:
            return jsonify({"message": "Record not found"}), 404
        photo_filename = v.photo_filename
        photo_public_id = v.photo_public_id
        db.session.delete(v)
        db.session.commit()
        response = {"success": True, "message": "Record deleted successfully"}
        if photo_filename or photo_public_id:
            try:
                delete_stored_photo(photo_filename, photo_public_id)
            except Exception:
                app.logger.exception("Failed to remove deleted record photo")
                response["warning"] = "Record deleted, but its photo could not be removed"
        return jsonify(response)
    except Exception as e:
        db.session.rollback()
        app.logger.error("deleteForm error: %s", e)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@app.route("/api/statistics", methods=["GET"])
def statistics():
    try:
        total = db.session.query(func.count(Voter.id)).scalar() or 0
        by_district = {}
        rows = db.session.query(Voter.district, func.count(Voter.id)).group_by(Voter.district).all()
        for district, count in rows:
            by_district[district or "Unknown"] = count
        by_sex = {}
        rows = db.session.query(Voter.sex, func.count(Voter.id)).group_by(Voter.sex).all()
        for sex, count in rows:
            by_sex[sex or "Unknown"] = count

        # Live counts of records by Vote selection, split by the graph modes
        # the front-end toggles between (Overall / Male / Female).
        def vote_counts(sex_filter=None):
            counts = {"vote1": 0, "vote2": 0, "vote3": 0}
            query = db.session.query(Voter.vote, func.count(Voter.id))
            if sex_filter is not None:
                query = query.filter(sex_filter)
            for vote, count in query.group_by(Voter.vote).all():
                key = (vote or "").strip().lower().replace(" ", "")
                if key in counts:
                    counts[key] = count
            return counts

        return jsonify({
            "total": total,
            "byDistrict": by_district,
            "bySex": by_sex,
            "overall": vote_counts(),
            "male": vote_counts(func.lower(Voter.sex) == "male"),
            "female": vote_counts(func.lower(Voter.sex) == "female"),
        })
    except Exception as e:
        app.logger.error("statistics error: %s", e)
        return jsonify({
            "total": 0, "byDistrict": {}, "bySex": {},
            "overall": {"vote1": 0, "vote2": 0, "vote3": 0},
            "male": {"vote1": 0, "vote2": 0, "vote3": 0},
            "female": {"vote1": 0, "vote2": 0, "vote3": 0},
        }), 500


@app.route("/api/photo/<path:filename>", methods=["GET"])
def serve_photo(filename):
    try:
        return send_from_directory(PHOTOS_DIR, filename, max_age=3600)
    except FileNotFoundError:
        abort(404)


# ─────────────────────────────────────────────────────────────
# Front-end (built React app) serving
# ─────────────────────────────────────────────────────────────
FALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Voters Database</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
    .wrap { max-width: 640px; margin: 12vh auto; padding: 0 20px; text-align: center; }
    h1 { font-size: 1.8rem; }
    code { background: #1e293b; padding: 2px 6px; border-radius: 4px; }
    .ok { color: #4ade80; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Voters Database API</h1>
    <p class="ok">Server is running.</p>
    <p>The REST API is available under <code>/api/*</code>.</p>
    <p>Health check: <code>GET /api/auth/status</code></p>
    <p>Build the front-end (<code>npm run build</code> in <code>frontend/</code>) to serve the web UI from this server.</p>
  </div>
</body>
</html>"""


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    index_path = os.path.join(FRONTEND_DIST, "index.html")
    if path:
        candidate = os.path.join(FRONTEND_DIST, path)
        if os.path.isfile(candidate):
            return send_from_directory(FRONTEND_DIST, path)
    if os.path.isfile(index_path):
        return send_from_directory(FRONTEND_DIST, "index.html")
    return FALLBACK_HTML, 200


# ─────────────────────────────────────────────────────────────
# In-place schema migration (adds columns introduced after first deploy)
# ─────────────────────────────────────────────────────────────
def ensure_voter_columns():
    """Add missing Voter columns to an existing SQLite database."""
    inspector = inspect(db.engine)
    existing = {col["name"] for col in inspector.get_columns("voters")}
    additions = {
        "vote": "VARCHAR(20)",
        "photo_url": "VARCHAR(1000)",
        "photo_public_id": "VARCHAR(255)",
    }
    for column, col_type in additions.items():
        if column not in existing:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE voters ADD COLUMN {column} {col_type}"))


# ─────────────────────────────────────────────────────────────
# Seed demo data (gated; auto-seeds empty database)
# ─────────────────────────────────────────────────────────────
def seed_demo_data():
    if SEED_DEMO == "false":
        return
    if db.session.query(func.count(Voter.id)).scalar():
        return

    demo = [
        dict(id="seed-1", idNumber="V1005", name="John", surname="Soso",
             dob="1990-01-15", sex="Male", contact="+67570000001",
             district="Port Moresby", residence="Section 1, East Road",
             village="Koki", llg="LLG 2", wd="WD 1"),
    ]
    first_names = ["John", "Mary", "Peter", "Rose", "Daniel", "Grace", "Michael", "Rachael"]
    surnames = ["Soso", "Toka", "Arua", "Mane", "Kila", "Peni", "Kapi", "Wari"]
    districts = ["Port Moresby", "Lae", "Madang", "Goroka", "Mount Hagen", "Wabag"]
    villages = ["Koki", "Eriku", "Wampar", "Kondiu", "Simbu", "Kundiawa"]
    sexes = ["Male", "Female"]
    for i in range(2, 61):
        demo.append(dict(
            id=f"seed-{i}", idNumber=f"D{1000 + i}",
            name=first_names[i % len(first_names)],
            surname=surnames[i % len(surnames)],
            dob=f"19{80 + (i % 25)}-0{(i % 12) + 1}-1{i % 9}",
            sex=sexes[i % 2], contact=f"+67570{i:05d}",
            district=districts[i % len(districts)],
            residence="Block 4, Main Street",
            village=villages[i % len(villages)],
            llg=f"LLG {i % 20 + 1}", wd=f"WD {i % 20 + 1}",
        ))
    for d in demo:
        db.session.add(Voter(**d))
    db.session.commit()


# ─────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    ensure_voter_columns()
    # Always ensure admin user exists AND password is updated from ENV
    admin = User.query.filter_by(username=ADMIN_USERNAME).first()
    if not admin:
        # also check if admin exists with different username and update it
        admin = User.query.filter_by(username="admin").first()
        if admin:
            admin.username = ADMIN_USERNAME
            admin.password = ADMIN_PASSWORD
        else:
            db.session.add(User(username=ADMIN_USERNAME, password=ADMIN_PASSWORD))
    else:
        # Update password every startup from ENV
        admin.password = ADMIN_PASSWORD
    db.session.commit()
    seed_demo_data()


@app.errorhandler(HTTPException)
def handle_http_exception(e):
    return jsonify({"message": e.description}), e.code


if __name__ == "__main__":
    try:
        from waitress import serve
        import threading

        # Bind on IPv6 (::) as well as IPv4 (0.0.0.0) so that both
        # 'localhost' (which Python's requests resolves to ::1 first) and
        # 127.0.0.1 connect immediately without the ~2s IPv4/IPv6 fallback.
        def serve_ipv6():
            try:
                serve(app, host="::", port=PORT, threads=8)
            except Exception as e:  # pragma: no cover
                app.logger.warning("IPv6 bind unavailable: %s", e)

        ipv6_thread = threading.Thread(target=serve_ipv6, daemon=True)
        ipv6_thread.start()

        print(f"* Serving on http://0.0.0.0:{PORT} (waitress, IPv4+IPv6)")
        try:
            serve(app, host="0.0.0.0", port=PORT, threads=8)
        except Exception:  # pragma: no cover - IPv4 already taken by dual-stack
            serve(app, host="::", port=PORT, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=PORT, debug=False)
