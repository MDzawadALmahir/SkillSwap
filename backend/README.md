# SkillSwap Backend (Flask + MySQL)

## 1) Create database

Run in MySQL:

```sql
SOURCE path/to/db/skillswap_init.sql;
```

If the database already exists from an older export, also run (in order as needed):

```sql
SOURCE path/to/db/upgrade_v2_requests_complaints.sql;
SOURCE path/to/db/upgrade_v4_messages_certificates.sql;
```

`upgrade_v4_messages_certificates.sql` adds the `messages` and `certificate_requests` tables used by member messaging and certificate flows.

## 2) Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

## 3) Set environment variables

Copy `.env.example` values into your environment (or set manually):

- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `ADMIN_SIGNUP_SECRET` — required value when signing up with `role: admin` (default in code is `change-this-secret` if unset)

## 4) Start API

```bash
python app.py
```

Server runs at `http://127.0.0.1:5000`.

On import/startup the app runs **`ensure_optional_tables()`**, which creates **`complaints`**, **`messages`**, and **`certificate_requests`** if they are missing (common when the database was created from an older script). You can still apply migrations manually with `db/ensure_complaints_messages_certificates.sql` if you prefer not to rely on that.

## Endpoints

### Auth
- `POST /api/auth/signup` — body: `name`, `email`, `phone`, `password`, optional `role` (`member`|`admin`), and `admin_secret` if `role` is `admin`
- `POST /api/auth/login` — body: `identifier` (email or phone), `password`

### Users
- `GET /api/users?search=...`
- `GET /api/users/<id>`
- `PATCH /api/users/<id>/profile` — body: `full_name`
- `PATCH /api/users/<id>/status` — body: `status` (`active`|`locked`|`at_risk`)
- `DELETE /api/users/<id>` — body: `password` (confirms delete)
- `POST /api/users/<id>/skills` — body: `skill_name`, `skill_type` (`offer`|`seek`)
- `DELETE /api/users/<id>/skills?skill_name=...&skill_type=offer|seek`

### Transactions
- `GET /api/transactions?status=...&search=...&involving_user_id=<id>` — optional filter for rows where the user is requester or provider
- `GET /api/transactions/stats`
- `POST /api/transactions` — body: `requester_user_id`, `provider_user_id`, `skill_name`, `credit_hours`, optional `notes` (skill must exist on provider as an `offer`)
- `PATCH /api/transactions/<id>/status` — body: `status` (`pending`|`completed`|`declined`)

### Skill requests (custom skills — admin approval)
- `POST /api/skill-requests` — body: `user_id`, `skill_name`, `skill_type` (`offer`|`seek`)
- `GET /api/skill-requests?user_id=<id>` — list for that member
- `GET /api/admin/skill-requests?status=pending|approved|rejected|all`
- `PATCH /api/admin/skill-requests/<id>` — body: `decision` (`approve`|`reject`), optional `admin_note`, optional `admin_user_id`

### Complaints
- `POST /api/complaints` — body: `submitted_by_user_id`, `subject`, `body`, `category` (`user_issue`|`bug`|`system`|`other`), optional `target_user_id`
- `GET /api/complaints?user_id=<id>` — submitter’s tickets (includes `admin_feedback`)
- `GET /api/admin/complaints?status=open|...|all`
- `PATCH /api/admin/complaints/<id>` — body: `status` and/or `admin_feedback`

### Messages (member-to-member)
- `POST /api/messages` — body: `from_user_id`, `to_user_id`, `body`
- `GET /api/messages/inbox?user_id=<id>` — conversation list with last message preview
- `GET /api/messages/thread?user_id=<id>&with_user_id=<other>` — ordered messages between two users

### Certificates / transcripts (completed session only)
- `POST /api/certificates/request` — body: `student_user_id`, `transaction_id` (student must be the requester on a **completed** transaction; one pending/approved request per transaction per student)
- `GET /api/certificates/my?user_id=<id>` — rows where the user is student or teacher
- `PATCH /api/certificates/<id>/teacher-respond` — body: `teacher_user_id`, `decision` (`approve`|`reject`), optional `teacher_note`
- `GET /api/certificates/<id>/download?type=certificate|transcript&user_id=<id>` — HTML download (student only when **approved**)

## Login validation

Login identifier must be a valid email or phone (10–15 digits, optional `+`), and the password must match the hash stored in MySQL.
