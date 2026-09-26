from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
import mysql.connector
import random
import re
import os
from reportlab.pdfgen import canvas
import io
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
from deep_translator import GoogleTranslator   # <-- AI FEATURE: Tamil -> English translation

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = "emrs_secret_key_2026"

print("=" * 60)
print("Flask app root path :", app.root_path)
print("Flask STATIC folder (put css/js/images here) :", app.static_folder)
print("CSS should be at :", os.path.join(app.static_folder, "css", "admin_dashboard.css"))
print("That file exists? :", os.path.isfile(os.path.join(app.static_folder, "css", "admin_dashboard.css")))
print("=" * 60)

# ================= MAIL CONFIGURATION ================= #
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = "dharshaaa7@gmail.com"

# 16-character Google App Password (NO SPACES)
app.config["MAIL_PASSWORD"] = "auroxawuhozzqsnm"

print("MAIL_USERNAME =", app.config["MAIL_USERNAME"])

if app.config["MAIL_PASSWORD"]:
    print("MAIL_PASSWORD Loaded Successfully")
else:
    print("MAIL_PASSWORD Not Found")

mail = Mail(app)

# ================= DATABASE CONNECTION ================= #
db = mysql.connector.connect(
    host="emrs-project-emrs-project.j.aivencloud.com",
    port=19848,
    user="avnadmin",
    password="AVNS__VIr2odYDjuaKrICSSh",
    database="defaultdb",
    ssl_ca="ca.pem",
    connection_timeout=30,
    autocommit=True
)

cursor = db.cursor(dictionary=True)

# ================= KEEP DB CONNECTION ALIVE ================= #
# Aiven (and most cloud MySQL) closes idle connections after a while.
# Since this app uses one global connection, we ping/reconnect it
# before every request so long-idle sessions don't crash with
# "Lost connection to MySQL server during query".

@app.before_request
def ensure_db_connection():
    # Static files (css, js, images, fonts) don't need the database at
    # all — running the DB ping/reconnect check for them just adds
    # delay and can even make them fail if the DB ping is slow.
    if request.endpoint == "static":
        return

    global db, cursor
    try:
        db.ping(reconnect=True, attempts=3, delay=2)
    except mysql.connector.Error as e:
        print("DB Reconnect Failed, creating new connection :", e)
        db = mysql.connector.connect(
            host="emrs-project-emrs-project.j.aivencloud.com",
            port=19848,
            user="avnadmin",
            password="AVNS__VIr2odYDjuaKrICSSh",
            database="defaultdb",
            ssl_ca="ca.pem",
            connection_timeout=30,
            autocommit=True
        )
    cursor = db.cursor(dictionary=True)

# ================= LAB REPORT PDF UPLOAD CONFIG ================= #
UPLOAD_FOLDER = os.path.join("static", "uploads", "lab_reports")
ALLOWED_EXTENSIONS = {"pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ================= ADMIN PROFILE PICTURE UPLOAD CONFIG ================= #
PROFILE_UPLOAD_FOLDER = os.path.join("static", "uploads", "profile_pictures")
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}
os.makedirs(PROFILE_UPLOAD_FOLDER, exist_ok=True)


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


# ================= HOME PAGE ================= #

@app.route("/")
def home():
    return render_template("index.html")

# ================= LOGIN PAGE ================= #

@app.route("/login")
def login():
    return render_template("login.html")

# ================= PATIENT LOGIN ================= #

@app.route("/patient_login", methods=["GET", "POST"])
def patient_login():

    if request.method == "POST":

        login_id = request.form["login_id"]
        password = request.form["password"]

        cursor.execute("""
            SELECT *
            FROM patient
            WHERE (email=%s OR username=%s OR mobile=%s)
            AND password=%s
        """, (login_id, login_id, login_id, password))

        patient = cursor.fetchone()

        if patient:

            session["patient_id"] = patient["patient_id"]
            session["username"] = patient["username"]

            return redirect(url_for("patient_dashboard"))

        else:
            return render_template(
                "wrong_mail.html",
                role="patient_login"
            )

    return render_template("patient_login.html")

# ================= DOCTOR login ~================= #

@app.route("/doctor_login", methods=["GET", "POST"])
def doctor_login():

    if request.method == "POST":

        login_id = request.form["login_id"]
        password = request.form["password"]

        cursor.execute("""
            SELECT * FROM doctor
            WHERE (email=%s OR username=%s)
            AND password=%s
        """, (login_id, login_id, password))

        doctor = cursor.fetchone()

        if doctor:

            session["doctor_id"] = doctor["doctor_id"]
            session["doctor_name"] = doctor["full_name"]
            session["doctor_photo"] = doctor.get("profile_picture")

            return redirect(url_for("doctor_dashboard"))

        else:
            return render_template(
                "wrong_mail.html",
                role="doctor_login"
            )

    return render_template("doctor_login.html")

# ================= DOCTOR REGISTER ================= #

@app.route("/doctor_register", methods=["GET", "POST"])
def doctor_register():

    if request.method == "POST":
        doctor_id = request.form.get("doctor_id")
        print("Selected Doctor ID :", doctor_id)

        print(request.form)

        full_name = request.form["full_name"]
        username = request.form["username"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        specialization = request.form["specialization"]
        qualification = request.form["qualification"]
        license_no = request.form["license_no"]
        hospital_name = request.form["hospital_name"]
        hospital_city = request.form["hospital_city"]
        hospital_district = request.form["hospital_district"]
        hospital_state = request.form["hospital_state"]

        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            return "❌ Passwords do not match"

        cursor.execute(
            "SELECT * FROM doctor WHERE email=%s",
            (email,)
        )

        if cursor.fetchone():
            return "❌ Email already registered"

        cursor.execute(
            "SELECT * FROM doctor WHERE username=%s",
            (username,)
        )

        if cursor.fetchone():
            return "❌ Username already exists"

        sql = """
        INSERT INTO doctor
        (
            full_name,
            username,
            email,
            mobile,
            specialization,
            qualification,
            license_no,
            hospital_name,
            hospital_city,
            hospital_district,
            hospital_state,
            password
        )
        VALUES
        (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
        """

        values = (
            full_name,
            username,
            email,
            mobile,
            specialization,
            qualification,
            license_no,
            hospital_name,
            hospital_city,
            hospital_district,
            hospital_state,
            password
        )

        cursor.execute(sql, values)
        db.commit()

        doctor_id = cursor.lastrowid

        session["doctor_id"] = doctor_id
        session["doctor_name"] = full_name

        return redirect(url_for("doctor_dashboard"))

    return render_template("doctor_register.html")

#============doctor dashboard==============#
@app.route("/doctor_dashboard")
def doctor_dashboard():

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    doctor_id = session["doctor_id"]
    print("Logged Doctor ID :", doctor_id)

    cursor.execute("""
        SELECT doctor_id, full_name, hospital_name, profile_picture
        FROM doctor
        WHERE doctor_id=%s
    """, (doctor_id,))

    doctor = cursor.fetchone()
    print("Doctor Details :", doctor)

    if doctor is None:
        session.clear()
        return redirect(url_for("doctor_login"))

    session["doctor_photo"] = doctor.get("profile_picture")

    hospital_name = doctor["hospital_name"]
    print("Hospital :", hospital_name)

    cursor.execute("""
        SELECT
            a.appointment_id,
            a.patient_id,
            p.full_name AS patient_name,
            a.appointment_date,
            a.appointment_time,
            a.reason,
            a.status,
            a.hospital_name
        FROM appointment a
        INNER JOIN patient p
            ON a.patient_id = p.patient_id
        WHERE a.doctor_id=%s
        ORDER BY
            a.appointment_date ASC,
            a.appointment_time ASC
    """, (doctor_id,))

    appointments = cursor.fetchall()
    print("Appointments :", appointments)

    total_patients = len(set(a["patient_id"] for a in appointments))
    pending_count = sum(1 for a in appointments if a["status"] == "Pending")
    approved_count = sum(1 for a in appointments if a["status"] == "Approved")
    rejected_count = sum(1 for a in appointments if a["status"] == "Rejected")

    return render_template(
        "doctor_dashboard.html",
        doctor=doctor,
        appointments=appointments,
        total_patients=total_patients,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count
    )

# ================= DOCTOR: MEDICAL RECORDS SEARCH (LIST) ================= #
# Sidebar "Medical Records" button -> search this doctor's own
# appointments by Patient ID / Name, then click "Open Record" to go to
# add_medical_record for that appointment (existing route further down).

@app.route('/doctor/medical-records', methods=['GET'])
def doctor_md_records():

    if 'doctor_id' not in session:
        return redirect(url_for('doctor_login'))

    doctor_id = session['doctor_id']

    search_id = request.args.get('search_id', '').strip()
    search_name = request.args.get('search_name', '').strip()

    query = """
        SELECT
            a.appointment_id,
            a.patient_id,
            p.full_name AS patient_name,
            a.appointment_date,
            a.appointment_time,
            a.status
        FROM appointment a
        JOIN patient p ON a.patient_id = p.patient_id
        WHERE a.doctor_id=%s
    """
    params = [doctor_id]

    if search_id:
        query += " AND a.patient_id=%s"
        params.append(search_id)

    if search_name:
        query += " AND p.full_name LIKE %s"
        params.append(f"%{search_name}%")

    query += " ORDER BY a.appointment_date DESC"

    cursor.execute(query, tuple(params))
    records = cursor.fetchall()

    return render_template(
        'doctor_md_records.html',
        records=records,
        search_id=search_id,
        search_name=search_name
    )

# ================= DOCTOR: PRESCRIPTIONS (SEARCH / ADD / EDIT) ================= #
# Sidebar "Prescriptions" button -> search patient by ID + Name (same
# pattern as doctor_patient) -> show that patient's prescription history
# written by this doctor, add a new prescription, or edit an existing one.
#
# NOTE: run this once if the column doesn't exist yet (stores checked
# timing options like "Morning, Night" as a comma-separated string):
#   ALTER TABLE prescriptions ADD COLUMN timing VARCHAR(50) NULL;

@app.route("/doctor_prescription", methods=["GET", "POST"])
def doctor_prescription():

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    doctor_id = session["doctor_id"]

    patient = None
    prescriptions = []

    if request.method == "POST":

        action = request.form.get("action", "search")

        if action == "search":

            patient_id = request.form["patient_id"].strip()
            patient_name = request.form["patient_name"].strip()

            cursor.execute("""
                SELECT * FROM patient
                WHERE patient_id=%s AND full_name=%s
            """, (patient_id, patient_name))

            patient = cursor.fetchone()

            if not patient:
                flash("Patient Not Found!", "danger")

        elif action == "add_prescription":

            patient_id = request.form["patient_id"]

            cursor.execute("SELECT * FROM patient WHERE patient_id=%s", (patient_id,))
            patient = cursor.fetchone()

            if patient:

                medicine_name = request.form["medicine_name"]
                dosage = request.form["dosage"]
                frequency = request.form["frequency"]
                duration = request.form["duration"]
                instructions = request.form["instructions"]
                timing = ", ".join(request.form.getlist("timing"))

                cursor.execute("""
                    INSERT INTO prescriptions
                    (patient_id, doctor_id, medicine_name, dosage, frequency, timing, duration, instructions, prescribed_date)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s, CURDATE())
                """, (patient_id, doctor_id, medicine_name, dosage, frequency, timing, duration, instructions))

                db.commit()

                flash("Prescription Added Successfully!", "success")

        elif action == "edit_prescription":

            patient_id = request.form["patient_id"]

            cursor.execute("SELECT * FROM patient WHERE patient_id=%s", (patient_id,))
            patient = cursor.fetchone()

            if patient:

                prescription_id = request.form["prescription_id"]
                medicine_name = request.form["medicine_name"]
                dosage = request.form["dosage"]
                frequency = request.form["frequency"]
                duration = request.form["duration"]
                instructions = request.form["instructions"]
                timing = ", ".join(request.form.getlist("timing"))

                cursor.execute("""
                    UPDATE prescriptions
                    SET medicine_name=%s, dosage=%s, frequency=%s, timing=%s, duration=%s, instructions=%s
                    WHERE prescription_id=%s AND doctor_id=%s
                """, (medicine_name, dosage, frequency, timing, duration, instructions, prescription_id, doctor_id))

                db.commit()

                flash("Prescription Updated Successfully!", "success")

        if patient:

            cursor.execute("""
                SELECT * FROM prescriptions
                WHERE patient_id=%s AND doctor_id=%s
                ORDER BY prescribed_date DESC
            """, (patient["patient_id"], doctor_id))

            prescriptions = cursor.fetchall()

    return render_template(
        "doctor_prescription.html",
        patient=patient,
        prescriptions=prescriptions
    )


# ================= DOCTOR SETTINGS (VIEW / EDIT PROFILE + PICTURE) ================= #
# Sidebar "Settings" button -> edit the doctor's own profile fields and
# upload/replace a profile picture (same pattern as admin_settings /
# patient_settings). Uses the shared PROFILE_UPLOAD_FOLDER +
# allowed_image() already defined near the top of app.py.
#
# NOTE: run this once if the column doesn't exist yet:
#   ALTER TABLE doctor ADD COLUMN profile_picture VARCHAR(255) NULL;

@app.route("/doctor_settings", methods=["GET", "POST"])
def doctor_settings():

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    doctor_id = session["doctor_id"]

    if request.method == "POST":

        full_name = request.form["full_name"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        specialization = request.form["specialization"]
        qualification = request.form["qualification"]
        license_no = request.form["license_no"]
        hospital_name = request.form["hospital_name"]
        hospital_city = request.form["hospital_city"]
        hospital_district = request.form["hospital_district"]
        hospital_state = request.form["hospital_state"]

        cursor.execute(
            "SELECT profile_picture FROM doctor WHERE doctor_id=%s",
            (doctor_id,)
        )
        current = cursor.fetchone()
        profile_picture = current["profile_picture"] if current else None

        file = request.files.get("profile_picture")

        if file and file.filename != "":
            if allowed_image(file.filename):
                filename = secure_filename(f"doctor_{doctor_id}_{file.filename}")
                file_path = os.path.join(PROFILE_UPLOAD_FOLDER, filename)
                file.save(file_path)
                profile_picture = f"uploads/profile_pictures/{filename}"
            else:
                flash("Only PNG / JPG / JPEG images are allowed for the profile picture.", "danger")

        cursor.execute("""
            UPDATE doctor
            SET
                full_name=%s,
                email=%s,
                mobile=%s,
                specialization=%s,
                qualification=%s,
                license_no=%s,
                hospital_name=%s,
                hospital_city=%s,
                hospital_district=%s,
                hospital_state=%s,
                profile_picture=%s
            WHERE doctor_id=%s
        """, (
            full_name,
            email,
            mobile,
            specialization,
            qualification,
            license_no,
            hospital_name,
            hospital_city,
            hospital_district,
            hospital_state,
            profile_picture,
            doctor_id
        ))

        db.commit()

        session["doctor_name"] = full_name
        session["doctor_photo"] = profile_picture

        flash("Profile Updated Successfully!", "success")

        return redirect(url_for("doctor_settings"))

    cursor.execute(
        "SELECT * FROM doctor WHERE doctor_id=%s",
        (doctor_id,)
    )
    doctor = cursor.fetchone()

    return render_template(
        "doctor_settings.html",
        doctor=doctor
    )

# ================= APPROVE APPOINTMENT ================= #
@app.route("/approve/<int:id>")
def approve(id):

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    cursor.execute("""
        UPDATE appointment
        SET status='Approved'
        WHERE appointment_id=%s
    """, (id,))

    db.commit()

    return redirect(request.referrer or url_for("doctor_dashboard"))


# ================= REJECT APPOINTMENT ================= #
@app.route("/reject/<int:id>")
def reject(id):

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    cursor.execute("""
        UPDATE appointment
        SET status='Rejected'
        WHERE appointment_id=%s
    """, (id,))

    db.commit()

    return redirect(request.referrer or url_for("doctor_dashboard"))

# ================= DOCTOR LOGOUT ================= #

@app.route("/doctor_logout")
def doctor_logout():

    session.pop("doctor_id", None)
    session.pop("doctor_name", None)

    return redirect(url_for("doctor_login"))

# ================= DOCTOR: ALL APPOINTMENTS ================= #
# Sidebar "Appointments" button -> a dedicated page listing every
# appointment for this doctor with Approve / Reject actions
# (same actions as the dashboard table, just on their own page).

@app.route("/doctor_appointments")
def doctor_appointments():

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    doctor_id = session["doctor_id"]

    cursor.execute("""
        SELECT
            a.appointment_id,
            a.patient_id,
            p.full_name AS patient_name,
            a.appointment_date,
            a.appointment_time,
            a.reason,
            a.status,
            a.hospital_name
        FROM appointment a
        JOIN patient p
            ON a.patient_id = p.patient_id
        WHERE a.doctor_id=%s
        ORDER BY
            a.appointment_date DESC,
            a.appointment_time DESC
    """, (doctor_id,))

    appointments = cursor.fetchall()

    total_appointments = len(appointments)
    pending_count = sum(1 for a in appointments if a["status"] == "Pending")
    approved_count = sum(1 for a in appointments if a["status"] == "Approved")
    rejected_count = sum(1 for a in appointments if a["status"] == "Rejected")

    return render_template(
        "doctor_appointments.html",
        appointments=appointments,
        total_appointments=total_appointments,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count
    )

# ================= DOCTOR: PATIENT LOOKUP (VIEW ONLY) ================= #
# Sidebar "Patients" button -> search by Patient ID + Name (same pattern
# as admin_patient) -> show patient details + their Medical Records +
# Prescriptions + Lab Reports. View only, no editing.

@app.route("/doctor_patient", methods=["GET", "POST"])
def doctor_patient():

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    patient = None
    medical_records = []
    prescriptions = []
    lab_reports_list = []

    if request.method == "POST":

        patient_id = request.form["patient_id"].strip()
        patient_name = request.form["patient_name"].strip()

        cursor.execute("""
            SELECT * FROM patient
            WHERE patient_id=%s AND full_name=%s
        """, (patient_id, patient_name))

        patient = cursor.fetchone()

        if not patient:
            flash("Patient Not Found!", "danger")
        else:
            cursor.execute("""
                SELECT
                    mr.*,
                    d.full_name AS doctor_name,
                    d.specialization
                FROM medical_records mr
                JOIN doctor d ON mr.doctor_id = d.doctor_id
                WHERE mr.patient_id=%s
                ORDER BY mr.visit_date DESC
            """, (patient["patient_id"],))
            medical_records = cursor.fetchall()

            cursor.execute("""
                SELECT
                    p.*,
                    d.full_name AS doctor_name
                FROM prescriptions p
                JOIN doctor d ON p.doctor_id = d.doctor_id
                WHERE p.patient_id=%s
                ORDER BY p.prescribed_date DESC
            """, (patient["patient_id"],))
            prescriptions = cursor.fetchall()

            cursor.execute("""
                SELECT * FROM lab_reports
                WHERE patient_id=%s
                ORDER BY visit_date DESC
            """, (patient["patient_id"],))
            lab_reports_list = cursor.fetchall()

    return render_template(
        "doctor_patient.html",
        patient=patient,
        medical_records=medical_records,
        prescriptions=prescriptions,
        lab_reports=lab_reports_list
    )

# ================= ADD MEDICAL RECORD ================= #

@app.route("/add_medical_record/<int:appointment_id>", methods=["GET", "POST"])
def add_medical_record(appointment_id):

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    doctor_id = session["doctor_id"]

    cursor.execute("""
        SELECT
            a.appointment_id,
            a.patient_id,
            p.full_name AS patient_name,
            d.full_name AS doctor_name
        FROM appointment a
        JOIN patient p
            ON a.patient_id = p.patient_id
        JOIN doctor d
            ON a.doctor_id = d.doctor_id
        WHERE a.appointment_id=%s
    """, (appointment_id,))

    appointment = cursor.fetchone()

    if not appointment:
        return "Appointment not found"

    if request.method == "POST":

        diagnosis = request.form["diagnosis"]
        symptoms = request.form["symptoms"]
        treatment = request.form["treatment"]
        doctor_notes = request.form["doctor_notes"]

        cursor.execute("""
            INSERT INTO medical_records
            (
                patient_id,
                doctor_id,
                appointment_id,
                visit_date,
                diagnosis,
                symptoms,
                treatment,
                doctor_notes
            )

            VALUES
            (
                %s,
                %s,
                %s,
                CURDATE(),
                %s,
                %s,
                %s,
                %s
            )
        """,
        (
            appointment["patient_id"],
            doctor_id,
            appointment_id,
            diagnosis,
            symptoms,
            treatment,
            doctor_notes
        ))

        db.commit()

        flash("Medical Record Added Successfully!", "success")

        return redirect(url_for("doctor_dashboard"))

    return render_template(
        "add_medical_record.html",
        appointment=appointment
    )

# ================= DOCTOR: ADD LAB REPORT ================= #
# Linked from doctor_dashboard.html "Add Report" button (Lab Report column,
# shown only once an appointment is Approved).

@app.route("/add_lab_report/<int:appointment_id>", methods=["GET", "POST"])
def add_lab_report(appointment_id):

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    cursor.execute("""
        SELECT
            a.appointment_id,
            a.patient_id,
            p.full_name AS patient_name,
            p.mobile AS patient_mobile,
            a.hospital_name,
            d.full_name AS doctor_name
        FROM appointment a
        JOIN patient p ON a.patient_id = p.patient_id
        JOIN doctor d ON a.doctor_id = d.doctor_id
        WHERE a.appointment_id=%s
    """, (appointment_id,))

    appointment = cursor.fetchone()

    if not appointment:
        return "Appointment not found"

    if request.method == "POST":

        visit_date = request.form["visit_date"]
        test_name = request.form["test_name"]
        test_code = request.form["test_code"]
        staff_name = request.form["staff_name"]
        clinic_name = request.form["clinic_name"]
        clinic_number = request.form["clinic_number"]

        file = request.files.get("report_pdf")

        if not file or file.filename == "":
            flash("Please upload the report PDF.", "danger")
        elif not allowed_file(file.filename):
            flash("Only PDF files are allowed.", "danger")
        else:
            filename = secure_filename(
                f"{appointment['patient_id']}_{test_code}_{file.filename}"
            )
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(file_path)
            report_pdf = f"uploads/lab_reports/{filename}"

            cursor.execute("""
                INSERT INTO lab_reports
                (patient_name, patient_id, patient_number, visit_date,
                 test_name, test_code, laboratory_staff_name, clinic_name,
                 clinic_number, report_pdf)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                appointment["patient_name"],
                appointment["patient_id"],
                appointment.get("patient_mobile"),
                visit_date,
                test_name,
                test_code,
                staff_name,
                clinic_name,
                clinic_number,
                report_pdf
            ))
            db.commit()

            flash("Lab Report Uploaded Successfully!", "success")

            return redirect(url_for("doctor_dashboard"))

    return render_template(
        "add_lab_report.html",
        appointment=appointment
    )


# ================= PATIENT PRESCRIPTIONS ================= #

@app.route("/prescriptions")
def prescriptions():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    cursor.execute("""
        SELECT
            p.prescription_id,
            p.medicine_name,
            p.dosage,
            p.frequency,
            p.duration,
            p.instructions,
            p.prescribed_date,
            d.full_name AS doctor_name,
            d.specialization
        FROM prescriptions p
        JOIN doctor d
            ON p.doctor_id = d.doctor_id
        WHERE p.patient_id = %s
        ORDER BY p.prescribed_date DESC
    """, (patient_id,))

    prescriptions = cursor.fetchall()

    total_prescriptions = len(prescriptions)

    last_prescription = (
        prescriptions[0]["prescribed_date"]
        if prescriptions else None
    )

    doctors_consulted = len(
        set(
            p["doctor_name"]
            for p in prescriptions
        )
    )

    return render_template(
        "prescriptions.html",
        prescriptions=prescriptions,
        total_prescriptions=total_prescriptions,
        last_prescription=last_prescription,
        doctors_consulted=doctors_consulted
    )

# ================= ADMIN LOGIN ================= #

@app.route("/admin_login", methods=["GET","POST"])
def admin_login():

    if request.method == "POST":

        login_id = request.form["login_id"]
        password = request.form["password"]

        cursor.execute("""
        SELECT * FROM admin
        WHERE (email=%s OR username=%s)
        AND password=%s
        """,(login_id,login_id,password))

        admin = cursor.fetchone()

        if admin:

            session["admin_id"] = admin["admin_id"]
            session["admin_name"] = admin["full_name"]

            return redirect(url_for("admin_dashboard"))

        else:
            return render_template(
                "wrong_mail.html",
                role="admin_login"
            )

    return render_template("admin_login.html")

# ================= ADMIN REGISTER ================= #

@app.route("/admin_register", methods=["GET", "POST"])
def admin_register():

    if request.method == "GET":
        return render_template("admin_register.html")

    full_name = request.form["full_name"]
    username = request.form["username"]
    employee_id = request.form["employee_id"]
    email = request.form["email"]
    mobile = request.form["mobile"]
    gender = request.form["gender"]
    hospital_name = request.form["hospital_name"]
    department = request.form["department"]
    address = request.form["address"]
    city = request.form["city"]
    state = request.form["state"]
    pincode = request.form["pincode"]

    password = request.form["password"]
    confirm_password = request.form["confirm_password"]

    if password != confirm_password:
        return "❌ Passwords do not match"

    cursor.execute(
        "SELECT * FROM admin WHERE email=%s",
        (email,)
    )

    if cursor.fetchone():
        return "❌ Email already exists"

    cursor.execute(
        "SELECT * FROM admin WHERE username=%s",
        (username,)
    )

    if cursor.fetchone():
        return "❌ Username already exists"

    cursor.execute(
        "SELECT * FROM admin WHERE employee_id=%s",
        (employee_id,)
    )

    if cursor.fetchone():
        return "❌ Employee ID already exists"

    sql = """
    INSERT INTO admin
    (
        full_name,
        username,
        employee_id,
        email,
        mobile,
        gender,
        hospital_name,
        department,
        address,
        city,
        state,
        pincode,
        password
    )
    VALUES
    (
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
    )
    """

    values = (
        full_name,
        username,
        employee_id,
        email,
        mobile,
        gender,
        hospital_name,
        department,
        address,
        city,
        state,
        pincode,
        password
    )

    cursor.execute(sql, values)
    db.commit()

    session["admin_id"] = cursor.lastrowid
    session["admin_name"] = full_name

    return redirect(url_for("admin_dashboard"))

# ================= ADMIN DASHBOARD ================= #

@app.route("/admin_dashboard")
def admin_dashboard():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    cursor.execute(
        "SELECT * FROM admin WHERE admin_id=%s",
        (session["admin_id"],)
    )
    admin = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) AS total FROM patient")
    total_patients = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM doctor")
    total_doctors = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM admin")
    total_admins = cursor.fetchone()["total"]

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM appointment")
        appointments = cursor.fetchone()["total"]
    except:
        appointments = 0

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM medical_records")
        total_medical_records = cursor.fetchone()["total"]
    except:
        total_medical_records = 0

    return render_template(
        "admin_dashboard.html",
        admin=admin,
        total_patients=total_patients,
        total_doctors=total_doctors,
        total_admins=total_admins,
        appointments=appointments,
        total_medical_records=total_medical_records
    )

# ================= ADMIN PATIENT PROFILE (VIEW ONLY) ================= #
# Sidebar "Patient" button -> search by Patient ID + Name -> show full
# patient details + their Medical Records + Prescriptions + Lab Reports.

@app.route("/admin_patient", methods=["GET", "POST"])
def admin_patient():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    patient = None
    medical_records = []
    prescriptions = []
    lab_reports_list = []

    if request.method == "POST":

        patient_id = request.form["patient_id"].strip()
        patient_name = request.form["patient_name"].strip()

        cursor.execute("""
            SELECT * FROM patient
            WHERE patient_id=%s AND full_name=%s
        """, (patient_id, patient_name))

        patient = cursor.fetchone()

        if not patient:
            flash("Patient Not Found!", "danger")
        else:
            cursor.execute("""
                SELECT
                    mr.*,
                    d.full_name AS doctor_name,
                    d.specialization
                FROM medical_records mr
                JOIN doctor d ON mr.doctor_id = d.doctor_id
                WHERE mr.patient_id=%s
                ORDER BY mr.visit_date DESC
            """, (patient["patient_id"],))
            medical_records = cursor.fetchall()

            cursor.execute("""
                SELECT
                    p.*,
                    d.full_name AS doctor_name
                FROM prescriptions p
                JOIN doctor d ON p.doctor_id = d.doctor_id
                WHERE p.patient_id=%s
                ORDER BY p.prescribed_date DESC
            """, (patient["patient_id"],))
            prescriptions = cursor.fetchall()

            cursor.execute("""
                SELECT * FROM lab_reports
                WHERE patient_id=%s
                ORDER BY visit_date DESC
            """, (patient["patient_id"],))
            lab_reports_list = cursor.fetchall()

    return render_template(
        "admin_patient.html",
        patient=patient,
        medical_records=medical_records,
        prescriptions=prescriptions,
        lab_reports=lab_reports_list
    )
# ================= ADMIN DOCTOR PROFILE (VIEW ONLY) ================= #
# Sidebar/back-button "Doctor" page -> search by Doctor ID + Full Name ->
# show full doctor details + patient count + appointment stats.

@app.route("/admin_doctor", methods=["GET", "POST"])
def admin_doctor():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    doctor = None
    patient_count = 0
    appointment_count = 0
    completed_count = 0
    pending_count = 0

    if request.method == "POST":

        doctor_id = request.form["doctor_id"].strip()
        doctor_name = request.form["doctor_name"].strip()

        cursor.execute("""
            SELECT * FROM doctor
            WHERE doctor_id=%s AND full_name=%s
        """, (doctor_id, doctor_name))

        doctor = cursor.fetchone()

        if not doctor:
            flash("Doctor Not Found!", "danger")
        else:

            cursor.execute("""
                SELECT COUNT(DISTINCT patient_id) AS total
                FROM appointment
                WHERE doctor_id=%s
            """, (doctor["doctor_id"],))
            patient_count = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM appointment
                WHERE doctor_id=%s
            """, (doctor["doctor_id"],))
            appointment_count = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM appointment
                WHERE doctor_id=%s AND status='Approved'
            """, (doctor["doctor_id"],))
            completed_count = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM appointment
                WHERE doctor_id=%s AND status='Pending'
            """, (doctor["doctor_id"],))
            pending_count = cursor.fetchone()["total"]

    return render_template(
        "admin_doctor.html",
        doctor=doctor,
        patient_count=patient_count,
        appointment_count=appointment_count,
        completed_count=completed_count,
        pending_count=pending_count
    )
# ================= ADMIN APPOINTMENTS OVERVIEW ================= #
# Sidebar "Appointments" button -> overall totals (Total / Approved /
# Rejected / Pending) + a per-doctor breakdown table.

@app.route("/admin_appointments")
def admin_appointments():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    cursor.execute("SELECT COUNT(*) AS total FROM appointment")
    total_appointments = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM appointment WHERE status='Approved'")
    total_approved = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM appointment WHERE status='Rejected'")
    total_rejected = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM appointment WHERE status='Pending'")
    total_pending = cursor.fetchone()["total"]

    cursor.execute("""
        SELECT
            d.doctor_id,
            d.full_name,
            d.specialization,
            COUNT(a.appointment_id) AS total,
            SUM(CASE WHEN a.status='Approved' THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN a.status='Rejected' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN a.status='Pending' THEN 1 ELSE 0 END) AS pending
        FROM doctor d
        LEFT JOIN appointment a
            ON d.doctor_id = a.doctor_id
        GROUP BY d.doctor_id, d.full_name, d.specialization
        ORDER BY total DESC
    """)
    doctor_stats = cursor.fetchall()

    return render_template(
        "admin_appointments.html",
        total_appointments=total_appointments,
        total_approved=total_approved,
        total_rejected=total_rejected,
        total_pending=total_pending,
        doctor_stats=doctor_stats
    )
# ================= ADMIN EDIT DOCTOR ================= #
# Linked from admin_doctor.html "Edit" button.
print(os.listdir('templates'))
# NOTE: lab_reports table has no doctor_id column, so the reports-upload
# form no longer sends/expects a doctor selection.
@app.route("/admin_doctor_edit/<int:doctor_id>", methods=["GET", "POST"])
def admin_doctor_edit(doctor_id):

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    cursor.execute(
        "SELECT * FROM doctor WHERE doctor_id=%s",
        (doctor_id,)
    )
    doctor = cursor.fetchone()

    if not doctor:
        flash("Doctor Not Found!", "danger")
        return redirect(url_for("admin_doctor"))

    if request.method == "POST":

        full_name = request.form["full_name"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        specialization = request.form["specialization"]
        qualification = request.form["qualification"]
        license_no = request.form["license_no"]
        hospital_name = request.form["hospital_name"]
        hospital_city = request.form["hospital_city"]
        hospital_district = request.form["hospital_district"]
        hospital_state = request.form["hospital_state"]

        cursor.execute("""
            UPDATE doctor
            SET
                full_name=%s,
                email=%s,
                mobile=%s,
                specialization=%s,
                qualification=%s,
                license_no=%s,
                hospital_name=%s,
                hospital_city=%s,
                hospital_district=%s,
                hospital_state=%s
            WHERE doctor_id=%s
        """, (
            full_name,
            email,
            mobile,
            specialization,
            qualification,
            license_no,
            hospital_name,
            hospital_city,
            hospital_district,
            hospital_state,
            doctor_id
        ))

        db.commit()

        flash("Doctor Details Updated Successfully!", "success")

        return redirect(url_for("admin_doctor_edit", doctor_id=doctor_id))
    return render_template(
        "admin_doctor_edit.html",
        doctor=doctor
    )
# ================= ADMIN LAB REPORTS HUB ================= #
# One page, three tabs (client-side switch, no reload needed to look
# between them once a patient is searched):
#   Reports        -> upload a new lab report PDF
#   Prescriptions  -> add a new prescription / edit an existing one
#   Medical Records-> edit an existing record (added by a doctor)
# All three tabs share ONE "Search Patient" box at the top.
#
# NOTE: the Frequency field on this page is 3 checkboxes
# (Morning / Afternoon / Night). The checked values are joined into one
# comma-separated string ("Morning, Night") before being saved, same as
# the "frequency" column in the prescriptions table already expects.

@app.route("/admin_lab_reports", methods=["GET", "POST"])
def admin_lab_reports():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    cursor.execute("SELECT doctor_id, full_name FROM doctor ORDER BY full_name")
    doctors = cursor.fetchall()

    active_tab = request.form.get("active_tab", "reports")

    patient = None
    reports = []
    prescriptions = []
    medical_records = []

    if request.method == "POST":

        action = request.form.get("action", "")
        patient_id = request.form.get("patient_id", "").strip()
        patient_name = request.form.get("patient_name", "").strip()

        if patient_id and patient_name:
            cursor.execute("""
                SELECT * FROM patient
                WHERE patient_id=%s AND full_name=%s
            """, (patient_id, patient_name))
            patient = cursor.fetchone()

            if not patient:
                flash("Patient Not Found!", "danger")

        if patient:

            # ---------------- REPORTS TAB: upload PDF ----------------
            if action == "upload_report":

                patient_number = request.form["patient_number"]
                visit_date = request.form["visit_date"]
                test_name = request.form["test_name"]
                test_code = request.form["test_code"]
                staff_name = request.form["staff_name"]
                clinic_name = request.form["clinic_name"]
                clinic_number = request.form["clinic_number"]

                file = request.files.get("report_pdf")

                if not file or file.filename == "":
                    flash("Please upload the report PDF.", "danger")
                elif not allowed_file(file.filename):
                    flash("Only PDF files are allowed.", "danger")
                else:
                    filename = secure_filename(f"{patient_id}_{test_code}_{file.filename}")
                    file_path = os.path.join(UPLOAD_FOLDER, filename)
                    file.save(file_path)
                    report_pdf = f"uploads/lab_reports/{filename}"

                    cursor.execute("""
                        INSERT INTO lab_reports
                        (patient_name, patient_id, patient_number, visit_date,
                         test_name, test_code, laboratory_staff_name, clinic_name,
                         clinic_number, report_pdf)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        patient["full_name"], patient_id, patient_number, visit_date,
                        test_name, test_code, staff_name, clinic_name,
                        clinic_number, report_pdf
                    ))
                    db.commit()
                    flash("Lab Report Uploaded Successfully!", "success")

            # ---------------- PRESCRIPTIONS TAB: add new ----------------
            elif action == "add_prescription":

                medicine_name = request.form["medicine_name"]
                dosage = request.form["dosage"]
                frequency = ", ".join(request.form.getlist("frequency"))
                duration = request.form["duration"]
                instructions = request.form["instructions"]
                doctor_id = request.form.get("doctor_id")

                cursor.execute("""
                    INSERT INTO prescriptions
                    (patient_id, doctor_id, medicine_name, dosage, frequency, duration, instructions, prescribed_date)
                    VALUES (%s,%s,%s,%s,%s,%s,%s, CURDATE())
                """, (patient_id, doctor_id, medicine_name, dosage, frequency, duration, instructions))
                db.commit()
                flash("Prescription Added Successfully!", "success")

            # ---------------- PRESCRIPTIONS TAB: edit existing ----------------
            elif action == "edit_prescription":

                prescription_id = request.form["prescription_id"]
                medicine_name = request.form["medicine_name"]
                dosage = request.form["dosage"]
                frequency = ", ".join(request.form.getlist("frequency"))
                duration = request.form["duration"]
                instructions = request.form["instructions"]

                cursor.execute("""
                    UPDATE prescriptions
                    SET medicine_name=%s, dosage=%s, frequency=%s, duration=%s, instructions=%s
                    WHERE prescription_id=%s
                """, (medicine_name, dosage, frequency, duration, instructions, prescription_id))
                db.commit()
                flash("Prescription Updated Successfully!", "success")

            # ---------------- MEDICAL RECORDS TAB: edit existing ----------------
            elif action == "edit_medical_record":

                record_id = request.form["record_id"]
                diagnosis = request.form["diagnosis"]
                symptoms = request.form["symptoms"]
                treatment = request.form["treatment"]
                doctor_notes = request.form["doctor_notes"]

                cursor.execute("""
                    UPDATE medical_records
                    SET diagnosis=%s, symptoms=%s, treatment=%s, doctor_notes=%s
                    WHERE record_id=%s
                """, (diagnosis, symptoms, treatment, doctor_notes, record_id))
                db.commit()
                flash("Medical Record Updated Successfully!", "success")

            # ---------------- reload all three lists for this patient ----------------
            cursor.execute("""
                SELECT * FROM lab_reports
                WHERE patient_id=%s ORDER BY visit_date DESC
            """, (patient_id,))
            reports = cursor.fetchall()

            cursor.execute("""
                SELECT p.*, d.full_name AS doctor_name
                FROM prescriptions p
                JOIN doctor d ON p.doctor_id = d.doctor_id
                WHERE p.patient_id=%s ORDER BY p.prescribed_date DESC
            """, (patient_id,))
            prescriptions = cursor.fetchall()

            cursor.execute("""
                SELECT mr.*, d.full_name AS doctor_name
                FROM medical_records mr
                JOIN doctor d ON mr.doctor_id = d.doctor_id
                WHERE mr.patient_id=%s ORDER BY mr.visit_date DESC
            """, (patient_id,))
            medical_records = cursor.fetchall()

    return render_template(
        "admin_lab_reports.html",
        doctors=doctors,
        patient=patient,
        reports=reports,
        prescriptions=prescriptions,
        medical_records=medical_records,
        active_tab=active_tab
    )

# ================= ADMIN SETTINGS (VIEW / EDIT PROFILE) ================= #

@app.route("/admin_settings", methods=["GET", "POST"])
def admin_settings():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    admin_id = session["admin_id"]

    if request.method == "POST":

        full_name = request.form["full_name"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        gender = request.form["gender"]
        hospital_name = request.form["hospital_name"]
        department = request.form["department"]
        address = request.form["address"]
        city = request.form["city"]
        state = request.form["state"]
        pincode = request.form["pincode"]

        cursor.execute(
            "SELECT profile_picture FROM admin WHERE admin_id=%s",
            (admin_id,)
        )
        current = cursor.fetchone()
        profile_picture = current["profile_picture"] if current else None

        file = request.files.get("profile_picture")

        if file and file.filename != "":
            if allowed_image(file.filename):
                filename = secure_filename(f"admin_{admin_id}_{file.filename}")
                file_path = os.path.join(PROFILE_UPLOAD_FOLDER, filename)
                file.save(file_path)
                profile_picture = f"uploads/profile_pictures/{filename}"
            else:
                flash("Only PNG / JPG / JPEG images are allowed for the profile picture.", "danger")

        cursor.execute("""
            UPDATE admin
            SET
                full_name=%s,
                email=%s,
                mobile=%s,
                gender=%s,
                hospital_name=%s,
                department=%s,
                address=%s,
                city=%s,
                state=%s,
                pincode=%s,
                profile_picture=%s
            WHERE admin_id=%s
        """, (
            full_name,
            email,
            mobile,
            gender,
            hospital_name,
            department,
            address,
            city,
            state,
            pincode,
            profile_picture,
            admin_id
        ))

        db.commit()

        session["admin_name"] = full_name

        flash("Profile Updated Successfully!", "success")

        return redirect(url_for("admin_settings"))

    cursor.execute(
        "SELECT * FROM admin WHERE admin_id=%s",
        (admin_id,)
    )
    admin = cursor.fetchone()

    return render_template(
        "admin_settings.html",
        admin=admin
    )

# ================= ADMIN LOGOUT ================= #

@app.route("/admin_logout")
def admin_logout():

    session.pop("admin_id",None)
    session.pop("admin_name",None)

    return redirect(url_for("admin_login"))


# ================= PATIENT REGISTER ================= #

@app.route("/patient_register", methods=["GET", "POST"])
def patient_register():

    if request.method == "GET":
        return render_template("patient-register.html")

    full_name = request.form["full_name"]
    username = request.form["username"]
    email = request.form["email"]
    mobile = request.form["mobile"]
    dob = request.form["dob"]
    gender = request.form["gender"]
    blood_group = request.form["blood_group"]

    house_no = request.form["house_no"]
    street = request.form["street"]
    city = request.form["city"]
    district = request.form["district"]
    state = request.form["state"]
    pincode = request.form["pincode"]
    hospital_name = request.form["hospital_name"]
    emergency_name = request.form["emergency_name"]
    emergency_phone = request.form["emergency_phone"]

    password = request.form["password"]

    cursor.execute(
        "SELECT * FROM patient WHERE email=%s",
        (email,)
    )

    if cursor.fetchone():
        return "❌ Email already registered."

    cursor.execute(
        "SELECT * FROM patient WHERE username=%s",
        (username,)
    )

    if cursor.fetchone():
        return "❌ Username already exists."

    sql = """
    INSERT INTO patient
(
    full_name,
    username,
    email,
    mobile,
    dob,
    gender,
    blood_group,
    house_no,
    street,
    city,
    district,
    state,
    pincode,
    hospital_name,
    emergency_name,
    emergency_phone,
    password
)
VALUES
(
    %s,%s,%s,%s,%s,%s,%s,
    %s,%s,%s,%s,%s,%s,
    %s,%s,%s,%s
)"""
    values = (
        full_name,
        username,
        email,
        mobile,
        dob,
        gender,
        blood_group,
        house_no,
        street,
        city,
        district,
        state,
        pincode,
        hospital_name,
        emergency_name,
        emergency_phone,
        password
    )

    cursor.execute(sql, values)
    db.commit()

    new_patient_id = cursor.lastrowid

    session["patient_id"] = new_patient_id
    session["username"] = username

    return redirect(url_for("patient_dashboard"))

#==============search hospital==========#
@app.route("/search_hospitals")
def search_hospitals():

    keyword = request.args.get("q", "")

    # Using the doctor table's hospital_name column (already filled in
    # whenever a doctor registers) instead of a separate hospital table
    # that needs manual data entry.
    cursor.execute("""
        SELECT DISTINCT hospital_name
        FROM doctor
        WHERE hospital_name LIKE %s
        LIMIT 10
    """, ("%" + keyword + "%",))

    hospitals = [row["hospital_name"] for row in cursor.fetchall()]

    return jsonify(hospitals)


# ============================================================
# AI FEATURE 1: District -> City -> Hospital cascading search
# ============================================================
# Uses the EXISTING `doctor` table's hospital_district / hospital_city /
# hospital_name columns (already filled in when a doctor registers) --
# no separate hospital table or manual data entry needed.

# Full Tamil Nadu district list so the dropdown always shows every
# district, even before any doctor has registered a hospital there yet.
TAMIL_NADU_DISTRICTS = [
    "Ariyalur", "Chengalpattu", "Chennai", "Coimbatore", "Cuddalore",
    "Dharmapuri", "Dindigul", "Erode", "Kallakurichi", "Kancheepuram",
    "Kanyakumari", "Karur", "Krishnagiri", "Madurai", "Mayiladuthurai",
    "Nagapattinam", "Namakkal", "Nilgiris", "Perambalur", "Pudukkottai",
    "Ramanathapuram", "Ranipet", "Salem", "Sivaganga", "Tenkasi",
    "Thanjavur", "Theni", "Thoothukudi", "Tiruchirappalli", "Tirunelveli",
    "Tirupathur", "Tiruppur", "Tiruvallur", "Tiruvannamalai", "Tiruvarur",
    "Vellore", "Viluppuram", "Virudhunagar"
]


@app.route("/api/get_districts")
def get_districts():

    # Any district that already has a hospital registered by a doctor,
    # PLUS the full static Tamil Nadu list, merged so every district
    # shows up regardless of whether a doctor has registered there yet.
    cursor.execute("""
        SELECT DISTINCT hospital_district
        FROM doctor
        WHERE hospital_district IS NOT NULL AND hospital_district != ''
    """)

    db_districts = [row["hospital_district"] for row in cursor.fetchall()]

    all_districts = sorted(set(TAMIL_NADU_DISTRICTS) | set(db_districts))

    return jsonify(all_districts)


@app.route("/api/get_cities/<district>")
def get_cities(district):

    cursor.execute("""
        SELECT DISTINCT hospital_city
        FROM doctor
        WHERE TRIM(LOWER(hospital_district))=TRIM(LOWER(%s))
        AND hospital_city IS NOT NULL AND hospital_city != ''
        ORDER BY hospital_city
    """, (district,))

    cities = [row["hospital_city"] for row in cursor.fetchall()]

    return jsonify(cities)


@app.route("/api/get_hospitals_by_location")
def get_hospitals_by_location():

    district = request.args.get("district", "")
    city = request.args.get("city", "")

    cursor.execute("""
        SELECT DISTINCT hospital_name
        FROM doctor
        WHERE TRIM(LOWER(hospital_district))=TRIM(LOWER(%s))
        AND TRIM(LOWER(hospital_city))=TRIM(LOWER(%s))
        AND hospital_name IS NOT NULL AND hospital_name != ''
        ORDER BY hospital_name
    """, (district, city))

    hospitals = cursor.fetchall()   # list of {"hospital_name": "..."}

    return jsonify(hospitals)


@app.route("/api/get_doctors_by_hospital")
def get_doctors_by_hospital():

    hospital_name = request.args.get("hospital_name", "")

    cursor.execute("""
        SELECT doctor_id, full_name, specialization
        FROM doctor
        WHERE TRIM(LOWER(hospital_name))=TRIM(LOWER(%s))
        ORDER BY full_name
    """, (hospital_name,))

    doctors = cursor.fetchall()

    return jsonify(doctors)


# ============================================================
# AI FEATURE 2: Voice/Text symptom -> Tamil to English -> Department
# ============================================================
# Frontend sends whatever the patient spoke (Tamil, via browser voice
# recognition with lang='ta-IN') or typed. This translates it to English
# and reuses suggest_department() below to detect the right department.

@app.route("/api/analyze_symptom", methods=["POST"])
def analyze_symptom():

    data = request.get_json(silent=True) or {}
    original_text = (data.get("text") or "").strip()

    if not original_text:
        return jsonify({"error": "No text provided"}), 400

    try:
        translated_text = GoogleTranslator(source="auto", target="en").translate(original_text)
        if not translated_text:
            translated_text = original_text
    except Exception as e:
        print("Translation Error :", e)
        translated_text = original_text   # fall back to original if translation service fails

    department = suggest_department(translated_text)

    return jsonify({
        "original_text": original_text,
        "translated_text": translated_text,
        "department": department
    })


# ================= PATIENT DASHBOARD ================= #

@app.route("/patient_dashboard")
def patient_dashboard():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    cursor.execute(
        "SELECT * FROM patient WHERE patient_id=%s",
        (patient_id,)
    )

    patient_data = cursor.fetchone()

    return render_template(
        "patient_dashboard.html",
        patient=patient_data
    )

#============patient lab reports===================#
@app.route("/lab_reports")
def lab_reports():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    cursor.execute("""
        SELECT *
        FROM lab_reports
        WHERE patient_id=%s
        ORDER BY report_id DESC
    """, (patient_id,))

    reports = cursor.fetchall()

    return render_template(
        "lab_reports.html",
        reports=reports
    )

#===============doctor lab reports===========#
@app.route("/doctor_lab_reports/<int:patient_id>")
def doctor_lab_reports(patient_id):

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    cursor.execute("""
        SELECT *
        FROM lab_reports
        WHERE patient_id=%s
        ORDER BY report_id DESC
    """, (patient_id,))

    reports = cursor.fetchall()

    return render_template(
        "doctor_lab_reports.html",
        reports=reports
    )

# ================= PATIENT MEDICAL RECORDS ================= #

@app.route("/medical_records")
def medical_records():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    cursor.execute("""
        SELECT
            mr.record_id,
            mr.visit_date,
            d.full_name AS doctor_name,
            d.specialization,
            mr.diagnosis,
            mr.symptoms,
            mr.treatment,
            mr.doctor_notes
        FROM medical_records mr
        JOIN doctor d
            ON mr.doctor_id = d.doctor_id
        WHERE mr.patient_id=%s
        ORDER BY mr.visit_date DESC
    """, (patient_id,))

    records = cursor.fetchall()

    total_records = len(records)

    last_visit = records[0]["visit_date"] if records else None

    doctors_consulted = len(
        set(record["doctor_name"] for record in records)
    )

    return render_template(
        "medical_records.html",
        records=records,
        total_records=total_records,
        last_visit=last_visit,
        doctors_consulted=doctors_consulted
    )

# ================= BOOK APPOINTMENT ================= #

@app.route("/book_appointment", methods=["GET", "POST"])
def book_appointment():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    cursor.execute("""
        SELECT full_name, email, hospital_name
        FROM patient
        WHERE patient_id=%s
    """, (patient_id,))

    patient = cursor.fetchone()
    print("Patient Details :", patient)

    if patient is None:
        return "Patient not found"

    hospital_name = patient["hospital_name"]
    suggested_department = ""

    cursor.execute("""
        SELECT
            doctor_id,
            full_name,
            specialization
        FROM doctor
        ORDER BY full_name
    """)

    doctors = cursor.fetchall()

    if request.method == "POST":

        appointment_date = request.form["appointment_date"]

        hour = int(request.form["hour"])
        minute = int(request.form["minute"])
        ampm = request.form["ampm"]

        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0

        appointment_time = f"{hour:02}:{minute:02}:00"

        reason = request.form["reason"]

        # AI FEATURE: if the patient picked a hospital via the
        # District -> City -> Hospital search (or the autocomplete box),
        # use that instead of their default on-file hospital.
        selected_hospital = request.form.get("hospital_name", "").strip()
        if selected_hospital:
            hospital_name = selected_hospital

        suggested_department = suggest_department(reason)

        cursor.execute("""
            SELECT
                doctor_id,
                full_name,
                specialization
            FROM doctor
            WHERE LOWER(specialization)=LOWER(%s)
            ORDER BY full_name
        """, (suggested_department,))

        doctors = cursor.fetchall()

        doctor_id = request.form.get("doctor_id")

        if doctor_id:

            cursor.execute("""
                INSERT INTO appointment
                (
                    patient_id,
                    doctor_id,
                    appointment_date,
                    appointment_time,
                    reason,
                    status,
                    hospital_name
                )
                VALUES
                (%s,%s,%s,%s,%s,%s,%s)
            """, (
                patient_id,
                doctor_id,
                appointment_date,
                appointment_time,
                reason,
                "Pending",
                hospital_name
            ))

            db.commit()

            appointment_id = cursor.lastrowid

            cursor.execute("""
                SELECT
                    full_name,
                    email
                FROM doctor
                WHERE doctor_id=%s
            """, (doctor_id,))

            doctor = cursor.fetchone()

            if doctor and doctor.get("email"):

                msg = Message(
                    subject="New Appointment Request - EMRS",
                    sender=app.config["MAIL_USERNAME"],
                    recipients=[doctor["email"]]
                )

                msg.body = f"""
                Hello Dr. {doctor['full_name']},

                You have received a new appointment request.

                Appointment ID : {appointment_id}
                Patient ID : {patient_id}
                Date : {appointment_date}
                Time : {appointment_time}
                Reason : {reason}

                Please login to EMRS and review the appointment.

                Regards,
                EMRS
                """

                try:
                    mail.send(msg)
                except Exception as e:
                    print("Doctor Mail Error :", e)

            if patient and patient.get("email"):

                msg = Message(
                    subject="Appointment Booked Successfully",
                    sender=app.config["MAIL_USERNAME"],
                    recipients=[patient["email"]]
                )

                msg.body = f"""
                Hello {patient['full_name']},

                Your appointment has been booked successfully.

                Appointment ID : {appointment_id}
                Date : {appointment_date}
                Time : {appointment_time}
                Status : Pending

                Your doctor will review your appointment soon.

                Thank you,
                EMRS
                """

                try:
                    mail.send(msg)
                except Exception as e:
                    print("Patient Mail Error :", e)

            else:
                print("Patient not found or email missing.")

            return redirect(
                url_for(
                    "appointment_success",
                    appointment_id=appointment_id
                )
            )

    return render_template(
        "book_appointment.html",
        doctors=doctors,
        suggested_department=suggested_department,
        hospital_name=hospital_name
    )

# ================= AI Department Suggestion ================= #
# Returns lowercase department names so they match `doctor.specialization
# |lower` exactly as used by filterDoctors() in book_appointment.html.
# Same category list as the client-side keyword logic, kept in sync so a
# Tamil voice input (translated here) and an English typed input (checked
# client-side) always land on the same department.

def suggest_department(reason):

    reason = (reason or "").lower()

    if any(word in reason for word in [
        "heart", "chest pain", "bp",
        "blood pressure", "palpitation", "cardiac"
    ]):
        return "cardiologist"

    elif any(word in reason for word in [
        "head", "headache", "brain",
        "migraine", "fits", "stroke", "seizure", "dizzy", "numbness"
    ]):
        return "neurologist"

    elif any(word in reason for word in [
        "skin", "rash", "allergy"
    ]):
        return "dermatology"

    elif any(word in reason for word in [
        "bone", "leg pain", "joint", "back pain", "fracture", "knee pain"
    ]):
        return "orthopedics"

    elif any(word in reason for word in [
        "stomach", "gas", "vomit", "ulcer", "abdomen"
    ]):
        return "gastroenterology"

    elif any(word in reason for word in [
        "ear", "nose", "throat"
    ]):
        return "ent"

    elif any(word in reason for word in [
        "eye", "vision", "blur"
    ]):
        return "ophthalmology"

    elif any(word in reason for word in [
        "fever", "cold", "cough", "infection", "body pain", "weakness"
    ]):
        return "general physician"

    else:
        return "general physician"


# ================= Appointment Success ================= #

@app.route("/appointment_success")
def appointment_success():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    appointment_id = request.args.get("appointment_id")

    return render_template(
        "appointment_success.html",
        appointment_id=appointment_id
    )

#===========appointment approval==================#
@app.route("/my_appointments")
def my_appointments():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    cursor.execute("""
        SELECT
            appointment_id,
            appointment_date,
            appointment_time,
            hospital_name,
            status
        FROM appointment
        WHERE patient_id=%s
        ORDER BY appointment_date DESC
    """, (patient_id,))

    appointments = cursor.fetchall()

    return render_template(
        "my_appointments.html",
        appointments=appointments
    )

#============= PDF DOWNLOADER =========================#

from reportlab.lib.colors import HexColor
from datetime import datetime


@app.route("/download_slip/<int:appointment_id>")
def download_slip(appointment_id):

    cursor.execute("""
        SELECT
            a.appointment_id,
            a.appointment_date,
            a.appointment_time,
            a.reason,
            a.hospital_name,
            a.status,
            p.full_name AS patient_name,
            d.full_name AS doctor_name,
            d.specialization
        FROM appointment a
        JOIN patient p ON a.patient_id = p.patient_id
        JOIN doctor d ON a.doctor_id = d.doctor_id
        WHERE a.appointment_id=%s
    """, (appointment_id,))

    appointment = cursor.fetchone()

    if not appointment:
        return "Appointment not found"

    appointment_date = appointment["appointment_date"].strftime("%d %b %Y")

    appointment_time = datetime.strptime(
        str(appointment["appointment_time"]),
        "%H:%M:%S"
    ).strftime("%I:%M %p")

    buffer = io.BytesIO()

    pdf = canvas.Canvas(buffer)
    pdf.setTitle("Appointment Slip")

    pdf.setStrokeColor(HexColor("#d9d9d9"))
    pdf.line(40, 755, 555, 755)

    logo_path = os.path.join(
        app.root_path,
        "static",
        "images",
        "hospital_logo.png"
    )

    print("Logo Path :", logo_path)
    print("Exists :", os.path.isfile(logo_path))

    if os.path.isfile(logo_path):
        try:
            pdf.drawImage(
                logo_path,
                45,
                772,
                width=50,
                height=50,
                preserveAspectRatio=True,
                mask="auto"
            )
        except Exception as e:
            print("Logo Error :", e)
    else:
        print("Logo file not found.")

    pdf.setFillColor(HexColor("#000000"))
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(120, 810, "EMRS")

    pdf.setFont("Helvetica", 12)
    pdf.drawString(120, 792, "Electronic Medical Record System")

    pdf.setFont("Helvetica-Bold", 14)

    hospital_name = appointment.get("hospital_name")
    if hospital_name is None:
        hospital_name = "Hospital Not Available"

    pdf.drawString(120, 772, str(hospital_name))

    pdf.setFillColor(HexColor("#000000"))
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(300, 735, "APPOINTMENT SLIP")

    pdf.line(50, 725, 550, 725)

    pdf.roundRect(45, 420, 510, 280, 8)

    y = 675

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Appointment ID :")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(190, y, str(appointment["appointment_id"]))

    y -= 30

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Patient Name :")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(190, y, str(appointment.get("patient_name") or "N/A"))

    y -= 30

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Doctor :")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(
    190,
    y,
    "Dr. " + str(appointment.get("doctor_name") or "Not Assigned")
    )

    y -= 30

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Department :")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(
    190,
    y,
    str(appointment.get("specialization") or "N/A")
    )

    y -= 30

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Appointment Date :")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(190, y, appointment_date)

    y -= 30

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Appointment Time :")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(190, y, appointment_time)

    y -= 30

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Status :")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(
    190,
    y,
    str(appointment.get("status") or "Pending")
    )
    y -= 40

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Reason")
    pdf.line(60, y - 5, 530, y - 5)

    pdf.setFont("Helvetica", 11)
    pdf.drawString(
    60,
    y - 25,
    str(appointment.get("reason") or "-")
    )

    pdf.roundRect(45, 280, 510, 90, 8)

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(
        60,
        345,
        "Please arrive 15 minutes before your appointment."
    )

    pdf.setFont("Helvetica", 10)
    pdf.drawString(
        60,
        325,
        "This slip is computer generated and does not require a signature."
    )

    pdf.drawString(
        60,
        305,
        "Generated by EMRS - Electronic Medical Record System"
    )

    pdf.line(390, 180, 540, 180)
    pdf.drawCentredString(465, 165, "Authorized Signature")

    pdf.save()

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Appointment_{appointment_id}.pdf",
        mimetype="application/pdf"
    )

# ================= EDIT PATIENT PROFILE ================= #

@app.route("/edit_patient_profile", methods=["GET", "POST"])
def edit_patient_profile():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    if request.method == "POST":

        full_name = request.form["full_name"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        dob = request.form["dob"]
        gender = request.form["gender"]
        blood_group = request.form["blood_group"]

        house_no = request.form["house_no"]
        street = request.form["street"]
        city = request.form["city"]
        district = request.form["district"]
        state = request.form["state"]
        pincode = request.form["pincode"]

        emergency_name = request.form["emergency_name"]
        emergency_phone = request.form["emergency_phone"]

        sql = """
        UPDATE patient
        SET
            full_name=%s,
            email=%s,
            mobile=%s,
            dob=%s,
            gender=%s,
            blood_group=%s,
            house_no=%s,
            street=%s,
            city=%s,
            district=%s,
            state=%s,
            pincode=%s,
            emergency_name=%s,
            emergency_phone=%s
        WHERE patient_id=%s
        """

        values = (
            full_name,
            email,
            mobile,
            dob,
            gender,
            blood_group,
            house_no,
            street,
            city,
            district,
            state,
            pincode,
            emergency_name,
            emergency_phone,
            patient_id
        )

        cursor.execute(sql, values)
        db.commit()

        return redirect(url_for("patient_dashboard"))

    cursor.execute(
        "SELECT * FROM patient WHERE patient_id=%s",
        (patient_id,)
    )

    patient_data = cursor.fetchone()

    return render_template(
        "edit_patient_profile.html",
        patient=patient_data
    )


# ================= PATIENT SETTINGS (VIEW / EDIT PROFILE + PROFILE PICTURE) ================= #
# Sidebar "Settings" button -> edit the patient's own profile fields and
# upload/replace a profile picture. The picture is stored under
# static/uploads/profile_pictures/ (same folder admin profile pictures use)
# and the saved path is written into patient.profile_image, which is the
# column patient_dashboard.html already reads to show the photo.
#
# NOTE: run this once if the column doesn't exist yet:
#   ALTER TABLE patient ADD COLUMN profile_image VARCHAR(255) NULL;

@app.route("/patient_settings", methods=["GET", "POST"])
def patient_settings():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    if request.method == "POST":

        full_name = request.form["full_name"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        dob = request.form["dob"]
        gender = request.form["gender"]
        blood_group = request.form["blood_group"]

        house_no = request.form["house_no"]
        street = request.form["street"]
        city = request.form["city"]
        district = request.form["district"]
        state = request.form["state"]
        pincode = request.form["pincode"]

        emergency_name = request.form["emergency_name"]
        emergency_phone = request.form["emergency_phone"]

        cursor.execute(
            "SELECT profile_image FROM patient WHERE patient_id=%s",
            (patient_id,)
        )
        current = cursor.fetchone()
        profile_image = current["profile_image"] if current else None

        file = request.files.get("profile_image")

        if file and file.filename != "":
            if allowed_image(file.filename):
                filename = secure_filename(f"patient_{patient_id}_{file.filename}")
                file_path = os.path.join(PROFILE_UPLOAD_FOLDER, filename)
                file.save(file_path)
                profile_image = url_for(
                    "static",
                    filename=f"uploads/profile_pictures/{filename}"
                )
            else:
                flash("Only PNG / JPG / JPEG images are allowed for the profile picture.", "danger")

        cursor.execute("""
            UPDATE patient
            SET
                full_name=%s,
                email=%s,
                mobile=%s,
                dob=%s,
                gender=%s,
                blood_group=%s,
                house_no=%s,
                street=%s,
                city=%s,
                district=%s,
                state=%s,
                pincode=%s,
                emergency_name=%s,
                emergency_phone=%s,
                profile_image=%s
            WHERE patient_id=%s
        """, (
            full_name,
            email,
            mobile,
            dob,
            gender,
            blood_group,
            house_no,
            street,
            city,
            district,
            state,
            pincode,
            emergency_name,
            emergency_phone,
            profile_image,
            patient_id
        ))

        db.commit()

        session["username"] = session.get("username")

        flash("Profile Updated Successfully!", "success")

        return redirect(url_for("patient_settings"))

    cursor.execute(
        "SELECT * FROM patient WHERE patient_id=%s",
        (patient_id,)
    )
    patient = cursor.fetchone()

    return render_template(
        "patient_settings.html",
        patient=patient
    )


# ================= LOGOUT ================= #

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("home"))

# ================= FORGOT PASSWORD ================= #

@app.route("/forgot_password/<role>", methods=["GET", "POST"])
def forgot_password(role):

    if role not in ["patient", "doctor", "admin"]:
        return "Invalid Role"

    if request.method == "POST":

        login_id = request.form["login_id"].strip()

        global db, cursor

        try:
            if not db.is_connected():
                db.reconnect(attempts=3, delay=2)
                cursor = db.cursor(dictionary=True)
        except Exception as e:
            return f"Database Connection Error: {e}"

        query = f"""
            SELECT full_name, email
            FROM {role}
            WHERE email=%s OR mobile=%s
        """

        try:
            cursor.execute(query, (login_id, login_id))
            user = cursor.fetchone()
        except Exception as e:
            return f"Database Query Error: {e}"

        if not user:
            return "❌ Email or Mobile not found."

        otp = str(random.randint(100000, 999999))

        session["otp"] = otp
        session["login_id"] = login_id
        session["table"] = role

        msg = Message(
            subject="EMRS Password Reset OTP",
            sender=app.config["MAIL_USERNAME"],
            recipients=[user["email"]]
        )

        msg.body = f"""
Hello {user['full_name']},

Your OTP is: {otp}

Do not share this OTP with anyone.

Regards,
EMRS Team
"""

        try:
            mail.send(msg)
        except Exception as e:
            return f"Mail Error: {e}"

        return redirect(url_for("verify_otp"))

    return render_template("forgot_password.html", role=role)

# ================= VERIFY OTP ================= #
@app.route("/verify_otp", methods=["GET", "POST"])
def verify_otp():

    if "otp" not in session:
        return redirect(url_for("login"))

    role = session.get("table")

    if request.method == "POST":

        entered_otp = request.form["otp"].strip()

        if entered_otp == session["otp"]:
            session["otp_verified"] = True
            return redirect(url_for("reset_password"))
        else:
            return render_template(
            "verify_otp.html",
            role=role,
            error="Invalid OTP! Please enter the correct OTP."
    )

    return render_template("verify_otp.html", role=role)

# ================= RESET PASSWORD ================= #

@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():

    if "otp_verified" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":

        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            return render_template(
                "reset_password.html",
                error="❌ Passwords do not match."
            )

        login_id = session["login_id"]
        table = session["table"]

        cursor.execute(f"""
            SELECT password
            FROM {table}
            WHERE email=%s OR mobile=%s
        """, (login_id, login_id))

        user = cursor.fetchone()

        if user and password == user["password"]:
            return render_template(
                "reset_password.html",
                error="❌ You cannot reuse your old password."
            )

        pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&#])[A-Za-z\d@$!%*?&#]{8,}$'

        if not re.match(pattern, password):
            return render_template(
                "reset_password.html",
                error="❌ Password must contain at least 8 characters, one uppercase letter, one lowercase letter, one number and one special character."
            )

        if table == "patient":
            cursor.execute("""
                UPDATE patient
                SET password=%s
                WHERE email=%s OR mobile=%s
            """, (password, login_id, login_id))

        elif table == "doctor":
            cursor.execute("""
                UPDATE doctor
                SET password=%s
                WHERE email=%s OR mobile=%s
            """, (password, login_id, login_id))

        elif table == "admin":
            cursor.execute("""
                UPDATE admin
                SET password=%s
                WHERE email=%s OR mobile=%s
            """, (password, login_id, login_id))

        db.commit()

        role = session["table"]

        session.pop("otp", None)
        session.pop("otp_verified", None)
        session.pop("login_id", None)
        session.pop("table", None)

        return render_template(
            "password_changed.html",
            role=role
        )

    return render_template("reset_password.html")


# ================= ERROR HANDLER ================= #

@app.errorhandler(404)
def page_not_found(error):
    return "<h2>404 - Page Not Found</h2>", 404


# ================= RUN APP ================= #
if __name__ == "__main__":
    app.run(debug=True)