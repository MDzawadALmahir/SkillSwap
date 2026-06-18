CREATE DATABASE IF NOT EXISTS skillswap_db;
USE skillswap_db;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(120) NOT NULL,
    email VARCHAR(150) NOT NULL UNIQUE,
    phone VARCHAR(20) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('member', 'admin') NOT NULL DEFAULT 'member',
    reliability_score DECIMAL(5,2) NOT NULL DEFAULT 100.00,
    credits INT NOT NULL DEFAULT 0,  -- new members receive 3 credits at signup (see backend/app.py)
    account_status ENUM('active', 'locked', 'at_risk') NOT NULL DEFAULT 'active',
    last_active DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    skill_name VARCHAR(100) NOT NULL,
    skill_type ENUM('offer', 'seek') NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_skills_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY uq_user_skill (user_id, skill_name, skill_type)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    requester_user_id INT NOT NULL,
    provider_user_id INT NOT NULL,
    skill_name VARCHAR(100) NOT NULL,
    status ENUM('pending', 'completed', 'declined') NOT NULL DEFAULT 'pending',
    credit_hours DECIMAL(6,2) NOT NULL DEFAULT 1.00,
    notes VARCHAR(255) NULL,
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    CONSTRAINT fk_transactions_requester FOREIGN KEY (requester_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_transactions_provider FOREIGN KEY (provider_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS skill_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    skill_name VARCHAR(100) NOT NULL,
    skill_type ENUM('offer', 'seek') NOT NULL,
    status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
    admin_note VARCHAR(500) NULL,
    reviewed_by_user_id INT NULL,
    reviewed_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_skill_req_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_skill_req_reviewer FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

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
    CONSTRAINT fk_complaint_submitter FOREIGN KEY (submitted_by_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_complaint_target FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_users_name_email ON users(full_name, email);
CREATE INDEX idx_transactions_status_requested ON transactions(status, requested_at);
CREATE INDEX idx_transactions_skill ON transactions(skill_name);
CREATE INDEX idx_skill_requests_status ON skill_requests(status, created_at);
CREATE INDEX idx_complaints_status ON complaints(status, created_at);

CREATE TABLE IF NOT EXISTS messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    from_user_id INT NOT NULL,
    to_user_id INT NOT NULL,
    body VARCHAR(2000) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_msg_from FOREIGN KEY (from_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_msg_to FOREIGN KEY (to_user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_msg_pair_time (from_user_id, to_user_id, created_at)
);

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
    CONSTRAINT fk_cert_teacher FOREIGN KEY (teacher_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_messages_to ON messages(to_user_id, created_at);
CREATE INDEX idx_cert_teacher_status ON certificate_requests(teacher_user_id, status);

-- Optional example admin account:
-- Password hash below corresponds to: Admin@123
-- INSERT INTO users (full_name, email, phone, password_hash, role, credits, reliability_score)
-- VALUES ('Admin User', 'admin@skillswap.com', '+8801700000000',
-- '$pbkdf2-sha256$29000$R8j5P2fMOWcsRWSM0dp77w$g9VaoXH95jK1hgs74SCprX5ZTLxq/KktlJ2UZYvF2lk',
-- 'admin', 100, 100.00);
