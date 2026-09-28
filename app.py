from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file, g
import mysql.connector
from mysql.connector import pooling
import random
import re
import os
import threading
import traceback

from dotenv import load_dotenv
load_dotenv(r"D:\EMRS\.env")   # <-- loads DB_HOST, DB_PORT, MAIL_USERNAME etc. from the .env file

from reportlab.pdfgen import canvas
import io
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
from deep_translator import GoogleTranslator   # <-- AI FEATURE: Tamil -> English translation

import cloudinary
import cloudinary.uploader
import cloudinary.api

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = "emrs_secret_key_2026"

print("=" * 60)
print("Flask app root path :", app.root_path)
print("Flask STATIC folder (put css/js/images here) :", app.static_folder)
print("CSS should be at :", os.path.join(app.static_folder, "css", "admin_dashboard.css"))
print("That file exists? :", os.path.isfile(os.path.join(app.static_folder, "css", "admin_dashboard.css")))
print("=" * 60)

# ================= CLOUDINARY CONFIGURATION ================= #
# Lab report PDFs are stored on Cloudinary instead of local disk because
# Render's free-tier filesystem is ephemeral - files saved to disk
# disappear on every restart/redeploy, which is why "View PDF" was
# throwing 404 after a while even though the DB row still existed.
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

# ================= MAIL CONFIGURATION ================= #
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")

print("MAIL_USERNAME =", app.config["MAIL_USERNAME"])

if app.config["MAIL_PASSWORD"]:
    print("MAIL_PASSWORD Loaded Successfully")
else:
    print("MAIL_PASSWORD Not Found")

mail = Mail(app)


# ================= SAFE MAIL SENDING ================= #
# Render's free web services block outbound SMTP ports (25/465/587), so a
# plain mail.send() can hang until gunicorn kills the worker -> the user
# sees "Internal Server Error" even though the DB work already succeeded.
# These helpers run the SMTP call in a background thread so a slow or
# blocked mail server can never freeze / crash a request.

def send_mail_async(msg):
    """Fire-and-forget. The request continues immediately."""
    def _send():
        with app.app_context():
            try:
                mail.send(msg)
            except Exception as e:
                print("Mail Error :", e)

    threading.Thread(target=_send, daemon=True).start()


def send_mail_with_timeout(msg, timeout=12):
    """Try to send, but give up after `timeout` seconds.
    Returns (ok, error_message)."""
    result = {"ok": False, "error": None}

    def _send():
        with app.app_context():
            try:
                mail.send(msg)
                result["ok"] = True
            except Exception as e:
                result["error"] = str(e)

    t = threading.Thread(target=_send, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        return False, "Mail server not reachable (timed out)"

    return result["ok"], result["error"]


# ================= DATABASE CONNECTION (POOL, ONE CONNECTION PER REQUEST) ================= #
# WHY THIS CHANGED:
# The old code used ONE global connection + ONE global cursor shared by every
# request. The booking page fires several AJAX calls at the same moment
# (/api/get_districts, /api/get_cities, /api/get_hospitals_by_location ...).
# Flask's dev server is multi-threaded, so those calls used the same cursor
# at the same time and overwrote each other's results. That is what made the
# City dropdown randomly stay disabled / empty.
#
# Now every request gets its OWN connection from a small pool, and hands it
# back automatically when the request ends. The rest of the code still uses
# the names `db` and `cursor` exactly as before - they are proxies that point
# to the current request's connection / cursor.

DB_CONFIG = dict(
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT")),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    ssl_ca="ca.pem",
    connection_timeout=30,
    autocommit=True
)

db_pool = pooling.MySQLConnectionPool(
    pool_name="emrs_pool",
    pool_size=8,
    pool_reset_session=True,
    **DB_CONFIG
)


def _get_conn():
    conn = g.get("_db_conn")

    if conn is None:
        conn = db_pool.get_connection()

        # Aiven (and most cloud MySQL) closes idle connections after a
        # while, so ping/reconnect before using it.
        try:
            conn.ping(reconnect=True, attempts=3, delay=2)
        except mysql.connector.Error as e:
            print("DB ping failed :", e)

        g._db_conn = conn

    return conn


def _get_cursor():
    cur = g.get("_db_cursor")

    if cur is None:
        cur = _get_conn().cursor(dictionary=True, buffered=True)
        g._db_cursor = cur

    return cur


class _DBProxy:
    def __getattr__(self, name):
        return getattr(_get_conn(), name)


class _CursorProxy:
    def __getattr__(self, name):
        return getattr(_get_cursor(), name)


db = _DBProxy()
cursor = _CursorProxy()


@app.teardown_appcontext
def close_db_connection(exc=None):
    cur = g.pop("_db_cursor", None)
    conn = g.pop("_db_conn", None)

    try:
        if cur is not None:
            cur.close()
    except Exception:
        pass

    try:
        if conn is not None:
            conn.close()      # for a pooled connection this returns it to the pool
    except Exception:
        pass


# ================= DEBUG LOGGER (booking flow) ================= #
# Prints one line per request for the booking pages so we can see exactly
# what the server answered: 302 -> redirected, 200 -> same page re-rendered.
# (FIX: a duplicate stub of the /download_slip route used to sit here and
#  caused "View function mapping is overwriting an existing endpoint
#  function: download_slip". The real route is further below.)
@app.after_request
def log_booking_flow(response):
    if request.path in ("/book_appointment", "/appointment_success"):
        print(">>> [%s] %s -> HTTP %s  Location=%s" % (
            request.method, request.path, response.status_code,
            response.headers.get("Location")
        ))
    return response


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
            return "\u274c Passwords do not match"

        cursor.execute(
            "SELECT * FROM doctor WHERE email=%s",
            (email,)
        )

        if cursor.fetchone():
            return "\u274c Email already registered"

        cursor.execute(
            "SELECT * FROM doctor WHERE username=%s",
            (username,)
        )

        if cursor.fetchone():
            return "\u274c Username already exists"

        # FIX: doctor_id is now generated here in Python (e.g. "KAT001"-style
        # prefix + number) instead of relying on a MySQL trigger. The trigger
        # was missing on the Aiven DB, so "" was being saved as the ID.
        new_doctor_id = generate_doctor_id(full_name, hospital_city)

        sql = """
        INSERT INTO doctor
        (
            doctor_id,
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
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
        """

        values = (
            new_doctor_id,
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

        session["doctor_id"] = new_doctor_id
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
        doctor_id=doctor_id,
        appointments=appointments,
        total_patients=total_patients,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count
    )

# ================= DOCTOR: MEDICAL RECORDS SEARCH (LIST) ================= #
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
    session.pop("doctor_photo", None)

    return redirect(url_for("doctor_login"))

# ================= DOCTOR: ALL APPOINTMENTS ================= #
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
            # Uploaded to Cloudinary instead of local disk so the file
            # survives Render restarts/redeploys (local disk is ephemeral).
            public_id = secure_filename(f"{appointment['patient_id']}_{test_code}")
            upload_result = cloudinary.uploader.upload(
                file,
                resource_type="raw",
                folder="lab_reports",
                public_id=public_id,
                overwrite=True
            )
            report_pdf = upload_result["secure_url"]

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
        return "\u274c Passwords do not match"

    cursor.execute(
        "SELECT * FROM admin WHERE email=%s",
        (email,)
    )

    if cursor.fetchone():
        return "\u274c Email already exists"

    cursor.execute(
        "SELECT * FROM admin WHERE username=%s",
        (username,)
    )

    if cursor.fetchone():
        return "\u274c Username already exists"

    cursor.execute(
        "SELECT * FROM admin WHERE employee_id=%s",
        (employee_id,)
    )

    if cursor.fetchone():
        return "\u274c Employee ID already exists"

    # FIX: admin_id is now generated here in Python instead of relying on
    # a MySQL trigger (the trigger may be missing on the Aiven DB, which
    # would save "" as the ID).
    new_admin_id = generate_admin_id(full_name, city)

    sql = """
    INSERT INTO admin
    (
        admin_id,
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
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
    )
    """

    values = (
        new_admin_id,
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

    session["admin_id"] = new_admin_id
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

# ================= ADMIN EDIT PATIENT PROFILE ================= #
# patient_id is a text ID like "RAAP01", so this must NOT use <int:...>
@app.route("/admin_edit_patient/<patient_id>", methods=["GET", "POST"])
def admin_edit_patient(patient_id):

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    cursor.execute(
        "SELECT * FROM patient WHERE patient_id=%s",
        (patient_id,)
    )
    patient = cursor.fetchone()

    if not patient:
        flash("Patient Not Found!", "danger")
        return redirect(url_for("admin_patient"))

    if request.method == "POST":

        full_name = request.form["full_name"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        dob = request.form["dob"]
        gender = request.form["gender"]
        blood_group = request.form["blood_group"]

        address = request.form["address"]
        city = request.form["city"]
        district = request.form["district"]
        state = request.form["state"]
        pincode = request.form["pincode"]

        emergency_name = request.form["emergency_name"]
        emergency_phone = request.form["emergency_phone"]

        # email must stay unique across other patients
        cursor.execute("""
            SELECT patient_id FROM patient
            WHERE email=%s AND patient_id!=%s
        """, (email, patient_id))

        if cursor.fetchone():
            flash("This email is already used by another patient.", "danger")
            return redirect(url_for("admin_edit_patient", patient_id=patient_id))

        cursor.execute("""
            UPDATE patient
            SET
                full_name=%s,
                email=%s,
                mobile=%s,
                dob=%s,
                gender=%s,
                blood_group=%s,
                address=%s,
                city=%s,
                district=%s,
                state=%s,
                pincode=%s,
                emergency_name=%s,
                emergency_phone=%s
            WHERE patient_id=%s
        """, (
            full_name,
            email,
            mobile,
            dob,
            gender,
            blood_group,
            address,
            city,
            district,
            state,
            pincode,
            emergency_name,
            emergency_phone,
            patient_id
        ))

        db.commit()

        flash("Patient Details Updated Successfully!", "success")

        return redirect(url_for("admin_edit_patient", patient_id=patient_id))

    return render_template(
        "admin_edit_patient.html",
        patient=patient
    )

# ================= ADMIN DOCTOR PROFILE (VIEW ONLY) ================= #
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
# doctor_id is now a text ID like "RASI01", so this must NOT use <int:...>
@app.route("/admin_doctor_edit/<doctor_id>", methods=["GET", "POST"])
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
                    # Uploaded to Cloudinary instead of local disk so the
                    # file survives Render restarts/redeploys (local disk
                    # on Render's free tier is ephemeral and gets wiped).
                    public_id = secure_filename(f"{patient_id}_{test_code}")
                    upload_result = cloudinary.uploader.upload(
                        file,
                        resource_type="raw",
                        folder="lab_reports",
                        public_id=public_id,
                        overwrite=True
                    )
                    report_pdf = upload_result["secure_url"]

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

    address = request.form["address"]
    city = request.form["city"]
    district = request.form["district"]
    state = request.form["state"]
    pincode = request.form["pincode"]
    emergency_name = request.form["emergency_name"]
    emergency_phone = request.form["emergency_phone"]

    password = request.form["password"]

    cursor.execute(
        "SELECT * FROM patient WHERE email=%s",
        (email,)
    )

    if cursor.fetchone():
        return "\u274c Email already registered."

    cursor.execute(
        "SELECT * FROM patient WHERE username=%s",
        (username,)
    )

    if cursor.fetchone():
        return "\u274c Username already exists."

    # NEW: generate the custom patient_id (e.g. "RAAP01") from the
    # patient's name + city, instead of relying on auto-increment
    # (hospital is no longer collected at registration time).
    new_patient_id = generate_patient_id(full_name, city)

    sql = """
    INSERT INTO patient
(
    patient_id,
    full_name,
    username,
    email,
    mobile,
    dob,
    gender,
    blood_group,
    address,
    city,
    district,
    state,
    pincode,
    emergency_name,
    emergency_phone,
    password
)
VALUES
(
    %s,%s,%s,%s,%s,%s,%s,%s,
    %s,%s,%s,%s,%s,
    %s,%s,%s
)"""
    values = (
        new_patient_id,
        full_name,
        username,
        email,
        mobile,
        dob,
        gender,
        blood_group,
        address,
        city,
        district,
        state,
        pincode,
        emergency_name,
        emergency_phone,
        password
    )

    cursor.execute(sql, values)
    db.commit()

    session["patient_id"] = new_patient_id
    session["username"] = username

    return redirect(url_for("patient_dashboard"))

#==============search hospital==========#
@app.route("/search_hospitals")
def search_hospitals():

    keyword = request.args.get("q", "")

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
    # FIX: earlier this returned ALL 38 Tamil Nadu districts. Most of them
    # have no registered hospital, so after choosing one the City box was
    # disabled with "No hospitals registered here yet" and looked "paused".
    # Now only districts that really have a doctor/hospital registered are
    # listed, so the City dropdown always has something to show.

    cursor.execute("""
        SELECT DISTINCT hospital_district
        FROM doctor
        WHERE hospital_district IS NOT NULL AND TRIM(hospital_district) != ''
    """)

    db_districts = [row["hospital_district"] for row in cursor.fetchall()]

    # Use the standard spelling from the list above when it matches
    # (case-insensitive), so "vellore" and "Vellore" don't show twice.
    canonical = {d.lower(): d for d in TAMIL_NADU_DISTRICTS}
    found = {}

    for d in db_districts:
        clean = d.strip()
        found[clean.lower()] = canonical.get(clean.lower(), clean)

    # Nothing registered yet -> fall back to the full list
    if not found:
        return jsonify(sorted(TAMIL_NADU_DISTRICTS))

    return jsonify(sorted(found.values()))


@app.route("/api/get_cities/<district>")
def get_cities(district):

    cursor.execute("""
        SELECT DISTINCT TRIM(hospital_city) AS hospital_city
        FROM doctor
        WHERE TRIM(LOWER(hospital_district))=TRIM(LOWER(%s))
        AND hospital_city IS NOT NULL AND TRIM(hospital_city) != ''
        ORDER BY hospital_city
    """, (district,))

    # remove duplicates that only differ by upper/lower case
    seen = {}
    for row in cursor.fetchall():
        city = row["hospital_city"]
        seen.setdefault(city.lower(), city)

    return jsonify(sorted(seen.values()))


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

    hospitals = cursor.fetchall()

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
        translated_text = original_text

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
# patient_id is now a text ID like "RAAP01", so this must NOT use <int:...>
@app.route("/doctor_lab_reports/<patient_id>")
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

# ================= BOOK APPOINTMENT (FIXED) ================= #

@app.route("/book_appointment", methods=["GET", "POST"])
def book_appointment():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    cursor.execute("""
        SELECT full_name, email
        FROM patient
        WHERE patient_id=%s
    """, (patient_id,))

    patient = cursor.fetchone()
    print("Patient Details :", patient)

    if patient is None:
        return "Patient not found"

    # ALWAYS send the full doctor list to the page. The JavaScript in the
    # template filters it by hospital / department, so the server must not
    # shrink it (that was leaving the dropdown empty after a failed submit).
    cursor.execute("""
        SELECT
            doctor_id,
            full_name,
            specialization
        FROM doctor
        ORDER BY full_name
    """)

    doctors = cursor.fetchall()

    hospital_name = ""
    suggested_department = ""
    form_date = ""
    form_reason = ""

    if request.method == "POST":

        print(">>> POST reached book_appointment :", dict(request.form))
        print(">>> session patient_id =", session.get("patient_id"))

        appointment_date = request.form.get("appointment_date", "").strip()
        reason = request.form.get("reason", "").strip()
        doctor_id = request.form.get("doctor_id", "").strip()
        hospital_name = request.form.get("hospital_name", "").strip()

        form_date = appointment_date
        form_reason = reason

        suggested_department = suggest_department(reason)

        # ---------- validation: show a message instead of silently reloading ----------
        error = None
        appointment_time = None

        try:
            hour = int(request.form["hour"])
            minute = int(request.form["minute"])
            ampm = request.form["ampm"]

            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0

            appointment_time = f"{hour:02}:{minute:02}:00"
        except (KeyError, ValueError):
            error = "Please select a valid appointment time."

        if not error and not appointment_date:
            error = "Please select the appointment date."
        elif not error and not reason:
            error = "Please describe your problem (text or voice)."
        elif not error and not doctor_id:
            error = "Please select a doctor."

        doctor = None
        if not error:
            cursor.execute("""
                SELECT doctor_id, full_name, email, hospital_name
                FROM doctor
                WHERE doctor_id=%s
            """, (doctor_id,))
            doctor = cursor.fetchone()

            if not doctor:
                error = "Selected doctor was not found. Please choose again."

        if error:
            print(">>> VALIDATION ERROR :", error)
            flash(error, "danger")
            return render_template(
                "book_appointment.html",
                doctors=doctors,
                suggested_department=suggested_department,
                hospital_name=hospital_name,
                form_date=form_date,
                form_reason=form_reason
            )

        # if the patient left the hospital box empty, use the doctor's hospital
        if not hospital_name:
            hospital_name = doctor.get("hospital_name") or ""

        # ---------- save appointment ----------
        appointment_id = None

        try:
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

            # lastrowid can be 0 / None in some setups -> look the row up
            if not appointment_id:
                cursor.execute("""
                    SELECT MAX(appointment_id) AS last_id
                    FROM appointment
                    WHERE patient_id=%s AND doctor_id=%s
                """, (patient_id, doctor_id))
                row = cursor.fetchone()
                appointment_id = row["last_id"] if row else None

        except Exception as e:
            # Print the FULL error in the terminal AND show it on the page
            print("Appointment Insert Error :", e)
            traceback.print_exc()
            flash("Could not book the appointment: " + str(e), "danger")
            return render_template(
                "book_appointment.html",
                doctors=doctors,
                suggested_department=suggested_department,
                hospital_name=hospital_name,
                form_date=form_date,
                form_reason=form_reason
            )

        # ---------- emails (background, never block the booking) ----------
        try:
            if doctor.get("email"):

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

                send_mail_async(msg)

            if patient.get("email"):

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

                send_mail_async(msg)

        except Exception as e:
            print("Mail Setup Error :", e)

        print(">>> SAVED OK, redirecting. appointment_id =", appointment_id)

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
        hospital_name=hospital_name,
        form_date=form_date,
        form_reason=form_reason
    )

# ================= AI Department Suggestion ================= #

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


# ============================================================
# CUSTOM ID GENERATORS (patient / doctor / admin)
# ============================================================
# Builds an ID like "RAAP01" -> first 2 letters of the name
# + first 2 letters of the city + a running 2-digit number
# for that exact prefix (so different name/city combos each start
# their own count from 01).

def generate_patient_id(full_name, city):

    name_letters = re.sub(r'[^A-Za-z]', '', full_name or "")
    name_part = (name_letters[:2] or "XX").upper()

    city_letters = re.sub(r'[^A-Za-z]', '', city or "")
    city_part = (city_letters[:2] or "XX").upper()

    prefix = name_part + city_part

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM patient
        WHERE patient_id LIKE %s
    """, (prefix + "%",))

    count = cursor.fetchone()["total"]
    next_number = count + 1

    new_id = f"{prefix}{next_number:02d}"

    while True:
        cursor.execute("SELECT patient_id FROM patient WHERE patient_id=%s", (new_id,))
        if not cursor.fetchone():
            break
        next_number += 1
        new_id = f"{prefix}{next_number:02d}"

    return new_id


def generate_doctor_id(full_name, hospital_city):
    """Same format as the patient ID (e.g. KAT001 style prefix + number),
    built from the doctor's name + hospital city. Replaces the MySQL
    trigger `before_doctor_insert`, which does not exist on the Aiven DB."""

    name_part = (re.sub(r'[^A-Za-z]', '', full_name or "")[:2] or "XX").upper()
    city_part = (re.sub(r'[^A-Za-z]', '', hospital_city or "")[:2] or "XX").upper()
    prefix = name_part + city_part

    n = 1
    while True:
        new_id = f"{prefix}{n:02d}"
        cursor.execute("SELECT doctor_id FROM doctor WHERE doctor_id=%s", (new_id,))
        if not cursor.fetchone():
            return new_id
        n += 1


def generate_admin_id(full_name, city):
    """Same format as the doctor/patient ID (e.g. RACH01), built from the
    admin's name + city. Replaces the MySQL trigger `before_admin_insert`."""

    name_part = (re.sub(r'[^A-Za-z]', '', full_name or "")[:2] or "XX").upper()
    city_part = (re.sub(r'[^A-Za-z]', '', city or "")[:2] or "XX").upper()
    prefix = name_part + city_part

    n = 1
    while True:
        new_id = f"{prefix}{n:02d}"
        cursor.execute("SELECT admin_id FROM admin WHERE admin_id=%s", (new_id,))
        if not cursor.fetchone():
            return new_id
        n += 1


# ================= Appointment Success ================= #

@app.route("/appointment_success")
def appointment_success():

    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    appointment_id = request.args.get("appointment_id", type=int)

    # If the id is missing, take the patient's latest appointment so
    # the page never breaks.
    if not appointment_id:
        cursor.execute("""
            SELECT MAX(appointment_id) AS last_id
            FROM appointment
            WHERE patient_id=%s
        """, (session["patient_id"],))
        row = cursor.fetchone()
        appointment_id = row["last_id"] if row else None

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
from reportlab.lib.pagesizes import A4
from datetime import datetime


@app.route("/download_slip/<int:appointment_id>")
def download_slip(appointment_id):

    # Only a logged-in patient or doctor can download a slip
    if "patient_id" not in session and "doctor_id" not in session:
        return redirect(url_for("login"))

    cursor.execute("""
        SELECT
            a.appointment_id,
            a.patient_id,
            a.appointment_date,
            a.appointment_time,
            a.reason,
            a.hospital_name,
            a.status,
            p.full_name AS patient_name,
            p.mobile AS patient_mobile,
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

    hospital_name = appointment.get("hospital_name") or "Hospital Not Assigned"
    status = str(appointment.get("status") or "Pending")

    # Status colours (mirrors typical hospital slip conventions)
    status_colors = {
        "Approved": HexColor("#1e9e5a"),
        "Pending": HexColor("#c98a10"),
        "Rejected": HexColor("#d64545"),
    }
    status_color = status_colors.get(status, HexColor("#555555"))

    buffer = io.BytesIO()

    page_w, page_h = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"Appointment Slip - {appointment_id}")

    margin = 40
    content_w = page_w - (2 * margin)

    navy = HexColor("#0b3d5c")
    accent = HexColor("#00b4d8")
    light_bg = HexColor("#f2f9fc")
    border_gray = HexColor("#c9d8e0")
    text_dark = HexColor("#1a1a1a")
    text_muted = HexColor("#5a6b75")

    # ---------- Outer page border ----------
    pdf.setStrokeColor(border_gray)
    pdf.setLineWidth(1)
    pdf.rect(margin - 15, margin - 15, content_w + 30, page_h - (2 * margin) + 30)

    # ---------- Header band ----------
    header_h = 90
    header_top = page_h - margin
    pdf.setFillColor(navy)
    pdf.rect(margin, header_top - header_h, content_w, header_h, fill=1, stroke=0)

    logo_path = os.path.join(app.root_path, "static", "images", "hospital_logo.png")
    text_x = margin + 20

    if os.path.isfile(logo_path):
        try:
            pdf.drawImage(
                logo_path,
                margin + 18,
                header_top - header_h + 20,
                width=50,
                height=50,
                preserveAspectRatio=True,
                mask="auto"
            )
            text_x = margin + 85
        except Exception as e:
            print("Logo Error :", e)

    pdf.setFillColor(HexColor("#ffffff"))
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(text_x, header_top - 35, hospital_name)

    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(HexColor("#d6ecf5"))
    pdf.drawString(text_x, header_top - 52, "Electronic Medical Record System")

    # Slip number / QR-less reference block, top-right of header
    pdf.setFont("Helvetica-Bold", 10)
    pdf.setFillColor(HexColor("#ffffff"))
    pdf.drawRightString(margin + content_w - 20, header_top - 35, "APPOINTMENT SLIP")
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(HexColor("#d6ecf5"))
    pdf.drawRightString(
        margin + content_w - 20,
        header_top - 50,
        f"Ref No: APT-{appointment_id:06d}"
    )
    pdf.drawRightString(
        margin + content_w - 20,
        header_top - 63,
        f"Issued: {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
    )

    # ---------- Status ribbon ----------
    ribbon_y = header_top - header_h - 28
    pdf.setFillColor(status_color)
    pdf.roundRect(margin + content_w - 140, ribbon_y - 4, 140, 24, 4, fill=1, stroke=0)
    pdf.setFillColor(HexColor("#ffffff"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(margin + content_w - 70, ribbon_y + 3, status.upper())

    pdf.setFillColor(text_dark)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin + 20, ribbon_y + 3, "Patient Appointment Confirmation")

    # ---------- Info panel ----------
    panel_top = ribbon_y - 25
    panel_h = 175
    pdf.setFillColor(light_bg)
    pdf.setStrokeColor(border_gray)
    pdf.roundRect(margin, panel_top - panel_h, content_w, panel_h, 6, fill=1, stroke=1)

    col1_x = margin + 25
    col2_x = margin + (content_w / 2) + 10
    row_h = 34
    y = panel_top - 30

    def field(x, label, value):
        pdf.setFont("Helvetica-Bold", 9)
        pdf.setFillColor(text_muted)
        pdf.drawString(x, y, label.upper())
        pdf.setFont("Helvetica-Bold", 12)
        pdf.setFillColor(text_dark)
        pdf.drawString(x, y - 15, str(value))

    field(col1_x, "Appointment ID", appointment["appointment_id"])
    field(col2_x, "Patient ID", appointment.get("patient_id") or "N/A")
    y -= row_h

    field(col1_x, "Patient Name", appointment.get("patient_name") or "N/A")
    field(col2_x, "Mobile Number", appointment.get("patient_mobile") or "N/A")
    y -= row_h

    field(col1_x, "Doctor", "Dr. " + str(appointment.get("doctor_name") or "Not Assigned"))
    field(col2_x, "Department", appointment.get("specialization") or "N/A")
    y -= row_h

    field(col1_x, "Appointment Date", appointment_date)
    field(col2_x, "Appointment Time", appointment_time)
    y -= row_h

    pdf.setStrokeColor(border_gray)
    pdf.line(margin + 20, y + 12, margin + content_w - 20, y + 12)

    # ---------- Reason box ----------
    reason_top = panel_top - panel_h - 20
    reason_h = 70
    pdf.setStrokeColor(border_gray)
    pdf.setFillColor(HexColor("#ffffff"))
    pdf.roundRect(margin, reason_top - reason_h, content_w, reason_h, 6, fill=1, stroke=1)

    pdf.setFont("Helvetica-Bold", 9)
    pdf.setFillColor(text_muted)
    pdf.drawString(margin + 20, reason_top - 20, "REASON FOR VISIT")

    pdf.setFont("Helvetica", 11)
    pdf.setFillColor(text_dark)
    reason_text = str(appointment.get("reason") or "-")
    # simple word-wrap so long reasons don't run off the page
    max_chars = 95
    lines = []
    words = reason_text.split()
    current = ""
    for w in words:
        trial = (current + " " + w).strip()
        if len(trial) > max_chars:
            lines.append(current)
            current = w
        else:
            current = trial
    if current:
        lines.append(current)
    ry = reason_top - 40
    for line in lines[:2]:
        pdf.drawString(margin + 20, ry, line)
        ry -= 15

    # ---------- Instructions ----------
    instr_top = reason_top - reason_h - 25
    pdf.setFont("Helvetica-Bold", 10)
    pdf.setFillColor(accent)
    pdf.drawString(margin, instr_top, "IMPORTANT INSTRUCTIONS")

    instr_lines = [
        "\u2022 Please arrive at least 15 minutes before your scheduled appointment time.",
        "\u2022 Carry a valid photo ID and any previous medical records / prescriptions.",
        "\u2022 This slip is system generated and valid for the date and time mentioned above.",
    ]
    iy = instr_top - 20
    pdf.setFont("Helvetica", 9.5)
    pdf.setFillColor(text_dark)
    for line in instr_lines:
        pdf.drawString(margin, iy, line)
        iy -= 16

    # ---------- Signature block ----------
    sig_y = iy - 35
    pdf.setStrokeColor(text_muted)
    pdf.line(margin + content_w - 200, sig_y, margin + content_w - 20, sig_y)
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(text_muted)
    pdf.drawCentredString(margin + content_w - 110, sig_y - 12, "Authorized Signature / Hospital Seal")

    # ---------- Footer ----------
    footer_y = margin
    pdf.setStrokeColor(border_gray)
    pdf.line(margin, footer_y + 22, margin + content_w, footer_y + 22)
    pdf.setFont("Helvetica-Oblique", 8.5)
    pdf.setFillColor(text_muted)
    pdf.drawCentredString(
        margin + (content_w / 2),
        footer_y + 8,
        "This is a computer generated document from EMRS - Electronic Medical Record System and does not require a physical signature."
    )

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

        address = request.form["address"]
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
            address=%s,
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
            address,
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

        address = request.form["address"]
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
                address=%s,
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
            address,
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

        # (DB reconnect is now handled automatically per request by the pool)

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
            return "\u274c Email or Mobile not found."

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

        # OTP mail: wait up to 12 seconds (well below gunicorn's 30s worker
        # timeout) so a blocked SMTP port shows a message instead of a 500.
        ok, err = send_mail_with_timeout(msg, timeout=12)

        if not ok:
            session.pop("otp", None)
            return f"Mail Error: {err}"

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
                error="\u274c Passwords do not match."
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
                error="\u274c You cannot reuse your old password."
            )

        pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&#])[A-Za-z\d@$!%*?&#]{8,}$'

        if not re.match(pattern, password):
            return render_template(
                "reset_password.html",
                error="\u274c Password must contain at least 8 characters, one uppercase letter, one lowercase letter, one number and one special character."
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