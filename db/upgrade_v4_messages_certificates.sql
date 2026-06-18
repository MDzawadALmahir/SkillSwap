USE skillswap_db;

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
