CREATE TABLE lab_reports (
    report_id INT AUTO_INCREMENT PRIMARY KEY,

    patient_name VARCHAR(100) NOT NULL,
    patient_id INT NOT NULL,
    patient_number VARCHAR(20) NOT NULL,

    visit_date DATE NOT NULL,

    test_name VARCHAR(150) NOT NULL,
    test_code VARCHAR(50) NOT NULL,

    laboratory_staff_name VARCHAR(100) NOT NULL,

    clinic_name VARCHAR(150) NOT NULL,
    clinic_number VARCHAR(20) NOT NULL,

    report_pdf VARCHAR(255) DEFAULT NULL
);