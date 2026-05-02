import csv
import io
import json
import os
import re
import math
import secrets
import smtplib
import tempfile
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from functools import wraps
from email.message import EmailMessage

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-this-secret-key")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_BYTES", str(7 * 1024 * 1024)))

DATA_FILE_NAME = os.environ.get("DENTAL_CRM_DATA_FILE", "data.json")
DATA_FILE = (
    DATA_FILE_NAME
    if os.path.isabs(DATA_FILE_NAME)
    else os.path.join(app.root_path, DATA_FILE_NAME)
)

UPLOAD_SUBDIR = "uploads"
UPLOAD_DIR = os.path.join(app.root_path, "static", UPLOAD_SUBDIR)
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

USERS = {
    "admin": {
        "id": "1",
        "username": "admin",
        "password_hash": generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123")),
        "role": "admin",
        "name": "Dr. Sharma",
    },
    "staff": {
        "id": "2",
        "username": "staff",
        "password_hash": generate_password_hash(os.environ.get("STAFF_PASSWORD", "staff123")),
        "role": "staff",
        "name": "Priya (Staff)",
    },
}

TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "5"))
RESET_TOKEN_TTL_MINUTES = int(os.environ.get("RESET_TOKEN_TTL_MINUTES", "30"))
UPGRADE_ACCESS_CODE = os.environ.get("UPGRADE_ACCESS_CODE", "").strip()

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()

PATIENT_STAGES = [
    "New Inquiry",
    "Qualified Lead",
    "Appointment Booked",
    "Visited Clinic",
    "Treatment Proposed",
    "Treatment Ongoing",
    "Follow-up Due",
    "Recall Scheduled",
    "Won Patient",
    "Lost Lead",
    "Missed Appointment",
]

APPOINTMENT_STATUSES = ["Scheduled", "Completed", "Missed", "Cancelled", "Rescheduled"]
TASK_STATUSES = ["Open", "In Progress", "Done"]
TASK_PRIORITIES = ["Low", "Normal", "High", "Urgent"]
PATIENT_PRIORITIES = ["Cold", "Warm", "Hot", "VIP"]
PATIENT_SOURCES = ["Walk-in", "Website", "Instagram", "Google Ads", "Referral", "WhatsApp", "Phone", "Other"]
COMMUNICATION_PREFS = ["WhatsApp", "Phone", "SMS", "Email", "No marketing"]
TREATMENT_STATUSES = ["Proposed", "Accepted", "In Progress", "Completed", "Declined"]
PAYMENT_STATUSES = ["Not quoted", "Quoted", "Partially paid", "Paid", "Overdue"]

DEFAULT_PROMOTIONS = [
    {
        "title": "Free Dental Checkup",
        "description": "Intro offer to bring new patients into the clinic.",
        "message": "Hello {name},\n\nWe are offering a free dental checkup this week. Reply to confirm a convenient slot.\n\n- {clinic}",
        "cta": "Reply to book a slot",
    },
    {
        "title": "Scaling & Polishing Reminder",
        "description": "Recall offer for hygiene and prevention.",
        "message": "Hello {name},\n\nIt has been a while since your last cleaning. We can schedule a scaling & polishing visit.\n\n- {clinic}",
        "cta": "Book hygiene visit",
    },
    {
        "title": "Implant Consultation",
        "description": "High-value consult offer for implant leads.",
        "message": "Hello {name},\n\nWe can schedule an implant consultation and explain options, timeline, and estimated cost.\n\n- {clinic}",
        "cta": "Schedule consultation",
    },
]

DEFAULT_AFTERCARE_TEMPLATES = [
    {
        "procedure": "Tooth Extraction (Removal)",
        "subject": "Aftercare - Tooth Extraction",
        "title": "Extraction aftercare",
        "body": (
            "Hello {name},\n\n"
            "After your tooth extraction, please follow these instructions:\n"
            "1) Bite firmly on the gauze for 30-45 minutes (change if needed).\n"
            "2) Do not rinse/spit for the first 24 hours.\n"
            "3) Avoid smoking/alcohol for 48-72 hours.\n"
            "4) Soft, cool foods today. Avoid hot and spicy foods.\n"
            "5) If bleeding continues: place fresh gauze and apply pressure.\n\n"
            "Pain and swelling:\n"
            "- Use ice pack outside the cheek (10 min on / 10 min off) for the first 6-8 hours.\n"
            "- Take medicines as prescribed.\n\n"
            "Start gentle warm salt-water rinses after 24 hours (3-4 times/day).\n\n"
            "Call us immediately if you have uncontrolled bleeding, fever, or increasing swelling.\n\n"
            "- {clinic}"
        ),
        "default_channels": ["WhatsApp", "Email"],
    },
    {
        "procedure": "Root Canal Treatment (RCT)",
        "subject": "Aftercare - Root Canal",
        "title": "RCT aftercare",
        "body": (
            "Hello {name},\n\n"
            "After your root canal visit:\n"
            "1) Mild pain/sensitivity is normal for 1-3 days.\n"
            "2) Avoid chewing on the treated tooth until final crown/filling is done.\n"
            "3) Take medicines as prescribed.\n"
            "4) Maintain normal brushing and flossing.\n\n"
            "If you notice swelling, severe pain, or the temporary filling breaks, contact us.\n\n"
            "- {clinic}"
        ),
        "default_channels": ["WhatsApp", "Email"],
    },
    {
        "procedure": "Scaling & Polishing",
        "subject": "Aftercare - Scaling",
        "title": "Scaling aftercare",
        "body": (
            "Hello {name},\n\n"
            "After scaling/polishing:\n"
            "1) Mild sensitivity for 24-48 hours is common.\n"
            "2) Avoid very hot/cold foods if sensitive.\n"
            "3) If gums feel sore, warm salt-water rinses can help.\n"
            "4) Brush gently and floss daily.\n\n"
            "If bleeding persists beyond 24 hours, contact us.\n\n"
            "- {clinic}"
        ),
        "default_channels": ["WhatsApp", "Email"],
    },
    {
        "procedure": "Dental Filling",
        "subject": "Aftercare - Filling",
        "title": "Filling aftercare",
        "body": (
            "Hello {name},\n\n"
            "After your filling:\n"
            "1) Avoid eating until numbness wears off.\n"
            "2) Mild sensitivity can last a few days.\n"
            "3) If your bite feels high or painful while chewing, contact us for adjustment.\n\n"
            "- {clinic}"
        ),
        "default_channels": ["WhatsApp", "Email"],
    },
]


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def human_timestamp():
    return datetime.now().strftime("%d %b %Y, %I:%M %p")


def safe_strip(value):
    return (value or "").strip()


def normalize_email(value):
    return safe_strip(value).lower()


def parse_iso_datetime(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def trial_ends_at_from_now():
    return (datetime.now() + timedelta(days=TRIAL_DAYS)).replace(microsecond=0).isoformat()


def user_is_entitled(user):
    if not user:
        return False
    if user.get("is_paid"):
        return True
    trial_ends_at = parse_iso_datetime(user.get("trial_ends_at"))
    if trial_ends_at and datetime.now() <= trial_ends_at:
        return True
    return False


def subscription_status(user):
    if not user:
        return {"status": "anonymous"}
    if user.get("is_paid"):
        return {"status": "paid"}
    trial_ends_at = parse_iso_datetime(user.get("trial_ends_at"))
    if not trial_ends_at:
        return {"status": "expired"}
    if datetime.now() > trial_ends_at:
        return {"status": "expired", "trial_ended_at": trial_ends_at.isoformat()}
    remaining = trial_ends_at - datetime.now()
    days_left = max(0, int(math.ceil(remaining.total_seconds() / 86400)))
    return {
        "status": "trial",
        "trial_ends_at": trial_ends_at.isoformat(),
        "days_left": days_left,
    }


def subscription_exempt_endpoint(endpoint):
    return endpoint in {
        "login",
        "signup",
        "forgot_password",
        "reset_password",
        "upgrade",
        "plans",
        "checkout",
        "logout",
        "static",
    }


def build_absolute_url(path):
    base = (request.url_root or "").rstrip("/")
    if not base:
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def smtp_settings():
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        port = 587
    return {
        "host": host,
        "port": port,
        "username": os.environ.get("SMTP_USERNAME", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "from_email": os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USERNAME", "").strip(),
        "use_tls": os.environ.get("SMTP_TLS", "1").strip() not in {"0", "false", "no"},
    }


def send_email(to_email, subject, body):
    settings = smtp_settings()
    if not settings:
        return False, "SMTP not configured."
    if not settings.get("from_email"):
        return False, "SMTP_FROM/SMTP_USERNAME not configured."

    message = EmailMessage()
    message["From"] = settings["from_email"]
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=12) as server:
            if settings["use_tls"]:
                server.starttls()
            if settings["username"] and settings["password"]:
                server.login(settings["username"], settings["password"])
            server.send_message(message)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def plan_catalog():
    # Default INR pricing tuned for Indian clinics; override anytime via env vars if needed.
    currency_symbol = "₹"
    try:
        monthly = int(os.environ.get("PLAN_MONTHLY_INR", "1999"))
        quarterly = int(os.environ.get("PLAN_QUARTERLY_INR", "4999"))
        yearly = int(os.environ.get("PLAN_YEARLY_INR", "17999"))
    except ValueError:
        monthly, quarterly, yearly = 1999, 4999, 17999

    return {
        "currency_symbol": currency_symbol,
        "plans": [
            {
                "id": "monthly",
                "name": "Monthly",
                "label": "Flexible",
                "price": monthly,
                "period": "per month",
                "cta": "Pay monthly",
                "amount_paise": monthly * 100,
                "highlight": False,
            },
            {
                "id": "quarterly",
                "name": "Quarterly",
                "label": "Best for cashflow",
                "price": quarterly,
                "period": "every 3 months",
                "cta": "Pay quarterly",
                "amount_paise": quarterly * 100,
                "highlight": True,
                "badge": "Most popular",
            },
            {
                "id": "yearly",
                "name": "Yearly",
                "label": "Best value",
                "price": yearly,
                "period": "per year",
                "cta": "Pay yearly",
                "amount_paise": yearly * 100,
                "highlight": False,
                "badge": "Best price",
            },
        ],
        "features": [
            "Patients + pipeline",
            "Appointments + follow-ups",
            "Tasks + team workflow",
            "Broadcast offers + aftercare",
            "Audit trail + insights",
        ],
    }


def normalize_phone(value):
    raw = safe_strip(value)
    return re.sub(r"[^\d+]", "", raw)


def to_money(value):
    try:
        return float(str(value or 0).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def checkbox_value(form, key, default=False):
    if key not in form:
        return default
    return safe_strip(form.get(key)).lower() in {"1", "true", "yes", "on"}


def parse_tags(value):
    return [tag.strip() for tag in safe_strip(value).split(",") if tag.strip()]


def render_message_template(text, patient, data):
    clinic = (data.get("settings") or {}).get("clinic_name") or "DentaCare"
    name = (patient or {}).get("name") or "there"
    phone = (patient or {}).get("phone") or ""
    return (
        (text or "")
        .replace("{clinic}", clinic)
        .replace("{name}", name)
        .replace("{phone}", phone)
    )


def whatsapp_link(phone, text):
    normalized = normalize_phone(phone).replace("+", "")
    return f"https://wa.me/{normalized}?text={text}"


@app.template_filter("inr")
def format_inr(value):
    return f"Rs {to_money(value):,.0f}"


@app.template_filter("date_label")
def format_date_label(value):
    if not value:
        return "Not scheduled"
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(value)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_error):
    flash("Upload too large. Please use an image under 7 MB (or reduce MAX_UPLOAD_BYTES).", "error")
    return redirect(request.referrer or url_for("dashboard"))


def ensure_uploads_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_uploaded_image(file_storage):
    if not file_storage or not getattr(file_storage, "filename", ""):
        return ""

    filename = secure_filename(file_storage.filename or "")
    _, ext = os.path.splitext(filename.lower())
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Unsupported image type. Please upload JPG, PNG, or WEBP.")

    ensure_uploads_dir()
    final_name = f"{uuid.uuid4().hex[:10]}{ext}"
    abs_path = os.path.join(UPLOAD_DIR, final_name)
    file_storage.save(abs_path)
    return f"{UPLOAD_SUBDIR}/{final_name}"


def empty_data():
    return {
        "patients": [],
        "appointments": [],
        "offers": [],
        "promotions": [],
        "aftercare_templates": [],
        "aftercare_sends": [],
        "tasks": [],
        "activities": [],
        "accounts": [],
        "settings": {
            "clinic_name": "DentaCare",
            "currency": "INR",
            "default_owner": "Priya (Staff)",
        },
    }


def migrate_data(data):
    baseline = empty_data()
    list_keys = [
        "patients",
        "appointments",
        "offers",
        "promotions",
        "aftercare_templates",
        "aftercare_sends",
        "tasks",
        "activities",
        "accounts",
    ]
    for key in list_keys:
        if key in data and not isinstance(data.get(key), list):
            data[key] = []

    for key, value in baseline.items():
        data.setdefault(key, value if not isinstance(value, list) else [])

    if "settings" in data and not isinstance(data.get("settings"), dict):
        data["settings"] = {}
    settings = data.setdefault("settings", {})
    for key, value in baseline["settings"].items():
        settings.setdefault(key, value)

    for patient in data["patients"]:
        ensure_patient_defaults(patient)

    for appointment in data["appointments"]:
        appointment.setdefault("provider", "Dr. Sharma")
        appointment.setdefault("room", "Chair 1")
        appointment.setdefault("channel", "Front desk")
        appointment.setdefault("status", "Scheduled")
        appointment.setdefault("created_at", now_iso())

    for offer in data["offers"]:
        offer.setdefault("channel", "WhatsApp")
        offer.setdefault("segment", "Manual selection")
        offer.setdefault("status", "Preview Ready")
        offer.setdefault("image_path", "")
        offer.setdefault("promotion_id", "")
        offer.setdefault("sent", {})
        if not isinstance(offer.get("sent"), dict):
            offer["sent"] = {}

    if not data.get("promotions"):
        data["promotions"] = []
        for promo in DEFAULT_PROMOTIONS:
            data["promotions"].append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "title": promo["title"],
                    "description": promo.get("description", ""),
                    "message": promo.get("message", ""),
                    "cta": promo.get("cta", ""),
                    "image_path": "",
                    "active": True,
                    "created_at": now_iso(),
                    "created_by": "System",
                }
            )

    for promo in data.get("promotions", []):
        promo.setdefault("id", str(uuid.uuid4())[:8])
        promo.setdefault("title", "Untitled offer")
        promo.setdefault("description", "")
        promo.setdefault("message", "")
        promo.setdefault("cta", "")
        promo.setdefault("image_path", "")
        promo.setdefault("active", True)
        promo.setdefault("created_at", now_iso())
        promo.setdefault("created_by", "System")

    if not data.get("aftercare_templates"):
        data["aftercare_templates"] = []
        for template in DEFAULT_AFTERCARE_TEMPLATES:
            data["aftercare_templates"].append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "procedure": template["procedure"],
                    "title": template["title"],
                    "subject": template["subject"],
                    "body": template["body"],
                    "image_path": "",
                    "active": True,
                    "default_channels": template.get("default_channels", ["WhatsApp"]),
                    "created_at": now_iso(),
                    "created_by": "System",
                }
            )

    for template in data.get("aftercare_templates", []):
        template.setdefault("id", str(uuid.uuid4())[:8])
        template.setdefault("procedure", "Procedure")
        template.setdefault("title", "Aftercare")
        template.setdefault("subject", "Aftercare")
        template.setdefault("body", "")
        template.setdefault("image_path", "")
        template.setdefault("active", True)
        template.setdefault("default_channels", ["WhatsApp"])
        template.setdefault("created_at", now_iso())
        template.setdefault("created_by", "System")
        if not isinstance(template.get("default_channels"), list):
            template["default_channels"] = ["WhatsApp"]

    for send in data.get("aftercare_sends", []):
        send.setdefault("id", str(uuid.uuid4())[:8])
        send.setdefault("template_id", "")
        send.setdefault("title", "Aftercare")
        send.setdefault("subject", "Aftercare")
        send.setdefault("body", "")
        send.setdefault("channels", ["WhatsApp"])
        send.setdefault("patient_ids", [])
        send.setdefault("image_path", "")
        send.setdefault("created_at", now_iso())
        send.setdefault("created_by", "System")
        send.setdefault("status", "Preview Ready")
        send.setdefault("sent", {})
        if not isinstance(send.get("sent"), dict):
            send["sent"] = {}
        if not isinstance(send.get("patient_ids"), list):
            send["patient_ids"] = []
        if not isinstance(send.get("channels"), list):
            send["channels"] = ["WhatsApp"]

    for task in data["tasks"]:
        task.setdefault("id", str(uuid.uuid4())[:8])
        task.setdefault("title", "Untitled task")
        task.setdefault("status", "Open")
        task.setdefault("priority", "Normal")
        task.setdefault("owner", settings.get("default_owner", "Unassigned"))
        task.setdefault("type", "Follow-up")
        task.setdefault("created_at", now_iso())
        task.setdefault("created_by", "System")

    for activity in data["activities"]:
        activity.setdefault("id", str(uuid.uuid4())[:8])
        activity.setdefault("created_at", now_iso())
        activity.setdefault("actor", "System")
        activity.setdefault("action", "Activity")

    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        data["accounts"] = []
        accounts = data["accounts"]

    for account in accounts:
        if not isinstance(account, dict):
            continue
        account.setdefault("id", str(uuid.uuid4()))
        account.setdefault("email", "")
        account["email"] = normalize_email(account.get("email"))
        account.setdefault("name", "")
        account.setdefault("role", "admin")
        account.setdefault("password_hash", "")
        account.setdefault("created_at", now_iso())
        account.setdefault("trial_starts_at", now_iso())
        account.setdefault("trial_ends_at", trial_ends_at_from_now())
        account.setdefault("is_paid", False)
        account.setdefault("last_login_at", "")
        account.setdefault("reset_token_hash", "")
        account.setdefault("reset_token_expires_at", "")

    return data


def get_user_data_file():
        email = (current_user() or {}).get("email", "")
        if not email:
                    return DATA_FILE
                import hashlib
    email_hash = hashlib.md5(email.lower().encode()).hexdigest()[:12]
    fname = f"data_{email_hash}.json"
    return os.path.join(app.root_path, fname)
def load_data():
    if not os.path.exists(get_user_data_file()):
        data = empty_data()
        save_data(data)
        return data

    try:
        with open(get_user_data_file(), "r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        data = empty_data()

    needs_save = False
    expected_list_keys = [
        "patients",
        "appointments",
        "offers",
        "promotions",
        "aftercare_templates",
        "aftercare_sends",
        "tasks",
        "activities",
        "accounts",
    ]

    for key in expected_list_keys:
        if key not in data or not isinstance(data.get(key), list):
            needs_save = True

    if "settings" not in data or not isinstance(data.get("settings"), dict):
        needs_save = True

    # Persist default libraries so their IDs stay stable across requests.
    if not data.get("promotions"):
        needs_save = True
    if not data.get("aftercare_templates"):
        needs_save = True

    offers_raw = data.get("offers")
    if isinstance(offers_raw, list):
        for offer in offers_raw:
            if not isinstance(offer, dict):
                needs_save = True
                continue
            if any(field not in offer for field in ("sent", "image_path", "promotion_id", "channel", "segment")):
                needs_save = True
            if "sent" in offer and not isinstance(offer.get("sent"), dict):
                needs_save = True
    else:
        needs_save = True

    data = migrate_data(data)
    if needs_save:
        save_data(data)
    return data


def save_data(data):
    os.makedirs(os.path.dirname(get_user_data_file()), exist_ok=True)
    migrate_data(data)

    fd, temp_path = tempfile.mkstemp(prefix=".data-", suffix=".json", dir=os.path.dirname(get_user_data_file()))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temp_path, get_user_data_file())
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def current_user():
    return session.get("user")


def find_account_by_login(data, login_value):
    login_value = safe_strip(login_value).lower()
    if not login_value:
        return None
    for account in data.get("accounts", []):
        if not isinstance(account, dict):
            continue
        if normalize_email(account.get("email")) == login_value:
            return account
    return None


def session_user_for_account(account):
    return {
        "id": account.get("id", ""),
        "name": account.get("name") or (account.get("email") or "User"),
        "role": account.get("role", "admin"),
        "username": normalize_email(account.get("email")),
        "email": normalize_email(account.get("email")),
        "trial_ends_at": account.get("trial_ends_at", ""),
        "is_paid": bool(account.get("is_paid", False)),
    }


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        endpoint = request.endpoint or ""
        if endpoint and not subscription_exempt_endpoint(endpoint) and not user_is_entitled(current_user()):
            return redirect(url_for("upgrade"))
        return view_func(*args, **kwargs)

    return wrapped_view


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if user.get("role") not in roles:
                flash("You do not have permission to perform that action.", "error")
                return redirect(url_for("dashboard"))
            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


@app.context_processor
def inject_global_template_context():
    endpoint = request.endpoint or ""
    active = "dashboard"

    if endpoint in {"crm_command_center"}:
        active = "crm"
    elif endpoint.startswith("patient") or endpoint in {"patients", "add_patient", "edit_patient"}:
        active = "patients"
    elif endpoint.startswith("appointment") or endpoint in {"appointments", "add_appointment"}:
        active = "appointments"
    elif endpoint.startswith("task") or endpoint == "tasks":
        active = "tasks"
    elif endpoint == "followups":
        active = "followups"
    elif endpoint == "broadcast":
        active = "broadcast"
    elif endpoint.startswith("offers") or endpoint == "offers":
        active = "offers"
    elif endpoint.startswith("aftercare") or endpoint == "aftercare":
        active = "aftercare"
    elif endpoint == "audit":
        active = "audit"

    return {
        "active": active,
        "user": current_user(),
        "subscription": subscription_status(current_user()),
        "request_base_url": (request.url_root or "").rstrip("/"),
        "patient_stages": PATIENT_STAGES,
        "patient_priorities": PATIENT_PRIORITIES,
        "patient_sources": PATIENT_SOURCES,
        "communication_prefs": COMMUNICATION_PREFS,
        "task_priorities": TASK_PRIORITIES,
        "payment_statuses": PAYMENT_STATUSES,
        "treatment_statuses": TREATMENT_STATUSES,
    }


def find_patient(data, pid):
    return next((patient for patient in data["patients"] if patient.get("id") == pid), None)


def find_appointment(data, aid):
    return next((appt for appt in data["appointments"] if appt.get("id") == aid), None)


def find_task(data, tid):
    return next((task for task in data["tasks"] if task.get("id") == tid), None)


def find_promotion(data, promo_id):
    return next((promo for promo in data.get("promotions", []) if promo.get("id") == promo_id), None)


def find_aftercare_template(data, template_id):
    return next(
        (template for template in data.get("aftercare_templates", []) if template.get("id") == template_id),
        None,
    )


def find_aftercare_send(data, send_id):
    return next((send for send in data.get("aftercare_sends", []) if send.get("id") == send_id), None)


def ensure_patient_defaults(patient):
    patient.setdefault("id", str(uuid.uuid4())[:8])
    patient.setdefault("name", "")
    patient.setdefault("phone", "")
    patient.setdefault("email", "")
    patient.setdefault("age", "")
    patient.setdefault("gender", "")
    patient.setdefault("treatment", "")
    patient.setdefault("notes", "")
    patient.setdefault("stage", "New Inquiry")
    patient.setdefault("followup_date", "")
    patient.setdefault("created_at", now_iso())
    patient.setdefault("history", [])
    patient.setdefault("source", "Walk-in")
    patient.setdefault("owner", "Priya (Staff)")
    patient.setdefault("priority", "Warm")
    patient.setdefault("communication_preference", "WhatsApp")
    patient.setdefault("next_action", "")
    patient.setdefault("estimated_value", "0")
    patient.setdefault("amount_collected", "0")
    patient.setdefault("payment_status", "Not quoted")
    patient.setdefault("last_contacted_at", "")
    patient.setdefault("tags", [])
    patient.setdefault("risk_flags", [])
    patient.setdefault("treatment_plans", [])
    consent = patient.setdefault("consent", {})
    consent.setdefault("whatsapp", True)
    consent.setdefault("sms", False)
    consent.setdefault("email", False)
    consent.setdefault("marketing", True)
    return patient


def add_history(patient, note, by=None):
    ensure_patient_defaults(patient)
    patient["history"].append(
        {
            "note": note,
            "date": human_timestamp(),
            "by": by or (current_user() or {}).get("name", "System"),
        }
    )


def record_activity(data, action, subject, detail="", patient_id=None):
    actor = (current_user() or {}).get("name", "System")
    data.setdefault("activities", []).append(
        {
            "id": str(uuid.uuid4())[:8],
            "created_at": now_iso(),
            "actor": actor,
            "action": action,
            "subject": subject,
            "detail": detail,
            "patient_id": patient_id,
        }
    )
    data["activities"] = data["activities"][-250:]


def build_patient_payload(form, existing=None):
    payload = existing if existing is not None else {}

    payload["name"] = safe_strip(form.get("name"))
    payload["phone"] = normalize_phone(form.get("phone"))
    payload["email"] = safe_strip(form.get("email"))
    payload["age"] = safe_strip(form.get("age"))
    payload["gender"] = safe_strip(form.get("gender"))
    payload["treatment"] = safe_strip(form.get("treatment"))
    payload["notes"] = safe_strip(form.get("notes"))
    payload["source"] = safe_strip(form.get("source")) or "Walk-in"
    payload["owner"] = safe_strip(form.get("owner")) or "Priya (Staff)"
    payload["priority"] = safe_strip(form.get("priority")) or "Warm"
    payload["communication_preference"] = safe_strip(form.get("communication_preference")) or "WhatsApp"
    payload["next_action"] = safe_strip(form.get("next_action"))
    payload["estimated_value"] = safe_strip(form.get("estimated_value")) or "0"
    payload["amount_collected"] = safe_strip(form.get("amount_collected")) or "0"
    payload["payment_status"] = safe_strip(form.get("payment_status")) or "Not quoted"
    payload["tags"] = parse_tags(form.get("tags"))

    stage = safe_strip(form.get("stage")) or "New Inquiry"
    payload["stage"] = stage if stage in PATIENT_STAGES else "New Inquiry"

    if payload["priority"] not in PATIENT_PRIORITIES:
        payload["priority"] = "Warm"
    if payload["source"] not in PATIENT_SOURCES:
        payload["source"] = "Other"
    if payload["communication_preference"] not in COMMUNICATION_PREFS:
        payload["communication_preference"] = "WhatsApp"
    if payload["payment_status"] not in PAYMENT_STATUSES:
        payload["payment_status"] = "Not quoted"

    payload["followup_date"] = safe_strip(form.get("followup_date"))
    payload["consent"] = {
        "whatsapp": checkbox_value(form, "consent_whatsapp", True),
        "sms": checkbox_value(form, "consent_sms", False),
        "email": checkbox_value(form, "consent_email", False),
        "marketing": checkbox_value(form, "consent_marketing", True),
    }

    return ensure_patient_defaults(payload)


def validate_patient_payload(payload):
    errors = []

    if not payload.get("name"):
        errors.append("Patient name is required.")

    if not payload.get("phone"):
        errors.append("Phone number is required.")

    age_value = payload.get("age")
    if age_value and not age_value.isdigit():
        errors.append("Age must be a valid number.")

    email_value = payload.get("email")
    if email_value and "@" not in email_value:
        errors.append("Email address is invalid.")

    for money_field in ("estimated_value", "amount_collected"):
        value = payload.get(money_field)
        if value and to_money(value) < 0:
            errors.append("Financial amounts cannot be negative.")

    followup_date = payload.get("followup_date")
    if followup_date:
        try:
            date.fromisoformat(followup_date)
        except ValueError:
            errors.append("Follow-up date is invalid.")

    return errors


def build_appointment_view(data, appointment):
    patient = find_patient(data, appointment.get("patient_id"))
    item = dict(appointment)
    item["patient_name"] = patient["name"] if patient else "Unknown"
    item["patient_phone"] = patient["phone"] if patient else ""
    item["patient_stage"] = patient.get("stage", "") if patient else ""
    return item


def stage_counts(data):
    counts = Counter(patient.get("stage", "New Inquiry") for patient in data["patients"])
    values = Counter()
    for patient in data["patients"]:
        values[patient.get("stage", "New Inquiry")] += to_money(patient.get("estimated_value"))
    return [
        {"stage": stage, "count": counts.get(stage, 0), "value": values.get(stage, 0)}
        for stage in PATIENT_STAGES
        if counts.get(stage, 0) or stage in {"New Inquiry", "Appointment Booked", "Treatment Ongoing"}
    ]


def patient_appointments(data, patient_id):
    return [appt for appt in data["appointments"] if appt.get("patient_id") == patient_id]


def score_patient(patient, appointments=None):
    ensure_patient_defaults(patient)
    stage_weight = {
        "New Inquiry": 20,
        "Qualified Lead": 38,
        "Appointment Booked": 52,
        "Visited Clinic": 64,
        "Treatment Proposed": 72,
        "Treatment Ongoing": 82,
        "Follow-up Due": 60,
        "Recall Scheduled": 58,
        "Won Patient": 92,
        "Lost Lead": 8,
        "Missed Appointment": 34,
    }
    priority_weight = {"Cold": 0, "Warm": 6, "Hot": 14, "VIP": 20}
    score = stage_weight.get(patient.get("stage"), 25)
    score += priority_weight.get(patient.get("priority"), 6)
    score += min(to_money(patient.get("estimated_value")) / 10000, 18)

    if patient.get("email"):
        score += 3
    if patient.get("phone"):
        score += 3
    if patient.get("followup_date"):
        try:
            followup = date.fromisoformat(patient["followup_date"])
            if followup <= date.today():
                score += 10
            elif followup <= date.today() + timedelta(days=7):
                score += 5
        except ValueError:
            pass
    if appointments and any(appt.get("status") == "Scheduled" for appt in appointments):
        score += 5
    return int(min(max(score, 0), 100))


def patient_financials(patient):
    estimated = to_money(patient.get("estimated_value"))
    collected = to_money(patient.get("amount_collected"))
    outstanding = max(estimated - collected, 0)
    paid_percent = int((collected / estimated) * 100) if estimated else 0
    return {
        "estimated": estimated,
        "collected": collected,
        "outstanding": outstanding,
        "paid_percent": min(paid_percent, 100),
    }


def compute_crm_insights(data):
    today = date.today()
    patients = data["patients"]
    appointments = data["appointments"]
    tasks = data.get("tasks", [])

    completed = [appt for appt in appointments if appt.get("status") == "Completed"]
    missed = [appt for appt in appointments if appt.get("status") == "Missed"]
    scheduled = [appt for appt in appointments if appt.get("status") == "Scheduled"]
    today_appts = [appt for appt in scheduled if appt.get("date") == today.isoformat()]

    active_patients = [
        patient
        for patient in patients
        if patient.get("stage") not in {"Lost Lead", "Won Patient"}
    ]

    open_tasks = [task for task in tasks if task.get("status") != "Done"]
    overdue_tasks = []
    due_today_tasks = []
    for task in open_tasks:
        due_date = task.get("due_date")
        if not due_date:
            continue
        try:
            due = date.fromisoformat(due_date)
        except ValueError:
            continue
        if due < today:
            overdue_tasks.append(task)
        elif due == today:
            due_today_tasks.append(task)

    total_estimated = sum(to_money(patient.get("estimated_value")) for patient in patients)
    total_collected = sum(to_money(patient.get("amount_collected")) for patient in patients)
    total_outstanding = max(total_estimated - total_collected, 0)

    engaged = [
        patient
        for patient in patients
        if patient.get("stage") in {"Visited Clinic", "Treatment Proposed", "Treatment Ongoing", "Won Patient"}
    ]
    no_show_rate = int((len(missed) / max(len(missed) + len(completed), 1)) * 100)
    conversion_rate = int((len(engaged) / max(len(patients), 1)) * 100)

    source_counts = Counter(patient.get("source", "Other") for patient in patients)
    owner_counts = Counter(patient.get("owner", "Unassigned") for patient in patients)

    patient_scores = []
    for patient in patients:
        score = score_patient(patient, patient_appointments(data, patient["id"]))
        patient["lead_score"] = score
        patient_scores.append(patient)

    priority_patients = sorted(
        patient_scores,
        key=lambda item: (item.get("lead_score", 0), to_money(item.get("estimated_value"))),
        reverse=True,
    )[:8]

    return {
        "total_patients": len(patients),
        "active_patients": len(active_patients),
        "today_appointments": len(today_appts),
        "scheduled_appointments": len(scheduled),
        "pending_followups": len([p for p in patients if p.get("stage") == "Follow-up Due"]),
        "missed_appointments": len([p for p in patients if p.get("stage") == "Missed Appointment"]),
        "open_tasks": len(open_tasks),
        "overdue_tasks": len(overdue_tasks),
        "due_today_tasks": len(due_today_tasks),
        "total_estimated": total_estimated,
        "total_collected": total_collected,
        "total_outstanding": total_outstanding,
        "conversion_rate": conversion_rate,
        "no_show_rate": no_show_rate,
        "stage_counts": stage_counts(data),
        "source_counts": source_counts.most_common(6),
        "owner_counts": owner_counts.most_common(6),
        "priority_patients": priority_patients,
        "recent_activities": sorted(
            data.get("activities", []),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )[:8],
    }


def filter_patients(data):
    q = safe_strip(request.args.get("q")).lower()
    stage = safe_strip(request.args.get("stage"))
    owner = safe_strip(request.args.get("owner"))
    priority = safe_strip(request.args.get("priority"))
    source = safe_strip(request.args.get("source"))

    patients = []
    for patient in data["patients"]:
        ensure_patient_defaults(patient)
        searchable = " ".join(
            [
                patient.get("name", ""),
                patient.get("phone", ""),
                patient.get("email", ""),
                patient.get("treatment", ""),
                patient.get("notes", ""),
                patient.get("source", ""),
                patient.get("owner", ""),
                " ".join(patient.get("tags", [])),
            ]
        ).lower()

        if q and q not in searchable:
            continue
        if stage and patient.get("stage") != stage:
            continue
        if owner and patient.get("owner") != owner:
            continue
        if priority and patient.get("priority") != priority:
            continue
        if source and patient.get("source") != source:
            continue
        patients.append(patient)

    for patient in patients:
        patient["lead_score"] = score_patient(patient, patient_appointments(data, patient["id"]))

    patients.sort(
        key=lambda patient: (
            patient.get("lead_score", 0),
            patient.get("created_at", ""),
        ),
        reverse=True,
    )

    return patients, {
        "q": q,
        "stage": stage,
        "owner": owner,
        "priority": priority,
        "source": source,
    }


def create_task(data, title, patient_id="", due_date="", priority="Normal", owner="", task_type="Follow-up"):
    task = {
        "id": str(uuid.uuid4())[:8],
        "patient_id": patient_id,
        "title": safe_strip(title),
        "due_date": due_date,
        "priority": priority if priority in TASK_PRIORITIES else "Normal",
        "owner": safe_strip(owner) or (current_user() or {}).get("name", "Unassigned"),
        "type": safe_strip(task_type) or "Follow-up",
        "status": "Open",
        "created_at": now_iso(),
        "created_by": (current_user() or {}).get("name", "System"),
        "completed_at": "",
    }
    data.setdefault("tasks", []).append(task)
    patient = find_patient(data, patient_id) if patient_id else None
    subject = patient.get("name") if patient else "General task"
    record_activity(data, "Task created", subject, task["title"], patient_id or None)
    return task


@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        if user_is_entitled(current_user()):
            return redirect(url_for("dashboard"))
        return redirect(url_for("upgrade"))

    data = load_data()
    errors = []
    form_data = {"name": "", "email": ""}

    if request.method == "POST":
        name = safe_strip(request.form.get("name"))
        email = normalize_email(request.form.get("email"))
        password = safe_strip(request.form.get("password"))
        confirm_password = safe_strip(request.form.get("confirm_password"))
        form_data = {"name": name, "email": email}

        if not name:
            errors.append("Your name is required.")
        if not email or "@" not in email or "." not in email.split("@")[-1]:
            errors.append("A valid email is required.")
        if find_account_by_login(data, email):
            errors.append("An account with this email already exists. Try signing in instead.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        if not errors:
            account = {
                "id": str(uuid.uuid4()),
                "email": email,
                "name": name,
                "role": "admin",
                "password_hash": generate_password_hash(password),
                "created_at": now_iso(),
                "trial_starts_at": now_iso(),
                "trial_ends_at": trial_ends_at_from_now(),
                "is_paid": False,
                "last_login_at": now_iso(),
                "reset_token_hash": "",
                "reset_token_expires_at": "",
            }
            data.setdefault("accounts", []).append(account)
            save_data(data)

            session.permanent = True
            session["user"] = session_user_for_account(account)
            flash(
                f"Trial activated — {TRIAL_DAYS} days free. Upgrade anytime to keep access.",
                "success",
            )
            return redirect(url_for("dashboard"))

    return render_template("signup.html", errors=errors, form_data=form_data, trial_days=TRIAL_DAYS)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user() and user_is_entitled(current_user()):
        return redirect(url_for("dashboard"))

    data = load_data()
    errors = []
    reset_url = ""
    reset_url_full = ""
    email_value = ""

    if request.method == "POST":
        email_value = normalize_email(request.form.get("email"))
        if not email_value or "@" not in email_value:
            errors.append("Please enter the email you used to create the account.")
        else:
            account = find_account_by_login(data, email_value)
            if account and account.get("password_hash"):
                token = secrets.token_urlsafe(32)
                account["reset_token_hash"] = generate_password_hash(token)
                account["reset_token_expires_at"] = (
                    datetime.now() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
                ).replace(microsecond=0).isoformat()
                save_data(data)
                reset_url = url_for("reset_password", token=token)
                reset_url_full = build_absolute_url(reset_url)

                ok, err = send_email(
                    email_value,
                    "Reset your DentaCare CRM password",
                    (
                        "We received a request to reset your DentaCare CRM password.\n\n"
                        f"Reset link (valid for {RESET_TOKEN_TTL_MINUTES} minutes):\n"
                        f"{reset_url_full}\n\n"
                        "If you did not request this, you can ignore this email."
                    ),
                )
                if ok:
                    reset_url = ""
                    reset_url_full = ""
                else:
                    flash(
                        f"Email delivery not configured ({err}). Showing the reset link here for now.",
                        "warning",
                    )

            flash(
                "If an account exists for that email, a reset link is ready.",
                "info",
            )

    return render_template(
        "forgot_password.html",
        errors=errors,
        reset_url=reset_url,
        reset_url_full=reset_url_full,
        email=email_value,
        reset_minutes=RESET_TOKEN_TTL_MINUTES,
    )


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    data = load_data()
    account = None
    now = datetime.now()

    for candidate in data.get("accounts", []):
        if not isinstance(candidate, dict):
            continue
        expires_at = parse_iso_datetime(candidate.get("reset_token_expires_at"))
        token_hash = candidate.get("reset_token_hash")
        if not expires_at or not token_hash:
            continue
        if now > expires_at:
            continue
        if check_password_hash(token_hash, token):
            account = candidate
            break

    if not account:
        flash("That reset link is invalid or expired. Please try again.", "error")
        return redirect(url_for("forgot_password"))

    errors = []
    if request.method == "POST":
        password = safe_strip(request.form.get("password"))
        confirm_password = safe_strip(request.form.get("confirm_password"))

        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        if not errors:
            account["password_hash"] = generate_password_hash(password)
            account["reset_token_hash"] = ""
            account["reset_token_expires_at"] = ""
            save_data(data)
            flash("Password updated. Please sign in with your new password.", "success")
            return redirect(url_for("login"))

    return render_template(
        "reset_password.html",
        errors=errors,
        token=token,
        email=account.get("email", ""),
    )


@app.route("/upgrade", methods=["GET", "POST"])
@login_required
def upgrade():
    user = current_user()
    # Allow trial users to access the upgrade page (upgrade early),
    # but paid users don't need to see it.
    if user and user.get("is_paid"):
        return redirect(url_for("dashboard"))

    data = load_data()
    errors = []
    status = subscription_status(user)

    if request.method == "GET":
        return redirect(url_for("plans"))

    if request.method == "POST":
        access_code = safe_strip(request.form.get("access_code"))
        if not access_code:
            errors.append("Please enter your upgrade code.")
        elif UPGRADE_ACCESS_CODE and access_code != UPGRADE_ACCESS_CODE:
            errors.append("Invalid upgrade code.")
        else:
            account = find_account_by_login(data, (user or {}).get("email"))
            if not account:
                errors.append("Account not found. Please contact support.")
            else:
                account["is_paid"] = True
                save_data(data)
                session["user"] = session_user_for_account(account)
                if not UPGRADE_ACCESS_CODE:
                    flash(
                        "Upgraded in demo mode. Set UPGRADE_ACCESS_CODE to secure upgrades.",
                        "warning",
                    )
                flash("Upgrade complete — welcome to Pro.", "success")
                return redirect(url_for("dashboard"))

    return render_template(
        "upgrade.html",
        errors=errors,
        status=status,
        trial_days=TRIAL_DAYS,
        reset_minutes=RESET_TOKEN_TTL_MINUTES,
    )


@app.route("/plans")
@login_required
def plans():
    user = current_user()
    if user and user.get("is_paid"):
        return redirect(url_for("dashboard"))
    catalog = plan_catalog()
    return render_template(
        "plans.html",
        status=subscription_status(user),
        catalog=catalog,
    )


@app.route("/checkout/<plan_id>")
@login_required
def checkout(plan_id):
    user = current_user()
    if user and user.get("is_paid"):
        return redirect(url_for("dashboard"))

    plan_id = safe_strip(plan_id).lower()
    catalog = plan_catalog()
    plan = next((p for p in catalog.get("plans", []) if p.get("id") == plan_id), None)
    if not plan:
        flash("Invalid plan selected.", "error")
        return redirect(url_for("plans"))

    amount_paise = plan.get("amount_paise", 0)
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        flash(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET environment variables.",
            "error",
        )
        return redirect(url_for("plans"))

    # Attach a reference for downstream attribution (webhook/automation can use this).
    rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    order = rz_client.order.create({"amount": amount_paise, "currency": "INR", "receipt": plan_id, "notes": {"user_id": (user or {}).get("id", ""), "plan": plan_id}})
    return render_template("checkout.html", order=order, plan=plan, key_id=RAZORPAY_KEY_ID, user=user)
    # Razorpay payment handled by checkout.html template


@app.route("/payment/verify", methods=["POST"])
@login_required
def payment_verify():
        rz_payment_id = safe_strip(request.form.get("razorpay_payment_id"))
        rz_order_id = safe_strip(request.form.get("razorpay_order_id"))
        rz_signature = safe_strip(request.form.get("razorpay_signature"))
        plan_id = safe_strip(request.form.get("plan_id"))
        if not rz_payment_id or not rz_order_id or not rz_signature:
                    flash("Payment verification failed.", "error")
                    return redirect(url_for("plans"))
                try:
                            rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
                            rz_client.utility.verify_payment_signature({
                                            "razorpay_order_id": rz_order_id,
                                            "razorpay_payment_id": rz_payment_id,
                                            "razorpay_signature": rz_signature,
                                        })
                        except Exception:
                                    flash("Payment verification failed. Contact support.", "error")
                                    return redirect(url_for("plans"))
                                data = load_data()
    user = current_user()
    email = normalize_email((user or {}).get("email", ""))
    account = find_account_by_login(data, email)
    if account:
                account["is_paid"] = True
                account["paid_plan"] = plan_id
                account["paid_at"] = now_iso()
                save_data(data)
                session["user"] = session_user_for_account(account)
            flash("Payment successful! Your account has been upgraded.", "success")
    return redirect(url_for("dashboard"))
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        if user_is_entitled(current_user()):
            return redirect(url_for("dashboard"))
        return redirect(url_for("upgrade"))

    error = None
    username = ""

    if request.method == "POST":
        username = safe_strip(request.form.get("login") or request.form.get("username")).lower()
        password = safe_strip(request.form.get("password"))

        user = USERS.get(username)

        if user and check_password_hash(user["password_hash"], password):
            session.permanent = True
            session["user"] = {
                "id": user["id"],
                "name": user["name"],
                "role": user["role"],
                "username": user["username"],
                "email": "",
                "trial_ends_at": "",
                "is_paid": True,
            }
            return redirect(url_for("dashboard"))

        data = load_data()
        account = find_account_by_login(data, username)

        if account and account.get("password_hash") and check_password_hash(account["password_hash"], password):
            account["last_login_at"] = now_iso()
            save_data(data)

            session.permanent = True
            session["user"] = session_user_for_account(account)
            if user_is_entitled(session["user"]):
                return redirect(url_for("dashboard"))
            flash("Your trial has ended. Upgrade to continue.", "warning")
            return redirect(url_for("upgrade"))

        error = "Invalid email/username or password."

    return render_template("login.html", error=error, username=username, trial_days=TRIAL_DAYS)


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    data = load_data()
    today = date.today().isoformat()

    today_appts = [
        build_appointment_view(data, appointment)
        for appointment in data["appointments"]
        if appointment.get("date") == today
    ]
    today_appts.sort(key=lambda item: item.get("time", ""))

    recent_patients = sorted(
        data["patients"],
        key=lambda patient: patient.get("created_at", ""),
        reverse=True,
    )[:5]

    due_tasks = [
        task for task in data.get("tasks", [])
        if task.get("status") != "Done" and task.get("due_date") and task.get("due_date") <= today
    ][:6]

    insights = compute_crm_insights(data)

    return render_template(
        "dashboard.html",
        stats=insights,
        today_appts=today_appts,
        recent_patients=recent_patients,
        due_tasks=due_tasks,
    )


@app.route("/crm")
@login_required
def crm_command_center():
    data = load_data()
    insights = compute_crm_insights(data)
    campaigns = sorted(
        data.get("offers", []),
        key=lambda item: item.get("created_at", ""),
        reverse=True,
    )[:6]
    open_tasks = sorted(
        [task for task in data.get("tasks", []) if task.get("status") != "Done"],
        key=lambda item: (item.get("due_date") or "9999-12-31", item.get("priority", "")),
    )[:10]

    return render_template(
        "crm.html",
        insights=insights,
        campaigns=campaigns,
        open_tasks=open_tasks,
    )


@app.route("/patients")
@login_required
def patients():
    data = load_data()
    filtered_patients, filters = filter_patients(data)
    owners = sorted({patient.get("owner", "Unassigned") for patient in data["patients"]})

    return render_template(
        "patients.html",
        patients=filtered_patients,
        all_patients=data["patients"],
        filters=filters,
        owners=owners,
        insights=compute_crm_insights(data),
    )


@app.route("/patients/add", methods=["GET", "POST"])
@login_required
def add_patient():
    if request.method == "POST":
        data = load_data()

        patient = build_patient_payload(request.form)
        patient["id"] = str(uuid.uuid4())[:8]
        patient["created_at"] = now_iso()
        patient["history"] = []
        patient["treatment_plans"] = []

        errors = validate_patient_payload(patient)
        if errors:
            return render_template(
                "add_patient.html",
                error=" ".join(errors),
                patient=patient,
            )

        add_history(patient, f"Patient created from {patient.get('source', 'Unknown')} source.")
        data["patients"].append(patient)

        if patient.get("next_action"):
            create_task(
                data,
                patient["next_action"],
                patient_id=patient["id"],
                due_date=patient.get("followup_date", ""),
                priority="High" if patient.get("priority") in {"Hot", "VIP"} else "Normal",
                owner=patient.get("owner", ""),
                task_type="Next action",
            )

        record_activity(data, "Patient created", patient["name"], "New CRM patient record", patient["id"])
        save_data(data)
        flash("Patient record created with CRM tracking enabled.", "success")
        return redirect(url_for("patient_profile", pid=patient["id"]))

    return render_template("add_patient.html", patient=None, error=None)


@app.route("/patients/<pid>")
@login_required
def patient_profile(pid):
    data = load_data()
    patient = find_patient(data, pid)

    if not patient:
        flash("Patient record not found.", "error")
        return redirect(url_for("patients"))

    ensure_patient_defaults(patient)
    appointments = [
        build_appointment_view(data, appointment)
        for appointment in data["appointments"]
        if appointment.get("patient_id") == pid
    ]
    appointments.sort(key=lambda item: (item.get("date", ""), item.get("time", "")), reverse=True)

    tasks = [
        task for task in data.get("tasks", [])
        if task.get("patient_id") == pid and task.get("status") != "Done"
    ]
    tasks.sort(key=lambda item: item.get("due_date") or "9999-12-31")

    activities = [
        activity for activity in data.get("activities", [])
        if activity.get("patient_id") == pid
    ]
    activities.sort(key=lambda item: item.get("created_at", ""), reverse=True)

    patient["lead_score"] = score_patient(patient, appointments)

    return render_template(
        "patient_profile.html",
        patient=patient,
        appointments=appointments,
        tasks=tasks,
        activities=activities[:8],
        financials=patient_financials(patient),
    )


@app.route("/patients/<pid>/edit", methods=["GET", "POST"])
@login_required
def edit_patient(pid):
    data = load_data()
    patient = find_patient(data, pid)

    if not patient:
        flash("Patient record not found.", "error")
        return redirect(url_for("patients"))

    ensure_patient_defaults(patient)

    if request.method == "POST":
        old_stage = patient.get("stage")
        build_patient_payload(request.form, patient)

        errors = validate_patient_payload(patient)
        if errors:
            return render_template(
                "edit_patient.html",
                patient=patient,
                error=" ".join(errors),
            )

        if old_stage != patient.get("stage"):
            add_history(patient, f"Stage changed from {old_stage} to {patient.get('stage')}.")

        note = safe_strip(request.form.get("add_note"))
        if note:
            add_history(patient, note)

        if patient.get("next_action"):
            existing_open = [
                task for task in data.get("tasks", [])
                if task.get("patient_id") == pid and task.get("title") == patient["next_action"] and task.get("status") != "Done"
            ]
            if not existing_open:
                create_task(
                    data,
                    patient["next_action"],
                    patient_id=pid,
                    due_date=patient.get("followup_date", ""),
                    priority="High" if patient.get("priority") in {"Hot", "VIP"} else "Normal",
                    owner=patient.get("owner", ""),
                    task_type="Next action",
                )

        record_activity(data, "Patient updated", patient["name"], "CRM profile edited", pid)
        save_data(data)
        flash("Patient profile updated.", "success")
        return redirect(url_for("patient_profile", pid=pid))

    return render_template("edit_patient.html", patient=patient, error=None)


@app.route("/patients/<pid>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_patient(pid):
    data = load_data()
    patient = find_patient(data, pid)

    data["patients"] = [patient for patient in data["patients"] if patient.get("id") != pid]
    data["appointments"] = [
        appointment for appointment in data["appointments"] if appointment.get("patient_id") != pid
    ]
    data["tasks"] = [task for task in data.get("tasks", []) if task.get("patient_id") != pid]

    if patient:
        record_activity(data, "Patient deleted", patient.get("name", "Unknown"), "Record removed", pid)

    save_data(data)
    flash("Patient and related operational records were removed.", "success")
    return redirect(url_for("patients"))


@app.route("/patients/<pid>/tasks", methods=["POST"])
@login_required
def add_patient_task(pid):
    data = load_data()
    patient = find_patient(data, pid)
    if not patient:
        flash("Patient record not found.", "error")
        return redirect(url_for("patients"))

    title = safe_strip(request.form.get("title"))
    if not title:
        flash("Task title is required.", "error")
        return redirect(url_for("patient_profile", pid=pid))

    create_task(
        data,
        title,
        patient_id=pid,
        due_date=safe_strip(request.form.get("due_date")),
        priority=safe_strip(request.form.get("priority")) or "Normal",
        owner=safe_strip(request.form.get("owner")) or patient.get("owner"),
        task_type=safe_strip(request.form.get("type")) or "Follow-up",
    )
    add_history(patient, f"Task added: {title}")
    save_data(data)
    flash("Patient task added.", "success")
    return redirect(url_for("patient_profile", pid=pid))


@app.route("/patients/<pid>/treatment-plans", methods=["POST"])
@login_required
def add_treatment_plan(pid):
    data = load_data()
    patient = find_patient(data, pid)
    if not patient:
        flash("Patient record not found.", "error")
        return redirect(url_for("patients"))

    ensure_patient_defaults(patient)
    plan_name = safe_strip(request.form.get("name"))
    if not plan_name:
        flash("Treatment plan name is required.", "error")
        return redirect(url_for("patient_profile", pid=pid))

    plan = {
        "id": str(uuid.uuid4())[:8],
        "name": plan_name,
        "quoted_amount": safe_strip(request.form.get("quoted_amount")) or "0",
        "status": safe_strip(request.form.get("status")) or "Proposed",
        "target_date": safe_strip(request.form.get("target_date")),
        "notes": safe_strip(request.form.get("notes")),
        "created_at": now_iso(),
        "created_by": (current_user() or {}).get("name", "System"),
    }
    if plan["status"] not in TREATMENT_STATUSES:
        plan["status"] = "Proposed"

    patient["treatment_plans"].append(plan)
    quoted = to_money(plan["quoted_amount"])
    if quoted > to_money(patient.get("estimated_value")):
        patient["estimated_value"] = str(int(quoted))
        patient["payment_status"] = "Quoted"

    patient["stage"] = "Treatment Proposed" if patient.get("stage") in {"New Inquiry", "Visited Clinic"} else patient["stage"]
    add_history(patient, f"Treatment plan added: {plan_name} ({format_inr(quoted)})")
    record_activity(data, "Treatment plan added", patient["name"], plan_name, pid)
    save_data(data)
    flash("Treatment plan added to the patient opportunity.", "success")
    return redirect(url_for("patient_profile", pid=pid))


@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    data = load_data()

    if request.method == "POST":
        title = safe_strip(request.form.get("title"))
        if not title:
            flash("Task title is required.", "error")
            return redirect(url_for("tasks"))
        create_task(
            data,
            title,
            patient_id=safe_strip(request.form.get("patient_id")),
            due_date=safe_strip(request.form.get("due_date")),
            priority=safe_strip(request.form.get("priority")) or "Normal",
            owner=safe_strip(request.form.get("owner")),
            task_type=safe_strip(request.form.get("type")) or "Follow-up",
        )
        save_data(data)
        flash("Task created.", "success")
        return redirect(url_for("tasks"))

    status = safe_strip(request.args.get("status")) or "Open"
    task_items = data.get("tasks", [])
    if status != "All":
        task_items = [task for task in task_items if task.get("status") == status]

    task_items.sort(key=lambda item: (item.get("due_date") or "9999-12-31", item.get("priority", "")))
    patient_lookup = {patient["id"]: patient for patient in data["patients"]}
    owners = sorted({task.get("owner", "Unassigned") for task in data.get("tasks", [])} | {"Dr. Sharma", "Priya (Staff)"})

    return render_template(
        "tasks.html",
        tasks=task_items,
        patients=data["patients"],
        patient_lookup=patient_lookup,
        owners=owners,
        selected_status=status,
    )


@app.route("/tasks/<tid>/status", methods=["POST"])
@login_required
def update_task_status(tid):
    data = load_data()
    task = find_task(data, tid)
    if not task:
        flash("Task not found.", "error")
        return redirect(url_for("tasks"))

    new_status = safe_strip(request.form.get("status")) or "Done"
    if new_status not in TASK_STATUSES:
        flash("Invalid task status.", "error")
        return redirect(url_for("tasks"))

    task["status"] = new_status
    task["completed_at"] = now_iso() if new_status == "Done" else ""

    patient = find_patient(data, task.get("patient_id"))
    if patient:
        add_history(patient, f"Task marked {new_status}: {task.get('title')}")
    record_activity(data, "Task updated", task.get("title", "Task"), f"Status changed to {new_status}", task.get("patient_id"))
    save_data(data)
    flash("Task status updated.", "success")
    return redirect(request.referrer or url_for("tasks"))


@app.route("/appointments")
@login_required
def appointments():
    data = load_data()

    status = safe_strip(request.args.get("status"))
    items = [build_appointment_view(data, appointment) for appointment in data["appointments"]]
    if status:
        items = [item for item in items if item.get("status") == status]
    items.sort(key=lambda item: (item.get("date", ""), item.get("time", "")))

    return render_template(
        "appointments.html",
        appointments=items,
        selected_status=status,
        statuses=APPOINTMENT_STATUSES,
    )


@app.route("/appointments/add", methods=["GET", "POST"])
@login_required
def add_appointment():
    data = load_data()

    if request.method == "POST":
        patient_id = safe_strip(request.form.get("patient_id"))
        appointment_date = safe_strip(request.form.get("date"))
        appointment_time = safe_strip(request.form.get("time"))
        treatment = safe_strip(request.form.get("treatment"))
        notes = safe_strip(request.form.get("notes"))
        provider = safe_strip(request.form.get("provider")) or "Dr. Sharma"
        room = safe_strip(request.form.get("room")) or "Chair 1"
        channel = safe_strip(request.form.get("channel")) or "Front desk"

        errors = []

        patient = find_patient(data, patient_id)
        if not patient:
            errors.append("Please select a valid patient.")

        if not appointment_date:
            errors.append("Appointment date is required.")
        else:
            try:
                date.fromisoformat(appointment_date)
            except ValueError:
                errors.append("Appointment date is invalid.")

        if not appointment_time:
            errors.append("Appointment time is required.")

        double_booked = any(
            appointment.get("status") == "Scheduled"
            and appointment.get("date") == appointment_date
            and appointment.get("time") == appointment_time
            and appointment.get("provider", "Dr. Sharma") == provider
            for appointment in data["appointments"]
        )
        if double_booked:
            errors.append("That provider already has a scheduled appointment at this time.")

        if errors:
            return render_template(
                "add_appointment.html",
                patients=data["patients"],
                pid=patient_id,
                error=" ".join(errors),
            )

        appointment = {
            "id": str(uuid.uuid4())[:8],
            "patient_id": patient_id,
            "date": appointment_date,
            "time": appointment_time,
            "treatment": treatment,
            "notes": notes,
            "provider": provider,
            "room": room,
            "channel": channel,
            "status": "Scheduled",
            "created_at": now_iso(),
        }

        data["appointments"].append(appointment)

        if patient:
            ensure_patient_defaults(patient)
            if patient.get("stage") in {"New Inquiry", "Qualified Lead", "Missed Appointment"}:
                patient["stage"] = "Appointment Booked"
            patient["next_action"] = f"Confirm appointment for {appointment_date}"
            add_history(patient, f"Appointment booked for {appointment_date} at {appointment_time}.")

        record_activity(data, "Appointment booked", patient["name"] if patient else "Unknown", treatment or "General consultation", patient_id)
        save_data(data)
        flash("Appointment scheduled and patient timeline updated.", "success")
        return redirect(url_for("appointments"))

    pid = safe_strip(request.args.get("pid"))
    return render_template(
        "add_appointment.html",
        patients=data["patients"],
        pid=pid,
        error=None,
    )


@app.route("/appointments/<aid>/status", methods=["POST"])
@login_required
def update_appointment_status(aid):
    data = load_data()
    appointment = find_appointment(data, aid)

    if not appointment:
        flash("Appointment not found.", "error")
        return redirect(url_for("appointments"))

    new_status = safe_strip(request.form.get("status")) or "Scheduled"
    if new_status not in APPOINTMENT_STATUSES:
        flash("Invalid appointment status.", "error")
        return redirect(url_for("appointments"))

    appointment["status"] = new_status
    patient = find_patient(data, appointment.get("patient_id"))

    if patient:
        ensure_patient_defaults(patient)

        if new_status == "Completed":
            if patient.get("stage") in {"Appointment Booked", "Missed Appointment"}:
                patient["stage"] = "Visited Clinic"
            add_history(patient, f"Appointment completed on {appointment.get('date', '')}.")
            if not patient.get("followup_date"):
                recall_date = date.today() + timedelta(days=180)
                patient["followup_date"] = recall_date.isoformat()
                create_task(
                    data,
                    "Schedule six-month recall",
                    patient_id=patient["id"],
                    due_date=recall_date.isoformat(),
                    priority="Normal",
                    owner=patient.get("owner", ""),
                    task_type="Recall",
                )
        elif new_status == "Missed":
            patient["stage"] = "Missed Appointment"
            patient["followup_date"] = date.today().isoformat()
            add_history(patient, f"Missed appointment on {appointment.get('date', '')}.")
            create_task(
                data,
                "Recover missed appointment",
                patient_id=patient["id"],
                due_date=date.today().isoformat(),
                priority="Urgent",
                owner=patient.get("owner", ""),
                task_type="Recovery",
            )
        elif new_status == "Cancelled":
            add_history(patient, f"Appointment cancelled for {appointment.get('date', '')}.")

    record_activity(
        data,
        "Appointment updated",
        patient.get("name", "Unknown") if patient else "Unknown",
        f"Status changed to {new_status}",
        patient.get("id") if patient else None,
    )
    save_data(data)
    flash("Appointment status updated.", "success")
    return redirect(url_for("appointments"))


@app.route("/followups")
@login_required
def followups():
    data = load_data()
    today = date.today()

    result = []
    seen_ids = set()

    for patient in data["patients"]:
        ensure_patient_defaults(patient)
        if patient["id"] in seen_ids:
            continue

        include_patient = False

        if patient.get("stage") in ["Follow-up Due", "Missed Appointment", "Treatment Ongoing", "Treatment Proposed"]:
            include_patient = True
        elif patient.get("followup_date"):
            try:
                followup_dt = date.fromisoformat(patient["followup_date"])
                if followup_dt <= today + timedelta(days=7):
                    include_patient = True
            except ValueError:
                pass

        if include_patient:
            patient["lead_score"] = score_patient(patient, patient_appointments(data, patient["id"]))
            result.append(patient)
            seen_ids.add(patient["id"])

    result.sort(key=lambda patient: (patient.get("followup_date") or "9999-12-31", -patient.get("lead_score", 0)))
    return render_template("followups.html", patients=result)


@app.route("/broadcast", methods=["GET", "POST"])
@login_required
def broadcast():
    data = load_data()

    if request.method == "POST":
        promotion_id = safe_strip(request.form.get("promotion_id"))
        title = safe_strip(request.form.get("title"))
        message = safe_strip(request.form.get("message"))
        channel = safe_strip(request.form.get("channel")) or "WhatsApp"
        segment = safe_strip(request.form.get("segment")) or "Manual selection"
        patient_ids = request.form.getlist("patient_ids")

        upload_error = None
        image_path = ""
        uploaded_image = request.files.get("image")
        if uploaded_image and uploaded_image.filename:
            try:
                image_path = save_uploaded_image(uploaded_image)
            except ValueError as exc:
                upload_error = str(exc)

        promo = find_promotion(data, promotion_id) if promotion_id else None
        if promo:
            if not title:
                title = promo.get("title", "")
            if not message:
                message = promo.get("message", "")
            if not image_path and promo.get("image_path"):
                image_path = promo.get("image_path", "")
            if segment == "Manual selection":
                segment = f"Offer: {promo.get('title', 'Promotion')}"

        errors = []
        if upload_error:
            errors.append(upload_error)
        if not title:
            errors.append("Campaign title is required.")
        if not message:
            errors.append("Message content is required.")
        if not patient_ids:
            errors.append("Please select at least one patient.")

        if errors:
            return render_template(
                "broadcast.html",
                patients=data["patients"],
                promotions=[p for p in data.get("promotions", []) if p.get("active")],
                error=" ".join(errors),
                form_data={
                    "promotion_id": promotion_id,
                    "title": title,
                    "message": message,
                    "channel": channel,
                    "segment": segment,
                    "patient_ids": patient_ids,
                },
            )

        offer = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "message": message,
            "patient_ids": patient_ids,
            "channel": channel,
            "segment": segment,
            "promotion_id": promotion_id,
            "image_path": image_path,
            "sent": {},
            "created_at": now_iso(),
            "created_by": current_user()["name"],
            "status": "Preview Ready",
        }

        data["offers"].append(offer)
        selected_patients = [patient for patient in data["patients"] if patient["id"] in patient_ids]
        for patient in selected_patients:
            ensure_patient_defaults(patient)
            patient["last_contacted_at"] = now_iso()
            add_history(patient, f"Included in campaign preview: {title}")

        record_activity(data, "Campaign created", title, f"{len(selected_patients)} recipients via {channel}")
        save_data(data)
        return redirect(url_for("broadcast_preview", offer_id=offer["id"]))

    patients = sorted(data["patients"], key=lambda patient: patient.get("name", ""))
    promotions = [p for p in data.get("promotions", []) if p.get("active")]
    return render_template("broadcast.html", patients=patients, promotions=promotions, error=None, form_data=None)


@app.route("/broadcast/<offer_id>/mark-sent", methods=["POST"])
@login_required
def broadcast_mark_sent(offer_id):
    data = load_data()
    offer = next((item for item in data.get("offers", []) if item.get("id") == offer_id), None)
    if not offer:
        flash("Campaign not found.", "error")
        return redirect(url_for("broadcast"))

    patient_id = safe_strip(request.form.get("patient_id"))
    channel = safe_strip(request.form.get("channel")) or offer.get("channel", "WhatsApp")
    offer.setdefault("sent", {})
    offer["sent"][patient_id] = {"channel": channel, "sent_at": now_iso()}

    patient = find_patient(data, patient_id)
    if patient:
        add_history(patient, f"Campaign sent ({channel}): {offer.get('title', '')}")
    record_activity(data, "Campaign marked sent", offer.get("title", "Campaign"), channel, patient_id or None)
    save_data(data)
    flash("Recipient marked as sent.", "success")
    return redirect(url_for("broadcast_preview", offer_id=offer_id))


@app.route("/broadcast/<offer_id>/preview")
@login_required
def broadcast_preview(offer_id):
    data = load_data()
    offer = next((item for item in data.get("offers", []) if item.get("id") == offer_id), None)
    if not offer:
        flash("Campaign not found.", "error")
        return redirect(url_for("broadcast"))

    selected_patients = [patient for patient in data["patients"] if patient.get("id") in offer.get("patient_ids", [])]
    image_external_url = ""
    if offer.get("image_path"):
        image_external_url = (request.url_root or "").rstrip("/") + url_for(
            "static",
            filename=offer["image_path"],
        )

    recipients = []
    for patient in selected_patients:
        personalized = render_message_template(offer.get("message", ""), patient, data)
        recipients.append(
            {
                "patient": patient,
                "message_text": personalized,
                "whatsapp_phone": normalize_phone(patient.get("phone", "")).replace("+", ""),
                "email": patient.get("email", ""),
            }
        )

    return render_template(
        "broadcast_preview.html",
        offer=offer,
        recipients=recipients,
        image_external_url=image_external_url,
    )


@app.route("/offers", methods=["GET", "POST"])
@login_required
def offers():
    data = load_data()

    if request.method == "POST":
        title = safe_strip(request.form.get("title"))
        description = safe_strip(request.form.get("description"))
        message = safe_strip(request.form.get("message"))
        cta = safe_strip(request.form.get("cta"))
        active = checkbox_value(request.form, "active", True)

        image_path = ""
        uploaded_image = request.files.get("image")
        if uploaded_image and uploaded_image.filename:
            try:
                image_path = save_uploaded_image(uploaded_image)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("aftercare"))
                return redirect(url_for("offers"))

        if not title or not message:
            flash("Offer title and message are required.", "error")
            return redirect(url_for("offers"))

        promo = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "description": description,
            "message": message,
            "cta": cta,
            "image_path": image_path,
            "active": active,
            "created_at": now_iso(),
            "created_by": (current_user() or {}).get("name", "System"),
        }
        data.setdefault("promotions", []).append(promo)
        record_activity(data, "Offer created", title, "Promotion added")
        save_data(data)
        flash("Offer saved to the library.", "success")
        return redirect(url_for("offers"))

    promotions = sorted(
        data.get("promotions", []),
        key=lambda item: (not bool(item.get("active")), item.get("created_at", "")),
    )
    campaigns = sorted(data.get("offers", []), key=lambda item: item.get("created_at", ""), reverse=True)[:10]
    return render_template("offers.html", promotions=promotions, campaigns=campaigns)


@app.route("/offers/<promo_id>/toggle", methods=["POST"])
@login_required
def offers_toggle(promo_id):
    data = load_data()
    promo = find_promotion(data, promo_id)
    if not promo:
        flash("Offer not found.", "error")
        return redirect(url_for("offers"))

    promo["active"] = not bool(promo.get("active"))
    record_activity(data, "Offer toggled", promo.get("title", "Offer"), f"Active={promo['active']}")
    save_data(data)
    flash("Offer status updated.", "success")
    return redirect(url_for("offers"))


@app.route("/offers/<promo_id>/edit", methods=["GET", "POST"])
@login_required
def offers_edit(promo_id):
    data = load_data()
    promo = find_promotion(data, promo_id)
    if not promo:
        flash("Offer not found.", "error")
        return redirect(url_for("offers"))

    if request.method == "POST":
        promo["title"] = safe_strip(request.form.get("title")) or promo.get("title", "")
        promo["description"] = safe_strip(request.form.get("description"))
        promo["message"] = safe_strip(request.form.get("message"))
        promo["cta"] = safe_strip(request.form.get("cta"))
        promo["active"] = checkbox_value(request.form, "active", True)

        uploaded_image = request.files.get("image")
        if uploaded_image and uploaded_image.filename:
            try:
                promo["image_path"] = save_uploaded_image(uploaded_image)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("offers_edit", promo_id=promo_id))

        if not promo.get("title") or not promo.get("message"):
            flash("Offer title and message are required.", "error")
            return redirect(url_for("offers_edit", promo_id=promo_id))

        record_activity(data, "Offer updated", promo.get("title", "Offer"), "Promotion edited")
        save_data(data)
        flash("Offer updated.", "success")
        return redirect(url_for("offers"))

    return render_template("offers_edit.html", promo=promo)


@app.route("/offers/<promo_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def offers_delete(promo_id):
    data = load_data()
    promo = find_promotion(data, promo_id)
    data["promotions"] = [item for item in data.get("promotions", []) if item.get("id") != promo_id]
    if promo:
        record_activity(data, "Offer deleted", promo.get("title", "Offer"), "Promotion removed")
    save_data(data)
    flash("Offer deleted.", "success")
    return redirect(url_for("offers"))


@app.route("/aftercare", methods=["GET", "POST"])
@login_required
def aftercare():
    data = load_data()

    if request.method == "POST":
        template_id = safe_strip(request.form.get("template_id"))
        patient_ids = request.form.getlist("patient_ids")
        channels = request.form.getlist("channels") or ["WhatsApp"]
        additional_note = safe_strip(request.form.get("note"))

        template = find_aftercare_template(data, template_id)
        if not template:
            flash("Please select a valid aftercare template.", "error")
            return redirect(url_for("aftercare"))
        if not patient_ids:
            flash("Please select at least one patient.", "error")
            return redirect(url_for("aftercare"))

        image_path = ""
        uploaded_image = request.files.get("image")
        if uploaded_image and uploaded_image.filename:
            try:
                image_path = save_uploaded_image(uploaded_image)
            except ValueError as exc:
                flash(str(exc), "error")

        send = {
            "id": str(uuid.uuid4())[:8],
            "template_id": template_id,
            "title": template.get("title", "Aftercare"),
            "subject": template.get("subject", "Aftercare"),
            "body": template.get("body", ""),
            "channels": channels,
            "patient_ids": patient_ids,
            "image_path": image_path or template.get("image_path", ""),
            "created_at": now_iso(),
            "created_by": (current_user() or {}).get("name", "System"),
            "status": "Preview Ready",
            "sent": {},
            "note": additional_note,
        }
        data.setdefault("aftercare_sends", []).append(send)

        selected_patients = [p for p in data["patients"] if p.get("id") in patient_ids]
        for patient in selected_patients:
            ensure_patient_defaults(patient)
            add_history(patient, f"Aftercare prepared: {template.get('procedure', 'Procedure')}")

        record_activity(
            data,
            "Aftercare prepared",
            template.get("procedure", "Aftercare"),
            f"{len(patient_ids)} recipients",
        )
        save_data(data)
        return redirect(url_for("aftercare_preview", send_id=send["id"]))

    templates = [t for t in data.get("aftercare_templates", []) if t.get("active")]
    patients = sorted(data["patients"], key=lambda patient: patient.get("name", ""))
    return render_template("aftercare.html", templates=templates, patients=patients)


@app.route("/aftercare/<send_id>/preview")
@login_required
def aftercare_preview(send_id):
    data = load_data()
    send = find_aftercare_send(data, send_id)
    if not send:
        flash("Aftercare send not found.", "error")
        return redirect(url_for("aftercare"))

    selected_patients = [p for p in data["patients"] if p.get("id") in send.get("patient_ids", [])]
    image_external_url = ""
    if send.get("image_path"):
        image_external_url = (request.url_root or "").rstrip("/") + url_for(
            "static",
            filename=send["image_path"],
        )

    recipients = []
    for patient in selected_patients:
        personalized = render_message_template(send.get("body", ""), patient, data)
        if send.get("note"):
            personalized = f"{personalized}\n\nNote: {send['note']}"
        recipients.append(
            {
                "patient": patient,
                "message_text": personalized,
                "whatsapp_phone": normalize_phone(patient.get("phone", "")).replace("+", ""),
                "email": patient.get("email", ""),
            }
        )

    return render_template(
        "aftercare_preview.html",
        send=send,
        recipients=recipients,
        image_external_url=image_external_url,
    )


@app.route("/aftercare/<send_id>/mark-sent", methods=["POST"])
@login_required
def aftercare_mark_sent(send_id):
    data = load_data()
    send = find_aftercare_send(data, send_id)
    if not send:
        flash("Aftercare send not found.", "error")
        return redirect(url_for("aftercare"))

    patient_id = safe_strip(request.form.get("patient_id"))
    channel = safe_strip(request.form.get("channel")) or "WhatsApp"
    send.setdefault("sent", {})
    send["sent"][patient_id] = {"channel": channel, "sent_at": now_iso()}

    patient = find_patient(data, patient_id)
    if patient:
        add_history(patient, f"Aftercare sent ({channel}): {send.get('title', '')}")
    record_activity(data, "Aftercare marked sent", send.get("title", "Aftercare"), channel, patient_id or None)
    save_data(data)
    flash("Recipient marked as sent.", "success")
    return redirect(request.referrer or url_for("aftercare_preview", send_id=send_id))


@app.route("/aftercare/templates", methods=["POST"])
@login_required
def aftercare_template_create():
    data = load_data()
    procedure = safe_strip(request.form.get("procedure"))
    title = safe_strip(request.form.get("title"))
    subject = safe_strip(request.form.get("subject"))
    body = safe_strip(request.form.get("body"))
    active = checkbox_value(request.form, "active", True)

    if not procedure or not title or not body:
        flash("Procedure, title, and body are required.", "error")
        return redirect(url_for("aftercare"))

    image_path = ""
    uploaded_image = request.files.get("image")
    if uploaded_image and uploaded_image.filename:
        try:
            image_path = save_uploaded_image(uploaded_image)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("aftercare"))

    template = {
        "id": str(uuid.uuid4())[:8],
        "procedure": procedure,
        "title": title,
        "subject": subject or f"Aftercare - {procedure}",
        "body": body,
        "image_path": image_path,
        "active": active,
        "default_channels": ["WhatsApp", "Email"],
        "created_at": now_iso(),
        "created_by": (current_user() or {}).get("name", "System"),
    }
    data.setdefault("aftercare_templates", []).append(template)
    record_activity(data, "Aftercare template created", procedure, title)
    save_data(data)
    flash("Aftercare template created.", "success")
    return redirect(url_for("aftercare"))


@app.route("/audit")
@login_required
@role_required("admin")
def audit():
    data = load_data()
    activities = sorted(
        data.get("activities", []),
        key=lambda item: item.get("created_at", ""),
        reverse=True,
    )
    return render_template("audit.html", activities=activities[:150])


@app.route("/reports/export/patients.csv")
@login_required
def export_patients_csv():
    data = load_data()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Name",
            "Phone",
            "Email",
            "Stage",
            "Owner",
            "Source",
            "Priority",
            "Treatment",
            "Estimated Value",
            "Amount Collected",
            "Payment Status",
            "Follow-up Date",
            "Lead Score",
            "Tags",
        ]
    )
    for patient in data["patients"]:
        ensure_patient_defaults(patient)
        writer.writerow(
            [
                patient.get("name", ""),
                patient.get("phone", ""),
                patient.get("email", ""),
                patient.get("stage", ""),
                patient.get("owner", ""),
                patient.get("source", ""),
                patient.get("priority", ""),
                patient.get("treatment", ""),
                patient.get("estimated_value", ""),
                patient.get("amount_collected", ""),
                patient.get("payment_status", ""),
                patient.get("followup_date", ""),
                score_patient(patient, patient_appointments(data, patient["id"])),
                ", ".join(patient.get("tags", [])),
            ]
        )

    filename = f"dental-crm-patients-{date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/reminders")
@login_required
def get_reminders():
    data = load_data()
    today = date.today()
    tomorrow = today + timedelta(days=1)
    reminders = []

    for appointment in data["appointments"]:
        if appointment.get("status") != "Scheduled":
            continue

        try:
            appointment_date = date.fromisoformat(appointment["date"])
        except (KeyError, ValueError):
            continue

        patient = find_patient(data, appointment.get("patient_id"))
        patient_name = patient["name"] if patient else "Unknown"

        if appointment_date == tomorrow:
            reminders.append(
                {
                    "type": "reminder",
                    "msg": f"Appointment tomorrow: {patient_name} at {appointment.get('time', '')}",
                    "color": "blue",
                }
            )
        elif appointment_date < today:
            reminders.append(
                {
                    "type": "missed",
                    "msg": f"Missed appointment: {patient_name} was scheduled on {appointment.get('date')}",
                    "color": "red",
                }
            )

    for patient in data["patients"]:
        followup_date = patient.get("followup_date")
        if not followup_date:
            continue

        try:
            followup_dt = date.fromisoformat(followup_date)
        except ValueError:
            continue

        if followup_dt <= today:
            reminders.append(
                {
                    "type": "followup",
                    "msg": f"Follow-up due: {patient.get('name', 'Unknown')} ({patient.get('treatment', 'General')})",
                    "color": "orange",
                }
            )

    for task in data.get("tasks", []):
        if task.get("status") == "Done" or not task.get("due_date"):
            continue
        try:
            due_date = date.fromisoformat(task["due_date"])
        except ValueError:
            continue
        if due_date <= today:
            reminders.append(
                {
                    "type": "task",
                    "msg": f"Task due: {task.get('title', 'Untitled task')}",
                    "color": "orange",
                }
            )

    return jsonify(reminders)


@app.route("/api/insights")
@login_required
def api_insights():
    data = load_data()
    return jsonify(compute_crm_insights(data))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": now_iso()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4040"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, port=port)
