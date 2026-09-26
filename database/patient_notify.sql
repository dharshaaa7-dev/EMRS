CREATE TABLE notifications (

    notification_id INT AUTO_INCREMENT PRIMARY KEY,

    patient_id INT,

    title VARCHAR(200),
    message TEXT,

    status VARCHAR(20) DEFAULT 'Unread',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (patient_id) REFERENCES patient(patient_id)

);