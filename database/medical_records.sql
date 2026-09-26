CREATE TABLE medical_records (
    record_id INT AUTO_INCREMENT PRIMARY KEY,

    appointment_id INT NOT NULL,
    patient_id INT NOT NULL,
    doctor_id INT NOT NULL,

    diagnosis VARCHAR(255) NOT NULL,
    symptoms TEXT,
    treatment TEXT,
    doctor_notes TEXT,

    visit_date DATE NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (appointment_id)
        REFERENCES appointment(appointment_id)
        ON DELETE CASCADE,

    FOREIGN KEY (patient_id)
        REFERENCES patient(patient_id)
        ON DELETE CASCADE,

    FOREIGN KEY (doctor_id)
        REFERENCES doctor(doctor_id)
        ON DELETE CASCADE
);