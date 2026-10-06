"""CareTrack personal health record and medicine management system."""
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import json
import os
import re
import secrets
import zipfile

from bson import ObjectId
from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
ALLOWED_UPLOADS = {"pdf", "png", "jpg", "jpeg", "gif", "webp"}
UPLOAD_CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    MONGO_URI=os.environ.get("MONGODB_URI", "mongodb://localhost:27017"),
    MONGO_DATABASE=os.environ.get("MONGODB_DATABASE", "personal_health_records"),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
)
mongo_client = MongoClient(app.config["MONGO_URI"], serverSelectionTimeoutMS=3000)
database = mongo_client[app.config["MONGO_DATABASE"]]
users = database.users
profiles = database.profiles
health_records = database.health_records
medicine_collection = database.medicines
dose_logs = database.dose_logs
appointments_collection = database.appointments
uploaded_files = database.uploaded_files

_database_initialized = False


def initialize_database():
    """Check Atlas and create indexes on first database-backed request, not import."""
    global _database_initialized
    if _database_initialized:
        return
    mongo_client.admin.command("ping")
    # Unique indexes preserve account and dose-log uniqueness. Other indexes speed up
    # user-scoped pages. Running this repeatedly is safe; PyMongo skips existing ones.
    users.create_index([("email", ASCENDING)], unique=True)
    users.create_index([("username", ASCENDING)], unique=True)
    profiles.create_index([("user_id", ASCENDING)], unique=True)
    health_records.create_index([("user_id", ASCENDING), ("record_date", DESCENDING)])
    medicine_collection.create_index([("user_id", ASCENDING)])
    dose_logs.create_index([("medicine_id", ASCENDING), ("scheduled_date", ASCENDING),
                            ("scheduled_time", ASCENDING)], unique=True)
    dose_logs.create_index([("user_id", ASCENDING), ("scheduled_date", ASCENDING)])
    appointments_collection.create_index([("user_id", ASCENDING), ("appointment_at", ASCENDING)])
    uploaded_files.create_index([("filename", ASCENDING)], unique=True)
    uploaded_files.create_index([("user_id", ASCENDING)])
    _database_initialized = True

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"


def mongo_object(document, date_fields=(), datetime_fields=()):
    """Turn a MongoDB document into an object convenient for beginner templates."""
    if document is None:
        return None
    values = dict(document)
    values["id"] = str(values.pop("_id"))
    for field in date_fields:
        if values.get(field) and isinstance(values[field], str):
            values[field] = date.fromisoformat(values[field])
    for field in datetime_fields:
        if values.get(field) and isinstance(values[field], str):
            values[field] = datetime.fromisoformat(values[field])
    return SimpleNamespace(**values)


class User(UserMixin):
    """Small Flask-Login adapter around one MongoDB account document."""
    def __init__(self, document):
        self.id = str(document["_id"])
        self.email = document["email"]
        self.username = document["username"]
        self.password_hash = document["password_hash"]

    @property
    def profile(self):
        profile_doc = profiles.find_one({"user_id": self.id})
        return mongo_object(profile_doc, date_fields=("date_of_birth",)) or SimpleNamespace(
            full_name="", date_of_birth=None, phone="", address="", emergency_name="",
            emergency_phone="", allergies="", important_notes="", photo_filename=None)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    try:
        document = users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None
    return User(document) if document else None


@app.before_request
def csrf_guard():
    if request.method == "POST":
        expected, supplied = session.get("csrf_token"), request.form.get("csrf_token")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            abort(400, "Form expired or invalid. Reload the page and try again.")


@app.before_request
def database_guard():
    """Keep static/login pages available and return a useful status if Atlas is down."""
    if request.path in {"/style.css", "/favicon.ico"}:
        return None
    if request.method == "GET" and request.endpoint in {"login", "register"}:
        return None
    try:
        initialize_database()
    except PyMongoError as exc:
        # Avoid logging the connection URI or database credentials.
        app.logger.error("MongoDB initialization failed (%s)", type(exc).__name__)
        return ("MongoDB is not reachable. Check MONGODB_URI, the Atlas cluster status, "
                "and Atlas Network Access/IP access list.", 503)


@app.context_processor
def template_helpers():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf_token"], "today": date.today()}


def parse_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        abort(404)


def owned_or_404(collection, item_id):
    item = collection.find_one({"_id": parse_object_id(item_id), "user_id": current_user.id})
    if item is None:
        abort(404)
    return item


def upload_file(field_name, category):
    file = request.files.get(field_name)
    if not file or not file.filename:
        return None
    original = secure_filename(file.filename)
    if not original or "." not in original:
        raise ValueError("Choose a PDF or image file with a valid filename.")
    ext = original.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_UPLOADS:
        raise ValueError("Allowed attachments: PDF, PNG, JPG, GIF, or WEBP.")
    content = file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Files must be 8 MB or smaller.")
    valid = (content.startswith(b"%PDF-") if ext == "pdf" else
             content.startswith(b"\x89PNG\r\n\x1a\n") if ext == "png" else
             content.startswith(b"\xff\xd8\xff") if ext in {"jpg", "jpeg"} else
             content[:6] in {b"GIF87a", b"GIF89a"} if ext == "gif" else
             content.startswith(b"RIFF") and content[8:12] == b"WEBP")
    if not valid:
        raise ValueError("The file contents do not match the selected PDF or image type.")
    stored = f"{current_user.id}_{category}_{secrets.token_hex(12)}.{ext}"
    uploaded_files.insert_one({"user_id": current_user.id, "filename": stored,
        "content_type": UPLOAD_CONTENT_TYPES[ext], "content": content})
    return stored


def safe_delete(filename):
    if filename:
        uploaded_files.delete_one({"user_id": current_user.id, "filename": filename})


def iso_date(value):
    return date.fromisoformat(value)


@app.route("/")
def index():
    return redirect(url_for("dashboard" if current_user.is_authenticated else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not re.fullmatch(r"[A-Za-z0-9._%+-]+@gmail\.com", email, re.I):
            flash("Please use a valid Gmail address ending in @gmail.com.", "danger")
        elif not re.fullmatch(r"[A-Za-z0-9_]{3,40}", username):
            flash("Username must be 3–40 characters (letters, numbers, underscores).", "danger")
        elif len(password) < 8:
            flash("Use a password with at least 8 characters.", "danger")
        elif password != confirm_password:
            flash("Passwords do not match.", "danger")
        else:
            user = User.__new__(User)
            user.email, user.username = email, username
            user.set_password(password)
            try:
                result = users.insert_one({"email": email, "username": username,
                                           "password_hash": user.password_hash})
                user.id = str(result.inserted_id)
                profiles.insert_one({"user_id": user.id, "full_name": "", "date_of_birth": None,
                    "phone": "", "address": "", "emergency_name": "", "emergency_phone": "",
                    "allergies": "", "important_notes": "", "photo_filename": None})
                login_user(user)
                flash("Account created. Add your profile details to get started.", "success")
                return redirect(url_for("profile"))
            except DuplicateKeyError:
                flash("That email or username is already registered.", "danger")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        account = users.find_one({"$or": [{"email": identity.lower()}, {"username": identity}]})
        user = User(account) if account else None
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Email/username or password was not recognized.", "danger")
    return render_template("auth.html", mode="login")


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    today_value = date.today().isoformat()
    meds = list(medicine_collection.find({"user_id": current_user.id, "active": True,
                               "start_date": {"$lte": today_value}, "end_date": {"$gte": today_value}}).sort("name", ASCENDING))
    schedule = []
    for med in meds:
        medicine = mongo_object(med, date_fields=("start_date", "end_date"))
        for reminder in medicine.reminder_times:
            log = dose_logs.find_one({"medicine_id": medicine.id, "scheduled_date": today_value,
                                      "scheduled_time": reminder})
            schedule.append((medicine, reminder, mongo_object(log)))
    record_docs = health_records.find({"user_id": current_user.id}).sort("record_date", DESCENDING).limit(4)
    records = [mongo_object(r, date_fields=("record_date",)) for r in record_docs]
    now = datetime.now().isoformat(timespec="minutes")
    appointment_docs = appointments_collection.find({"user_id": current_user.id,
        "appointment_at": {"$gte": now}}).sort("appointment_at", ASCENDING).limit(4)
    upcoming = [mongo_object(a, datetime_fields=("appointment_at",)) for a in appointment_docs]
    return render_template("dashboard.html", schedule=schedule, records=records,
                           appointments=upcoming, profile=current_user.profile)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    data = current_user.profile
    if request.method == "POST":
        try:
            photo = upload_file("photo", "profile")
            dob = request.form.get("date_of_birth", "")
            changes = {"full_name": request.form.get("full_name", "").strip(),
                "date_of_birth": dob or None, "phone": request.form.get("phone", "").strip(),
                "address": request.form.get("address", "").strip(),
                "emergency_name": request.form.get("emergency_name", "").strip(),
                "emergency_phone": request.form.get("emergency_phone", "").strip(),
                "allergies": request.form.get("allergies", "").strip(),
                "important_notes": request.form.get("important_notes", "").strip()}
            if dob:
                iso_date(dob)
            if photo:
                safe_delete(data.photo_filename)
                changes["photo_filename"] = photo
            profiles.update_one({"user_id": current_user.id}, {"$set": changes}, upsert=True)
            flash("Profile saved.", "success")
            return redirect(url_for("profile"))
        except (ValueError, OSError) as exc:
            flash(str(exc), "danger")
    return render_template("profile.html", profile=data)


@app.route("/records", methods=["GET", "POST"])
@login_required
def records():
    if request.method == "POST":
        try:
            record_date = iso_date(request.form.get("record_date", ""))
            record_type = request.form.get("record_type", "").strip()
            if not record_type:
                raise ValueError("Enter a record type.")
            attachment = upload_file("attachment", "record")
            health_records.insert_one({"user_id": current_user.id, "record_date": record_date.isoformat(),
                "record_type": record_type, "provider": request.form.get("provider", "").strip(),
                "notes": request.form.get("notes", "").strip(), "attachment_filename": attachment})
            flash("Health record added.", "success")
            return redirect(url_for("records"))
        except (ValueError, OSError) as exc:
            flash(str(exc), "danger")
    items = [mongo_object(r, date_fields=("record_date",)) for r in
             health_records.find({"user_id": current_user.id}).sort("record_date", DESCENDING)]
    return render_template("records.html", records=items)


@app.post("/records/<item_id>/delete")
@login_required
def delete_record(item_id):
    item = owned_or_404(health_records, item_id)
    safe_delete(item.get("attachment_filename"))
    health_records.delete_one({"_id": item["_id"], "user_id": current_user.id})
    flash("Record deleted.", "success")
    return redirect(url_for("records"))


@app.route("/medicines", methods=["GET", "POST"])
@login_required
def medicines():
    if request.method == "POST":
        try:
            name, dose = request.form.get("name", "").strip(), request.form.get("dose", "").strip()
            start = iso_date(request.form.get("start_date", ""))
            end = iso_date(request.form.get("end_date", ""))
            times = [t.strip() for t in request.form.get("reminder_times", "").split(",") if t.strip()]
            for value in times:
                datetime.strptime(value, "%H:%M")
            if not name or not dose or not times:
                raise ValueError("Medicine name, dose, and at least one reminder time are required.")
            if end < start:
                raise ValueError("End date must be on or after start date.")
            if len(times) > 8 or len(set(times)) != len(times):
                raise ValueError("Choose 1–8 unique reminder times.")
            medicine_collection.insert_one({"user_id": current_user.id, "name": name, "dose": dose,
                "instructions": request.form.get("instructions", "").strip(), "start_date": start.isoformat(),
                "end_date": end.isoformat(), "reminder_times": sorted(times), "active": True})
            flash("Medicine schedule added.", "success")
            return redirect(url_for("medicines"))
        except (ValueError, TypeError) as exc:
            flash(str(exc) or "Check the dates and reminder times.", "danger")
    item_docs = medicine_collection.find({"user_id": current_user.id}).sort([("active", DESCENDING), ("name", ASCENDING)])
    items = [mongo_object(m, date_fields=("start_date", "end_date")) for m in item_docs]
    week_start = date.today() - timedelta(days=date.today().weekday())
    labels, taken, skipped = [], [], []
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).isoformat()
        labels.append(date.fromisoformat(day).strftime("%a"))
        logs = list(dose_logs.find({"user_id": current_user.id, "scheduled_date": day}))
        taken.append(sum(log["status"] == "taken" for log in logs))
        skipped.append(sum(log["status"] == "skipped" for log in logs))
    return render_template("medicines.html", medicines=items, chart_labels=labels,
                           chart_taken=taken, chart_skipped=skipped)


@app.post("/doses/<medicine_id>/<scheduled_date>/<scheduled_time>/<status>")
@login_required
def mark_dose(medicine_id, scheduled_date, scheduled_time, status):
    med = owned_or_404(medicine_collection, medicine_id)
    if status not in {"taken", "skipped"}:
        abort(400)
    try:
        day = iso_date(scheduled_date)
        at = datetime.strptime(scheduled_time, "%H:%M").strftime("%H:%M")
    except ValueError:
        abort(400)
    if day != date.today() or at not in med["reminder_times"] or not (
            date.fromisoformat(med["start_date"]) <= day <= date.fromisoformat(med["end_date"])):
        abort(400)
    key = {"medicine_id": str(med["_id"]), "scheduled_date": day.isoformat(), "scheduled_time": at}
    dose_logs.update_one(key, {"$set": {"user_id": current_user.id, "status": status,
                                          "updated_at": datetime.utcnow().isoformat()}}, upsert=True)
    flash(f"Dose marked {status}.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.post("/medicines/<item_id>/toggle")
@login_required
def toggle_medicine(item_id):
    med = owned_or_404(medicine_collection, item_id)
    medicine_collection.update_one({"_id": med["_id"], "user_id": current_user.id},
                         {"$set": {"active": not med.get("active", True)}})
    flash("Medicine schedule updated.", "success")
    return redirect(url_for("medicines"))


@app.route("/appointments", methods=["GET", "POST"])
@login_required
def appointments():
    if request.method == "POST":
        try:
            appointment_at = datetime.strptime(request.form.get("appointment_at", ""), "%Y-%m-%dT%H:%M")
            appointments_collection.insert_one({"user_id": current_user.id,
                "appointment_at": appointment_at.isoformat(timespec="minutes"),
                "provider": request.form.get("provider", "").strip(),
                "notes": request.form.get("notes", "").strip()})
            flash("Appointment saved.", "success")
            return redirect(url_for("appointments"))
        except ValueError:
            flash("Choose a valid appointment date and time.", "danger")
    now = datetime.now().isoformat(timespec="minutes")
    upcoming = [mongo_object(a, datetime_fields=("appointment_at",)) for a in appointments_collection.find(
        {"user_id": current_user.id, "appointment_at": {"$gte": now}}).sort("appointment_at", ASCENDING)]
    past = [mongo_object(a, datetime_fields=("appointment_at",)) for a in appointments_collection.find(
        {"user_id": current_user.id, "appointment_at": {"$lt": now}}).sort("appointment_at", DESCENDING)]
    return render_template("appointments.html", upcoming=upcoming, past=past)


@app.post("/appointments/<item_id>/delete")
@login_required
def delete_appointment(item_id):
    item = owned_or_404(appointments_collection, item_id)
    appointments_collection.delete_one({"_id": item["_id"], "user_id": current_user.id})
    flash("Appointment deleted.", "success")
    return redirect(url_for("appointments"))


@app.route("/uploads/<filename>")
@login_required
def view_upload(filename):
    owned = current_user.profile.photo_filename == filename
    owned = owned or health_records.find_one({"user_id": current_user.id, "attachment_filename": filename}) is not None
    if not owned:
        abort(404)
    stored_file = uploaded_files.find_one({"user_id": current_user.id, "filename": filename})
    if not stored_file:
        abort(404)
    response = send_file(BytesIO(stored_file["content"]), mimetype=stored_file["content_type"],
                         download_name=filename, as_attachment=False)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/style.css")
def local_stylesheet():
    """Serve the same public stylesheet locally; Vercel serves public/style.css itself."""
    return send_from_directory(ROOT / "public", "style.css", mimetype="text/css")


@app.route("/favicon.ico")
def favicon():
    # A missing optional icon should not trigger a database operation.
    return "", 204


@app.route("/backup.zip")
@login_required
def backup():
    profile_data = profiles.find_one({"user_id": current_user.id}, {"_id": 0, "user_id": 0}) or {}
    record_docs = list(health_records.find({"user_id": current_user.id}, {"_id": 0, "user_id": 0}))
    medicine_docs = []
    for med in medicine_collection.find({"user_id": current_user.id}):
        logs = dose_logs.find({"medicine_id": str(med["_id"]), "user_id": current_user.id})
        medicine_docs.append({"name": med["name"], "dose": med["dose"], "instructions": med.get("instructions", ""),
            "start_date": med["start_date"], "end_date": med["end_date"],
            "reminder_times": med["reminder_times"], "active": med["active"],
            "dose_history": [{"date": log["scheduled_date"], "time": log["scheduled_time"],
                              "status": log["status"]} for log in logs]})
    appointment_docs = list(appointments_collection.find({"user_id": current_user.id},
                           {"_id": 0, "user_id": 0}))
    # JSON-safe fields for datetime values (MongoDB stores timestamps as ISO strings here).
    payload = {"account": {"email": current_user.email, "username": current_user.username},
        "profile": profile_data, "health_records": record_docs, "medicines": medicine_docs,
        "appointments": appointment_docs, "exported_at": datetime.utcnow().isoformat() + "Z"}
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        names = [current_user.profile.photo_filename] + [r.get("attachment_filename") for r in record_docs]
        for filename in names:
            if filename:
                stored_file = uploaded_files.find_one({"user_id": current_user.id, "filename": filename})
                if stored_file:
                    archive.writestr(f"attachments/{filename}", stored_file["content"])
    output.seek(0)
    return send_file(output, mimetype="application/zip", as_attachment=True,
                     download_name=f"health-records-{current_user.username}.zip")


@app.errorhandler(413)
def too_large(_error):
    return render_template("error.html", code=413, message="Upload is too large. Maximum file size is 8 MB."), 413


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Invalid request.")), 400


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="We couldn’t find that page or item."), 404


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
