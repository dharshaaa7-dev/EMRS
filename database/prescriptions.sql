CREATE TABLE prescriptions (
    prescription_id INT AUTO_INCREMENT PRIMARY KEY,

    patient_id INT NOT NULL,
    doctor_id INT NOT NULL,
    appointment_id INT,

    medicine_name VARCHAR(150) NOT NULL,
    dosage VARCHAR(100),
    frequency VARCHAR(100),
    duration VARCHAR(100),

    instructions TEXT,

    prescribed_date DATE DEFAULT (CURRENT_DATE),

    FOREIGN KEY (patient_id) REFERENCES patient(patient_id),
    FOREIGN KEY (doctor_id) REFERENCES doctor(doctor_id),
    FOREIGN KEY (appointment_id) REFERENCES appointment(appointment_id)
);