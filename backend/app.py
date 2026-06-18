import html
import os
import re
from datetime import datetime

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
CORS(app)
FRONTEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Free time credits granted to every new member at signup (enough for a few skill sessions).
NEW_USER_WELCOME_CREDITS = 3


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "skillswap_db"),
    )


def ensure_optional_tables():
    """Create complaints/messages/certificate_requests if missing (DBs created before those migrations)."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for stmt in (
            """
            CREATE TABLE IF NOT EXISTS complaints (
                id INT AUTO_INCREMENT PRIMARY KEY,
                submitted_by_user_id INT NOT NULL,
                target_user_id INT NULL,
                category ENUM('user_issue', 'bug', 'system', 'other') NOT NULL DEFAULT 'other',
                subject VARCHAR(200) NOT NULL,
                body TEXT NOT NULL,
                status ENUM('open', 'in_progress', 'resolved', 'closed') NOT NULL DEFAULT 'open',
                admin_feedback TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_complaint_submitter FOREIGN KEY (submitted_by_user_id)
                    REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_complaint_target FOREIGN KEY (target_user_id)
                    REFERENCES users(id) ON DELETE SET NULL,
                INDEX idx_complaints_status (status, created_at)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                from_user_id INT NOT NULL,
                to_user_id INT NOT NULL,
                body VARCHAR(2000) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_msg_from FOREIGN KEY (from_user_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_msg_to FOREIGN KEY (to_user_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_msg_pair_time (from_user_id, to_user_id, created_at),
                INDEX idx_messages_to (to_user_id, created_at)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS certificate_requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                transaction_id INT NOT NULL,
                student_user_id INT NOT NULL,
                teacher_user_id INT NOT NULL,
                skill_name VARCHAR(100) NOT NULL,
                status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
                teacher_note VARCHAR(500) NULL,
                requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                responded_at DATETIME NULL,
                issued_at DATETIME NULL,
                UNIQUE KEY uq_cert_tx_student (transaction_id, student_user_id),
                CONSTRAINT fk_cert_tx FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE,
                CONSTRAINT fk_cert_student FOREIGN KEY (student_user_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_cert_teacher FOREIGN KEY (teacher_user_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_cert_teacher_status (teacher_user_id, status)
            )
            """,
        ):
            cursor.execute(stmt)
        conn.commit()
    except Error as exc:
        import sys

        print(f"[SkillSwap] ensure_optional_tables: {exc}", file=sys.stderr)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9]{10,15}$")


def normalize_phone(raw_phone):
    if raw_phone is None:
        return ""
    return re.sub(r"[\s\-()]", "", str(raw_phone))


def is_valid_email(email):
    return bool(EMAIL_PATTERN.fullmatch((email or "").strip()))


def is_valid_phone(phone):
    return bool(PHONE_PATTERN.fullmatch(normalize_phone(phone)))


def iso_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return val


def fetch_users(search_text=""):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT
                u.id,
                u.full_name,
                u.email,
                u.phone,
                u.role,
                u.reliability_score,
                u.credits,
                u.account_status,
                u.last_active,
                u.created_at
            FROM users u
        """
        params = []
        if search_text:
            query += " WHERE u.full_name LIKE %s OR u.email LIKE %s OR u.phone LIKE %s "
            like = f"%{search_text}%"
            params.extend([like, like, like])
        query += " ORDER BY u.created_at DESC"
        cursor.execute(query, params)
        users = cursor.fetchall()

        if not users:
            return []

        user_ids = [row["id"] for row in users]
        placeholders = ",".join(["%s"] * len(user_ids))
        cursor.execute(
            f"""
            SELECT user_id, skill_name, skill_type
            FROM user_skills
            WHERE user_id IN ({placeholders})
            ORDER BY skill_name ASC
            """,
            user_ids,
        )
        skill_rows = cursor.fetchall()
        skill_map = {uid: {"offers": [], "seeks": []} for uid in user_ids}
        for row in skill_rows:
            if row["skill_type"] == "offer":
                skill_map[row["user_id"]]["offers"].append(row["skill_name"])
            else:
                skill_map[row["user_id"]]["seeks"].append(row["skill_name"])

        for user in users:
            user["offers"] = skill_map[user["id"]]["offers"]
            user["seeks"] = skill_map[user["id"]]["seeks"]
            if isinstance(user["last_active"], datetime):
                user["last_active"] = user["last_active"].isoformat()
            if isinstance(user["created_at"], datetime):
                user["created_at"] = user["created_at"].isoformat()

        return users
    finally:
        conn.close()


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/auth/signup")
def signup():
    payload = request.get_json(silent=True) or {}
    full_name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    phone = normalize_phone(payload.get("phone"))
    password = payload.get("password") or ""
    role = (payload.get("role") or "member").strip().lower()
    admin_secret = (payload.get("admin_secret") or "").strip()

    if not full_name:
        return jsonify({"error": "Name is required."}), 400
    if not is_valid_email(email):
        return jsonify({"error": "Provide a valid email address."}), 400
    if not is_valid_phone(phone):
        return jsonify({"error": "Provide a valid phone number (10-15 digits)."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if role not in ("member", "admin"):
        role = "member"
    if role == "admin":
        expected = os.getenv("ADMIN_SIGNUP_SECRET", "change-this-secret")
        if not expected or admin_secret != expected:
            return jsonify({"error": "Invalid admin signup secret."}), 403

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE email = %s OR phone = %s", (email, phone))
        existing = cursor.fetchone()
        if existing:
            return jsonify({"error": "User with this email or phone already exists."}), 409

        welcome_credits = NEW_USER_WELCOME_CREDITS if role == "member" else 0
        cursor.execute(
            """
            INSERT INTO users (full_name, email, phone, password_hash, role, credits)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (full_name, email, phone, generate_password_hash(password), role, welcome_credits),
        )
        user_id = cursor.lastrowid
        conn.commit()
        msg = "Account created successfully."
        if welcome_credits:
            msg += f" You received {welcome_credits} free time credits to get started."
        return jsonify({"message": msg, "user_id": user_id, "welcome_credits": welcome_credits}), 201
    finally:
        conn.close()


@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    identifier = (payload.get("identifier") or "").strip()
    password = payload.get("password") or ""

    if not identifier or not password:
        return jsonify({"error": "Identifier and password are required."}), 400

    email = identifier.lower()
    phone = normalize_phone(identifier)
    if not (is_valid_email(email) or is_valid_phone(phone)):
        return jsonify({"error": "Login identifier must be a valid email or phone number."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        if is_valid_email(email):
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        else:
            cursor.execute("SELECT * FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"error": "Invalid credentials."}), 401
        if user["account_status"] == "locked":
            return jsonify({"error": "This account is locked by admin."}), 403
        if not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Invalid credentials."}), 401

        cursor.execute("UPDATE users SET last_active = NOW() WHERE id = %s", (user["id"],))
        conn.commit()

        return jsonify(
            {
                "message": "Login successful.",
                "user": {
                    "id": user["id"],
                    "name": user["full_name"],
                    "email": user["email"],
                    "phone": user["phone"],
                    "role": user["role"],
                    "credits": user["credits"],
                    "reliability_score": float(user["reliability_score"]),
                    "account_status": user["account_status"],
                },
            }
        )
    finally:
        conn.close()


@app.get("/api/users")
def list_users():
    search = (request.args.get("search") or "").strip()
    users = fetch_users(search)
    return jsonify({"users": users, "count": len(users)})


def fetch_user_by_id(user_id):
    users = fetch_users("")
    for u in users:
        if u["id"] == user_id:
            return u
    return None


@app.get("/api/users/<int:user_id>")
def get_user(user_id):
    user = fetch_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found."}), 404
    return jsonify({"user": user})


@app.delete("/api/users/<int:user_id>")
def delete_user(user_id):
    payload = request.get_json(silent=True) or {}
    password = payload.get("password") or ""
    if not password:
        return jsonify({"error": "Password is required to delete your account."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, password_hash, role FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "User not found."}), 404
        if not check_password_hash(row["password_hash"], password):
            return jsonify({"error": "Incorrect password."}), 401
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return jsonify({"message": "Account deleted."})
    finally:
        conn.close()


@app.post("/api/users/<int:user_id>/skills")
def add_user_skill(user_id):
    payload = request.get_json(silent=True) or {}
    skill_name = (payload.get("skill_name") or "").strip()
    skill_type = (payload.get("skill_type") or "").strip().lower()
    if not skill_name:
        return jsonify({"error": "skill_name is required."}), 400
    if skill_type not in ("offer", "seek"):
        return jsonify({"error": "skill_type must be offer or seek."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({"error": "User not found."}), 404
        cursor.execute(
            "SELECT id FROM user_skills WHERE user_id = %s AND skill_name = %s AND skill_type = %s",
            (user_id, skill_name, skill_type),
        )
        if cursor.fetchone():
            return jsonify({"message": "Skill already exists."}), 200
        cursor.execute(
            "INSERT INTO user_skills (user_id, skill_name, skill_type) VALUES (%s, %s, %s)",
            (user_id, skill_name, skill_type),
        )
        conn.commit()
        return jsonify({"message": "Skill saved."}), 201
    except Error as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.patch("/api/users/<int:user_id>/profile")
def update_profile(user_id):
    payload = request.get_json(silent=True) or {}
    full_name = (payload.get("full_name") or "").strip()
    if not full_name:
        return jsonify({"error": "full_name is required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("UPDATE users SET full_name = %s WHERE id = %s", (full_name, user_id))
        if cursor.rowcount == 0:
            return jsonify({"error": "User not found."}), 404
        conn.commit()
        return jsonify({"message": "Profile updated."})
    finally:
        conn.close()


@app.delete("/api/users/<int:user_id>/skills")
def remove_user_skill(user_id):
    skill_name = (request.args.get("skill_name") or "").strip()
    skill_type = (request.args.get("skill_type") or "").strip().lower()
    if not skill_name or skill_type not in ("offer", "seek"):
        return jsonify({"error": "skill_name and skill_type (offer|seek) are required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "DELETE FROM user_skills WHERE user_id = %s AND skill_name = %s AND skill_type = %s",
            (user_id, skill_name, skill_type),
        )
        conn.commit()
        return jsonify({"message": "Skill removed."})
    finally:
        conn.close()


@app.patch("/api/users/<int:user_id>/status")
def update_user_status(user_id):
    payload = request.get_json(silent=True) or {}
    status = (payload.get("status") or "").strip().lower()
    if status not in ("active", "locked", "at_risk"):
        return jsonify({"error": "status must be active, locked, or at_risk"}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("UPDATE users SET account_status = %s WHERE id = %s", (status, user_id))
        if cursor.rowcount == 0:
            return jsonify({"error": "User not found."}), 404
        conn.commit()
        return jsonify({"message": "User status updated."})
    finally:
        conn.close()


@app.get("/api/transactions")
def list_transactions():
    status = (request.args.get("status") or "all").strip().lower()
    search = (request.args.get("search") or "").strip().lower()
    involving = request.args.get("involving_user_id")
    involving_id = None
    if involving is not None and str(involving).strip() != "":
        try:
            involving_id = int(involving)
        except ValueError:
            involving_id = None

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT
                t.id,
                t.requester_user_id,
                t.provider_user_id,
                t.skill_name,
                t.status,
                t.credit_hours,
                t.notes,
                t.requested_at,
                t.completed_at,
                requester.full_name AS requester_name,
                provider.full_name AS provider_name
            FROM transactions t
            JOIN users requester ON requester.id = t.requester_user_id
            JOIN users provider ON provider.id = t.provider_user_id
        """
        where_clauses = []
        params = []
        if status in ("pending", "completed", "declined"):
            where_clauses.append("t.status = %s")
            params.append(status)
        if involving_id is not None:
            where_clauses.append("(t.requester_user_id = %s OR t.provider_user_id = %s)")
            params.extend([involving_id, involving_id])
        if search:
            where_clauses.append(
                "(LOWER(t.skill_name) LIKE %s OR LOWER(requester.full_name) LIKE %s OR LOWER(provider.full_name) LIKE %s)"
            )
            like = f"%{search}%"
            params.extend([like, like, like])
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY t.requested_at DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        for row in rows:
            if isinstance(row["requested_at"], datetime):
                row["requested_at"] = row["requested_at"].isoformat()
            if isinstance(row["completed_at"], datetime):
                row["completed_at"] = row["completed_at"].isoformat()
            row["credit_hours"] = float(row["credit_hours"])

        return jsonify({"transactions": rows, "count": len(rows)})
    finally:
        conn.close()


@app.get("/api/transactions/stats")
def transaction_stats():
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(status = 'pending') AS pending,
                SUM(status = 'completed') AS completed,
                SUM(status = 'declined') AS declined,
                COALESCE(SUM(CASE WHEN status='completed' THEN credit_hours ELSE 0 END), 0) AS total_credit_exchanged,
                COALESCE(AVG(CASE WHEN status='completed' THEN credit_hours ELSE NULL END), 0) AS avg_transaction_size
            FROM transactions
            """
        )
        stats = cursor.fetchone()
        total = int(stats["total"] or 0)
        completed = int(stats["completed"] or 0)
        completion_rate = round((completed / total) * 100, 2) if total else 0
        return jsonify(
            {
                "total": total,
                "pending": int(stats["pending"] or 0),
                "completed": completed,
                "declined": int(stats["declined"] or 0),
                "completion_rate": completion_rate,
                "total_credit_exchanged": float(stats["total_credit_exchanged"] or 0),
                "avg_transaction_size": float(stats["avg_transaction_size"] or 0),
            }
        )
    finally:
        conn.close()


@app.post("/api/transactions")
def create_transaction():
    payload = request.get_json(silent=True) or {}
    try:
        requester_id = int(payload.get("requester_user_id"))
        provider_id = int(payload.get("provider_user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "requester_user_id and provider_user_id must be valid integers."}), 400
    skill_name = (payload.get("skill_name") or "").strip()
    credit_hours = payload.get("credit_hours", 0)
    notes = (payload.get("notes") or "").strip()

    if not skill_name:
        return jsonify({"error": "skill_name is required."}), 400
    if requester_id == provider_id:
        return jsonify({"error": "You cannot request a session with yourself."}), 400

    try:
        credit_hours = float(credit_hours)
    except ValueError:
        return jsonify({"error": "credit_hours must be a number."}), 400
    if credit_hours <= 0:
        return jsonify({"error": "credit_hours must be greater than 0."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, account_status FROM users WHERE id IN (%s, %s)",
            (requester_id, provider_id),
        )
        rows = cursor.fetchall()
        if len(rows) != 2:
            return jsonify({"error": "Requester or provider account not found."}), 404
        for row in rows:
            if row["account_status"] == "locked":
                return jsonify({"error": "Cannot create a session with a locked account."}), 403
        cursor.execute(
            """
            SELECT 1 FROM user_skills
            WHERE user_id = %s AND skill_type = 'offer' AND skill_name = %s
            LIMIT 1
            """,
            (provider_id, skill_name),
        )
        if not cursor.fetchone():
            return jsonify({"error": "That skill is not listed as offered by this member."}), 400
        cursor.execute("SELECT credits FROM users WHERE id = %s", (requester_id,))
        requester = cursor.fetchone()
        if requester["credits"] < credit_hours:
            return jsonify(
                {
                    "error": (
                        f"Insufficient credits. You have {requester['credits']} hour(s) "
                        f"but this session costs {credit_hours}."
                    )
                }
            ), 400
        cursor.execute(
            """
            INSERT INTO transactions (requester_user_id, provider_user_id, skill_name, credit_hours, notes)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (requester_id, provider_id, skill_name, credit_hours, notes),
        )
        cursor.execute(
            "UPDATE users SET credits = credits - %s WHERE id = %s",
            (credit_hours, requester_id),
        )
        conn.commit()
        return jsonify({"message": "Transaction created.", "transaction_id": cursor.lastrowid}), 201
    except Error as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.patch("/api/transactions/<int:transaction_id>/status")
def update_transaction_status(transaction_id):
    payload = request.get_json(silent=True) or {}
    status = (payload.get("status") or "").strip().lower()
    if status not in ("pending", "completed", "declined"):
        return jsonify({"error": "status must be pending, completed, or declined"}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT requester_user_id, provider_user_id, status, credit_hours
            FROM transactions WHERE id = %s
            """,
            (transaction_id,),
        )
        tx = cursor.fetchone()
        if not tx:
            return jsonify({"error": "Transaction not found."}), 404
        old_status = tx["status"]
        credit_hours = float(tx["credit_hours"])

        if status == "completed":
            cursor.execute(
                "UPDATE transactions SET status=%s, completed_at=NOW() WHERE id=%s",
                (status, transaction_id),
            )
        else:
            cursor.execute(
                "UPDATE transactions SET status=%s, completed_at=NULL WHERE id=%s",
                (status, transaction_id),
            )

        if status == "completed" and old_status == "pending":
            cursor.execute(
                "UPDATE users SET credits = credits + %s WHERE id = %s",
                (credit_hours, tx["provider_user_id"]),
            )
        elif status == "declined" and old_status == "pending":
            cursor.execute(
                "UPDATE users SET credits = credits + %s WHERE id = %s",
                (credit_hours, tx["requester_user_id"]),
            )

        conn.commit()
        return jsonify({"message": "Transaction updated."})
    finally:
        conn.close()


# --- Skill requests (custom / "Others" skills → admin approval) ---


@app.post("/api/skill-requests")
def create_skill_request():
    payload = request.get_json(silent=True) or {}
    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "user_id is required."}), 400
    skill_name = (payload.get("skill_name") or "").strip()
    skill_type = (payload.get("skill_type") or "").strip().lower()
    if not skill_name:
        return jsonify({"error": "skill_name is required."}), 400
    if skill_type not in ("offer", "seek"):
        return jsonify({"error": "skill_type must be offer or seek."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({"error": "User not found."}), 404
        cursor.execute(
            """
            SELECT id FROM skill_requests
            WHERE user_id = %s AND skill_name = %s AND skill_type = %s AND status = 'pending'
            """,
            (user_id, skill_name, skill_type),
        )
        if cursor.fetchone():
            return jsonify({"message": "A pending request for this skill already exists."}), 200
        cursor.execute(
            """
            INSERT INTO skill_requests (user_id, skill_name, skill_type)
            VALUES (%s, %s, %s)
            """,
            (user_id, skill_name, skill_type),
        )
        conn.commit()
        return jsonify({"message": "Submitted for admin review.", "request_id": cursor.lastrowid}), 201
    except Error as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.get("/api/skill-requests")
def list_skill_requests():
    user_id = request.args.get("user_id")
    if user_id is None or str(user_id).strip() == "":
        return jsonify({"error": "user_id query parameter is required."}), 400
    try:
        user_id = int(user_id)
    except ValueError:
        return jsonify({"error": "Invalid user_id."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, user_id, skill_name, skill_type, status, admin_note, created_at, reviewed_at
            FROM skill_requests
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
        for r in rows:
            r["created_at"] = iso_dt(r["created_at"])
            r["reviewed_at"] = iso_dt(r["reviewed_at"])
        return jsonify({"requests": rows})
    finally:
        conn.close()


@app.get("/api/admin/skill-requests")
def admin_list_skill_requests():
    status = (request.args.get("status") or "pending").strip().lower()
    if status not in ("pending", "approved", "rejected", "all"):
        status = "pending"

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        if status == "all":
            cursor.execute(
                """
                SELECT sr.id, sr.user_id, u.full_name AS user_name, sr.skill_name, sr.skill_type,
                       sr.status, sr.admin_note, sr.created_at, sr.reviewed_at
                FROM skill_requests sr
                JOIN users u ON u.id = sr.user_id
                ORDER BY sr.status = 'pending' DESC, sr.created_at DESC
                """
            )
        else:
            cursor.execute(
                """
                SELECT sr.id, sr.user_id, u.full_name AS user_name, sr.skill_name, sr.skill_type,
                       sr.status, sr.admin_note, sr.created_at, sr.reviewed_at
                FROM skill_requests sr
                JOIN users u ON u.id = sr.user_id
                WHERE sr.status = %s
                ORDER BY sr.created_at DESC
                """,
                (status,),
            )
        rows = cursor.fetchall()
        for r in rows:
            r["created_at"] = iso_dt(r["created_at"])
            r["reviewed_at"] = iso_dt(r["reviewed_at"])
        return jsonify({"requests": rows})
    finally:
        conn.close()


@app.patch("/api/admin/skill-requests/<int:request_id>")
def admin_review_skill_request(request_id):
    payload = request.get_json(silent=True) or {}
    decision = (payload.get("decision") or "").strip().lower()
    admin_note = (payload.get("admin_note") or "").strip()
    try:
        admin_uid = int(payload.get("admin_user_id")) if payload.get("admin_user_id") else None
    except (TypeError, ValueError):
        admin_uid = None

    if decision not in ("approve", "reject"):
        return jsonify({"error": "decision must be approve or reject."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM skill_requests WHERE id = %s", (request_id,))
        req = cursor.fetchone()
        if not req:
            return jsonify({"error": "Request not found."}), 404
        if req["status"] != "pending":
            return jsonify({"error": "This request was already reviewed."}), 400

        if decision == "reject":
            cursor.execute(
                """
                UPDATE skill_requests
                SET status = 'rejected', admin_note = %s, reviewed_at = NOW(), reviewed_by_user_id = %s
                WHERE id = %s
                """,
                (admin_note or None, admin_uid, request_id),
            )
            conn.commit()
            return jsonify({"message": "Request rejected."})

        cursor.execute(
            "SELECT id FROM user_skills WHERE user_id = %s AND skill_name = %s AND skill_type = %s",
            (req["user_id"], req["skill_name"], req["skill_type"]),
        )
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO user_skills (user_id, skill_name, skill_type) VALUES (%s, %s, %s)",
                (req["user_id"], req["skill_name"], req["skill_type"]),
            )
        cursor.execute(
            """
            UPDATE skill_requests
            SET status = 'approved', admin_note = %s, reviewed_at = NOW(), reviewed_by_user_id = %s
            WHERE id = %s
            """,
            (admin_note or None, admin_uid, request_id),
        )
        conn.commit()
        return jsonify({"message": "Request approved and skill added to profile."})
    except Error as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


# --- Complaints ---


@app.post("/api/complaints")
def create_complaint():
    payload = request.get_json(silent=True) or {}
    try:
        submitted_by = int(payload.get("submitted_by_user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "submitted_by_user_id is required."}), 400
    target_raw = payload.get("target_user_id")
    target_user_id = None
    if target_raw is not None and str(target_raw).strip() != "":
        try:
            target_user_id = int(target_raw)
        except ValueError:
            return jsonify({"error": "Invalid target_user_id."}), 400
    category = (payload.get("category") or "other").strip().lower()
    if category not in ("user_issue", "bug", "system", "other"):
        category = "other"
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()
    if not subject or not body:
        return jsonify({"error": "subject and body are required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE id = %s", (submitted_by,))
        if not cursor.fetchone():
            return jsonify({"error": "User not found."}), 404
        if target_user_id is not None:
            cursor.execute("SELECT id FROM users WHERE id = %s", (target_user_id,))
            if not cursor.fetchone():
                return jsonify({"error": "Target user not found."}), 404
        cursor.execute(
            """
            INSERT INTO complaints (submitted_by_user_id, target_user_id, category, subject, body)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (submitted_by, target_user_id, category, subject, body),
        )
        conn.commit()
        return jsonify({"message": "Complaint submitted.", "id": cursor.lastrowid}), 201
    except Error as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.get("/api/complaints")
def list_complaints_for_user():
    user_id = request.args.get("user_id")
    if user_id is None or str(user_id).strip() == "":
        return jsonify({"error": "user_id is required."}), 400
    try:
        user_id = int(user_id)
    except ValueError:
        return jsonify({"error": "Invalid user_id."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT c.id, c.target_user_id, tu.full_name AS target_user_name,
                   c.category, c.subject, c.body, c.status, c.admin_feedback, c.created_at, c.updated_at
            FROM complaints c
            LEFT JOIN users tu ON tu.id = c.target_user_id
            WHERE c.submitted_by_user_id = %s
            ORDER BY c.created_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
        for r in rows:
            r["created_at"] = iso_dt(r["created_at"])
            r["updated_at"] = iso_dt(r["updated_at"])
        return jsonify({"complaints": rows})
    finally:
        conn.close()


@app.get("/api/admin/complaints")
def admin_list_complaints():
    status = (request.args.get("status") or "all").strip().lower()
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        if status in ("open", "in_progress", "resolved", "closed"):
            cursor.execute(
                """
                SELECT c.id, c.submitted_by_user_id, su.full_name AS submitter_name,
                       c.target_user_id, tu.full_name AS target_user_name,
                       c.category, c.subject, c.body, c.status, c.admin_feedback, c.created_at, c.updated_at
                FROM complaints c
                JOIN users su ON su.id = c.submitted_by_user_id
                LEFT JOIN users tu ON tu.id = c.target_user_id
                WHERE c.status = %s
                ORDER BY c.created_at DESC
                """,
                (status,),
            )
        else:
            cursor.execute(
                """
                SELECT c.id, c.submitted_by_user_id, su.full_name AS submitter_name,
                       c.target_user_id, tu.full_name AS target_user_name,
                       c.category, c.subject, c.body, c.status, c.admin_feedback, c.created_at, c.updated_at
                FROM complaints c
                JOIN users su ON su.id = c.submitted_by_user_id
                LEFT JOIN users tu ON tu.id = c.target_user_id
                ORDER BY c.status = 'open' DESC, c.created_at DESC
                """
            )
        rows = cursor.fetchall()
        for r in rows:
            r["created_at"] = iso_dt(r["created_at"])
            r["updated_at"] = iso_dt(r["updated_at"])
        return jsonify({"complaints": rows})
    finally:
        conn.close()


@app.patch("/api/admin/complaints/<int:complaint_id>")
def admin_update_complaint(complaint_id):
    payload = request.get_json(silent=True) or {}
    status = (payload.get("status") or "").strip().lower()
    admin_feedback = (payload.get("admin_feedback") or "").strip()

    if status and status not in ("open", "in_progress", "resolved", "closed"):
        return jsonify({"error": "Invalid status."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM complaints WHERE id = %s", (complaint_id,))
        if not cursor.fetchone():
            return jsonify({"error": "Complaint not found."}), 404
        if status and admin_feedback != "":
            cursor.execute(
                """
                UPDATE complaints SET status = %s, admin_feedback = %s WHERE id = %s
                """,
                (status, admin_feedback, complaint_id),
            )
        elif status:
            cursor.execute("UPDATE complaints SET status = %s WHERE id = %s", (status, complaint_id))
        elif admin_feedback != "":
            cursor.execute(
                "UPDATE complaints SET admin_feedback = %s WHERE id = %s",
                (admin_feedback, complaint_id),
            )
        else:
            return jsonify({"error": "Provide status and/or admin_feedback."}), 400
        conn.commit()
        return jsonify({"message": "Complaint updated."})
    finally:
        conn.close()


# --- Direct messages ---


@app.post("/api/messages")
def send_message():
    payload = request.get_json(silent=True) or {}
    try:
        from_uid = int(payload.get("from_user_id"))
        to_uid = int(payload.get("to_user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "from_user_id and to_user_id are required."}), 400
    body = (payload.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body is required."}), 400
    if len(body) > 2000:
        return jsonify({"error": "Message too long (max 2000 characters)."}), 400
    if from_uid == to_uid:
        return jsonify({"error": "Cannot message yourself."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE id IN (%s, %s)", (from_uid, to_uid))
        if len(cursor.fetchall()) != 2:
            return jsonify({"error": "User not found."}), 404
        cursor.execute(
            "INSERT INTO messages (from_user_id, to_user_id, body) VALUES (%s, %s, %s)",
            (from_uid, to_uid, body),
        )
        conn.commit()
        return jsonify({"message": "Sent.", "id": cursor.lastrowid}), 201
    except Error as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.get("/api/messages/thread")
def message_thread():
    try:
        user_id = int(request.args.get("user_id"))
        with_uid = int(request.args.get("with_user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "user_id and with_user_id are required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT m.id, m.from_user_id, m.to_user_id, m.body, m.created_at,
                   uf.full_name AS from_name, ut.full_name AS to_name
            FROM messages m
            JOIN users uf ON uf.id = m.from_user_id
            JOIN users ut ON ut.id = m.to_user_id
            WHERE (m.from_user_id = %s AND m.to_user_id = %s)
               OR (m.from_user_id = %s AND m.to_user_id = %s)
            ORDER BY m.created_at ASC
            """,
            (user_id, with_uid, with_uid, user_id),
        )
        rows = cursor.fetchall()
        for r in rows:
            r["created_at"] = iso_dt(r["created_at"])
        return jsonify({"messages": rows})
    finally:
        conn.close()


@app.get("/api/messages/inbox")
def message_inbox():
    try:
        user_id = int(request.args.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "user_id is required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT m.id, m.from_user_id, m.to_user_id, m.body, m.created_at,
                   uf.full_name AS from_name, ut.full_name AS to_name
            FROM messages m
            JOIN users uf ON uf.id = m.from_user_id
            JOIN users ut ON ut.id = m.to_user_id
            WHERE m.from_user_id = %s OR m.to_user_id = %s
            ORDER BY m.created_at DESC
            LIMIT 300
            """,
            (user_id, user_id),
        )
        rows = cursor.fetchall()
        seen = {}
        for r in rows:
            pid = r["to_user_id"] if r["from_user_id"] == user_id else r["from_user_id"]
            if pid in seen:
                continue
            pname = r["to_name"] if r["from_user_id"] == user_id else r["from_name"]
            seen[pid] = {
                "partner_id": pid,
                "partner_name": pname,
                "last_body": r["body"],
                "last_at": iso_dt(r["created_at"]),
            }
        return jsonify({"conversations": list(seen.values())})
    finally:
        conn.close()


# --- Certificates (student requests → teacher confirms → download) ---


@app.post("/api/certificates/request")
def request_certificate():
    payload = request.get_json(silent=True) or {}
    try:
        student_user_id = int(payload.get("student_user_id"))
        transaction_id = int(payload.get("transaction_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "student_user_id and transaction_id are required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, requester_user_id, provider_user_id, skill_name, status
            FROM transactions WHERE id = %s
            """,
            (transaction_id,),
        )
        tx = cursor.fetchone()
        if not tx:
            return jsonify({"error": "Transaction not found."}), 404
        if tx["status"] != "completed":
            return jsonify({"error": "Certificate is only available after the session is completed."}), 400
        if tx["requester_user_id"] != student_user_id:
            return jsonify({"error": "Only the student (requester) can request a certificate for this session."}), 403
        teacher_id = tx["provider_user_id"]
        cursor.execute(
            "SELECT id FROM certificate_requests WHERE transaction_id = %s AND student_user_id = %s",
            (transaction_id, student_user_id),
        )
        if cursor.fetchone():
            return jsonify({"message": "Certificate request already exists."}), 200
        cursor.execute(
            """
            INSERT INTO certificate_requests
            (transaction_id, student_user_id, teacher_user_id, skill_name)
            VALUES (%s, %s, %s, %s)
            """,
            (transaction_id, student_user_id, teacher_id, tx["skill_name"]),
        )
        conn.commit()
        return jsonify({"message": "Request sent to teacher for confirmation.", "id": cursor.lastrowid}), 201
    except Error as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.get("/api/certificates/my")
def list_my_certificates():
    try:
        user_id = int(request.args.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "user_id is required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT cr.id, cr.transaction_id, cr.skill_name, cr.status, cr.teacher_note,
                   cr.requested_at, cr.responded_at, cr.issued_at,
                   cr.student_user_id, cr.teacher_user_id,
                   su.full_name AS student_name, tu.full_name AS teacher_name
            FROM certificate_requests cr
            JOIN users su ON su.id = cr.student_user_id
            JOIN users tu ON tu.id = cr.teacher_user_id
            WHERE cr.student_user_id = %s OR cr.teacher_user_id = %s
            ORDER BY cr.requested_at DESC
            """,
            (user_id, user_id),
        )
        rows = cursor.fetchall()
        for r in rows:
            r["requested_at"] = iso_dt(r["requested_at"])
            r["responded_at"] = iso_dt(r["responded_at"])
            r["issued_at"] = iso_dt(r["issued_at"])
        return jsonify({"certificates": rows})
    finally:
        conn.close()


@app.patch("/api/certificates/<int:cert_id>/teacher-respond")
def teacher_respond_certificate(cert_id):
    payload = request.get_json(silent=True) or {}
    try:
        teacher_user_id = int(payload.get("teacher_user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "teacher_user_id is required."}), 400
    decision = (payload.get("decision") or "").strip().lower()
    teacher_note = (payload.get("teacher_note") or "").strip()
    if decision not in ("approve", "reject"):
        return jsonify({"error": "decision must be approve or reject."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM certificate_requests WHERE id = %s", (cert_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Certificate request not found."}), 404
        if row["teacher_user_id"] != teacher_user_id:
            return jsonify({"error": "Only the teacher for this session can confirm."}), 403
        if row["status"] != "pending":
            return jsonify({"error": "This request was already handled."}), 400

        if decision == "reject":
            cursor.execute(
                """
                UPDATE certificate_requests
                SET status='rejected', teacher_note=%s, responded_at=NOW()
                WHERE id=%s
                """,
                (teacher_note or None, cert_id),
            )
        else:
            cursor.execute(
                """
                UPDATE certificate_requests
                SET status='approved', teacher_note=%s, responded_at=NOW(), issued_at=NOW()
                WHERE id=%s
                """,
                (teacher_note or None, cert_id),
            )
        conn.commit()
        return jsonify({"message": "Updated."})
    finally:
        conn.close()


@app.get("/api/certificates/<int:cert_id>/download")
def download_certificate_document(cert_id):
    doc_type = (request.args.get("type") or "certificate").strip().lower()
    if doc_type not in ("certificate", "transcript"):
        doc_type = "certificate"
    try:
        user_id = int(request.args.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "user_id is required."}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT cr.*, su.full_name AS student_name, tu.full_name AS teacher_name
            FROM certificate_requests cr
            JOIN users su ON su.id = cr.student_user_id
            JOIN users tu ON tu.id = cr.teacher_user_id
            WHERE cr.id = %s
            """,
            (cert_id,),
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Not found."}), 404
        if row["status"] != "approved":
            return jsonify({"error": "Certificate is not issued yet (teacher must approve)."}), 403
        if user_id not in (row["student_user_id"], row["teacher_user_id"]):
            return jsonify({"error": "Not allowed."}), 403

        stu = html.escape(row["student_name"] or "")
        tea = html.escape(row["teacher_name"] or "")
        skill = html.escape(row["skill_name"] or "")
        issued = html.escape(str(iso_dt(row["issued_at"]) or ""))

        txid = row.get("transaction_id")
        if doc_type == "transcript":
            title = "Session transcript"
            body_extra = f"<p><strong>Session / course:</strong> {skill}</p><p><strong>Transaction reference:</strong> {html.escape(str(txid))}</p>"
        else:
            title = "Certificate of completion"
            body_extra = f"<p>This certifies that the learner named below successfully completed a SkillSwap time-banking session.</p><p><strong>Course / skill:</strong> {skill}</p>"

        html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: Georgia, serif; margin: 40px; color: #111; }}
.brand {{ color: #4a67e9; font-weight: 800; font-size: 14px; letter-spacing: 0.08em; }}
h1 {{ font-size: 22px; margin: 24px 0 8px; }}
.student {{ font-size: 36px; font-weight: 800; margin: 16px 0; color: #1a195d; border-bottom: 3px solid #1a195d; display: inline-block; padding-bottom: 6px; }}
.meta {{ margin-top: 20px; font-size: 15px; line-height: 1.7; }}
.footer {{ margin-top: 40px; font-size: 13px; color: #555; }}
</style></head><body>
<div class="brand">SKILLSWAP · TIME BANKING</div>
<h1>{html.escape(title)}</h1>
{body_extra}
<p class="meta"><strong>Student (primary):</strong></p>
<div class="student">{stu}</div>
<p class="meta"><strong>Instructor:</strong> {tea}</p>
<p class="meta"><strong>Issued at:</strong> {issued}</p>
<div class="footer">SkillSwap — community skill exchange. This document was generated electronically after instructor confirmation.</div>
</body></html>"""
        fname = f"skillswap-{doc_type}-{cert_id}.html"
        return Response(
            html_doc,
            mimetype="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    finally:
        conn.close()


# Static HTML last so /api/* is never handled by the catch-all file route.
@app.get("/")
def serve_root():
    return send_from_directory(FRONTEND_ROOT, "login.html")


@app.get("/<path:filename>")
def serve_frontend_files(filename):
    if filename.startswith("api/"):
        abort(404)
    return send_from_directory(FRONTEND_ROOT, filename)


ensure_optional_tables()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
