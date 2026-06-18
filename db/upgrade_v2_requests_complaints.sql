-- Run if you already created skillswap_db without these tables:
USE skillswap_db;

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

CREATE INDEX idx_skill_requests_status ON skill_requests(status, created_at);
CREATE INDEX idx_complaints_status ON complaints(status, created_at);
