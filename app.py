from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import mysql.connector
import random
import re
import os
from reportlab.pdfgen import canvas
from flask import send_file
import io
from flask_mail import Mail, Message

app = Flask(__name__)
app.secret_key = "emrs_secret_key_2026"

# ================= MAIL CONFIGURATION ================= #
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = "dharshaaa7@gmail.com"
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")

mail = Mail(app)

# ================= DATABASE CONNECTION ================= #
import os
db = mysql.connector.connect(
    host="emrs-project-emrs-project.j.aivencloud.com",
    port=19848,
    user="avnadmin",
    password=os.environ.get("DB_PASSWORD"),
    database="defaultdb",
    ssl_ca="ca.pem"
)
cursor = db.cursor(dictionary=True)

print("✅ Database Connected Successfully!")
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

        # Check Login (Email / Username / Mobile)
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
                "patient_login.html",
                error="❌ Invalid Email / Username / Mobile or Password."
            )

    return render_template("patient_login.html")
# ================= DOCTOR ~================= #

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

            return redirect(url_for("doctor_dashboard"))

        else:
            return "❌ Invalid Email/Username or Password"

    return render_template("doctor_login.html")

# ================= DOCTOR REGISTER ================= #

@app.route("/doctor_register", methods=["GET", "POST"])
def doctor_register():

    if request.method == "POST":
        doctor_id = request.form.get("doctor_id")
        print("Doctor Selected From Form:", doctor_id)

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

        # Check Email
        cursor.execute(
            "SELECT * FROM doctor WHERE email=%s",
            (email,)
        )

        if cursor.fetchone():
            return "❌ Email already registered"

        # Check Username
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
# ================= DOCTOR DASHBOARD ================= #
@app.route("/doctor_dashboard")
def doctor_dashboard():

    if "doctor_id" not in session:
        return redirect(url_for("doctor_login"))

    # Logged in doctor id
    doctor_id = session["doctor_id"]

    # Get logged-in doctor's hospital
    cursor.execute("""
        SELECT hospital_name
        FROM doctor
        WHERE doctor_id=%s
    """, (doctor_id,))

    doctor = cursor.fetchone()

    if not doctor:
        return redirect(url_for("doctor_login"))

    hospital_name = doctor["hospital_name"]

    # Get appointments only for this doctor
    cursor.execute("""
        SELECT
            a.appointment_id,
            a.patient_id,
            p.full_name AS patient_name,
            a.appointment_date,
            a.appointment_time,
            a.reason,
            a.hospital_name,
            a.status
        FROM appointment a
        INNER JOIN patient p
            ON a.patient_id = p.patient_id
        WHERE a.doctor_id=%s
          AND a.hospital_name=%s
        ORDER BY
            a.appointment_date ASC,
            a.appointment_time ASC
    """, (doctor_id, hospital_name))

    appointments = cursor.fetchall()

    total_patients = len(set(a["patient_id"] for a in appointments))
    pending_count = sum(1 for a in appointments if a["status"] == "Pending")
    approved_count = sum(1 for a in appointments if a["status"] == "Approved")

    return render_template(
        "doctor_dashboard.html",
        appointments=appointments,
        total_patients=total_patients,
        pending_count=pending_count,
        approved_count=approved_count
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

    return redirect(url_for("doctor_dashboard"))


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

    return redirect(url_for("doctor_dashboard"))
# ================= DOCTOR LOGOUT ================= #

@app.route("/doctor_logout")
def doctor_logout():

    session.pop("doctor_id", None)
    session.pop("doctor_name", None)

    return redirect(url_for("doctor_login"))

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
            return "❌ Invalid Email/Username or Password"

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

    # Email Check
    cursor.execute(
        "SELECT * FROM admin WHERE email=%s",
        (email,)
    )

    if cursor.fetchone():
        return "❌ Email already exists"

    # Username Check
    cursor.execute(
        "SELECT * FROM admin WHERE username=%s",
        (username,)
    )

    if cursor.fetchone():
        return "❌ Username already exists"

    # Employee ID Check
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

    # Admin details
    cursor.execute(
        "SELECT * FROM admin WHERE admin_id=%s",
        (session["admin_id"],)
    )
    admin = cursor.fetchone()

    # Total Patients
    cursor.execute("SELECT COUNT(*) AS total FROM patient")
    total_patients = cursor.fetchone()["total"]

    # Total Doctors
    cursor.execute("SELECT COUNT(*) AS total FROM doctor")
    total_doctors = cursor.fetchone()["total"]

    # Total Admins
    cursor.execute("SELECT COUNT(*) AS total FROM admin")
    total_admins = cursor.fetchone()["total"]

    # Total Appointments
    # appointment table create pannirundha use pannunga
    try:
        cursor.execute("SELECT COUNT(*) AS total FROM appointment")
        appointments = cursor.fetchone()["total"]
    except:
        appointments = 0

    return render_template(
        "admin_dashboard.html",
        admin=admin,
        total_patients=total_patients,
        total_doctors=total_doctors,
        total_admins=total_admins,
        appointments=appointments
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
        # Check Email
    cursor.execute(
        "SELECT * FROM patient WHERE email=%s",
        (email,)
    )

    if cursor.fetchone():
        return "❌ Email already registered."

    # Check Username
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

    cursor.execute("""
        SELECT hospital_name
        FROM hospital
        WHERE hospital_name LIKE %s
        LIMIT 10
    """, ("%" + keyword + "%",))

    hospitals = [row["hospital_name"] for row in cursor.fetchall()]

    return jsonify(hospitals)


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
# ================= BOOK APPOINTMENT ================= #

@app.route("/book_appointment", methods=["GET", "POST"])
def book_appointment():

    # Patient Login Check
    if "patient_id" not in session:
        return redirect(url_for("patient_login"))

    patient_id = session["patient_id"]

    # Get patient's selected hospital
    cursor.execute("""
        SELECT hospital_name
        FROM patient
        WHERE patient_id=%s
    """, (patient_id,))

    patient = cursor.fetchone()
    hospital_name = patient["hospital_name"]

    # Default doctor list
    cursor.execute("""
        SELECT doctor_id,
               full_name,
               specialization
        FROM doctor
        ORDER BY full_name
    """)
    doctors = cursor.fetchall()

    suggested_department = ""

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

        # DON'T take hospital name from form
        reason = request.form["reason"]

        suggested_department = suggest_department(reason)

        # Get doctors of suggested department
        cursor.execute("""
            SELECT doctor_id,
                   full_name,
                   specialization
            FROM doctor
            WHERE specialization=%s
            ORDER BY full_name
        """, (suggested_department,))

        doctors = cursor.fetchall()

        doctor_id = request.form.get("doctor_id")

        if doctor_id:

            # Again fetch patient's hospital
            cursor.execute("""
                SELECT hospital_name
                FROM patient
                WHERE patient_id=%s
            """, (patient_id,))

            patient = cursor.fetchone()
            hospital_name = patient["hospital_name"]
            print("Selected Doctor ID:", doctor_id)
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
                (
                    %s,%s,%s,%s,%s,%s,%s
                )
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

def suggest_department(reason):
    reason = reason.lower()

    if any(word in reason for word in [
        "headache", "migraine", "brain", "dizzy",
        "fits", "stroke"
    ]):
        return "Neurology"

    elif any(word in reason for word in [
        "chest pain", "heart", "bp",
        "blood pressure", "palpitation"
    ]):
        return "Cardiology"

    elif any(word in reason for word in [
        "fever", "cold", "cough",
        "infection", "body pain"
    ]):
        return "General Medicine"

    elif any(word in reason for word in [
        "stomach", "vomit", "gas",
        "ulcer", "abdomen"
    ]):
        return "Gastroenterology"

    elif any(word in reason for word in [
        "skin", "rash", "itching",
        "pimple", "allergy"
    ]):
        return "Dermatology"

    elif any(word in reason for word in [
        "bone", "joint", "fracture",
        "leg pain", "back pain"
    ]):
        return "Orthopedics"

    elif any(word in reason for word in [
        "eye", "vision", "blur",
        "red eye"
    ]):
        return "Ophthalmology"

    elif any(word in reason for word in [
        "ear", "nose", "throat",
        "hearing"
    ]):
        return "ENT"

    else:
        return "General Medicine"
#==============appointment success page======================#

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

from flask import send_file
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
import io
from datetime import datetime
import os


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

    # Date Format
    appointment_date = appointment["appointment_date"].strftime("%d %b %Y")

    # Time Format
    appointment_time = datetime.strptime(
        str(appointment["appointment_time"]),
        "%H:%M:%S"
    ).strftime("%I:%M %p")

    buffer = io.BytesIO()

    pdf = canvas.Canvas(buffer)
    pdf.setTitle("Appointment Slip")

    # =====================================================
    # Header Background
    # =====================================================
    pdf.setStrokeColor(HexColor("#d9d9d9"))
    pdf.line(40, 755, 555, 755)

    # =====================================================
    # Hospital Logo
    # =====================================================
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
    # =====================================================
    # Header Text
    # =====================================================#
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

    # =====================================================
    # Title
    # =====================================================

    pdf.setFillColor(HexColor("#000000"))
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(300, 735, "APPOINTMENT SLIP")

    pdf.line(50, 725, 550, 725)

    # =====================================================
    # Patient Details
    # =====================================================

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
#===============Footer Box==================#
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

    # Signature
    pdf.line(390, 180, 540, 180)
    pdf.drawCentredString(465, 165, "Authorized Signature")

        # Signature
    pdf.line(390, 180, 540, 180)
    pdf.drawCentredString(465, 165, "Authorized Signature")

    # =====================================================
    # Save PDF
    # =====================================================
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


# ================= LOGOUT ================= #

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("home"))

# ================= FORGOT PASSWORD ================= #

@app.route("/forgot_password/<role>", methods=["GET", "POST"])
def forgot_password(role):

    # Valid roles only
    if role not in ["patient", "doctor", "admin"]:
        return "Invalid Role"

    if request.method == "POST":

        login_id = request.form["login_id"].strip()

        # Check user
        query = f"""
            SELECT full_name, email
            FROM {role}
            WHERE email=%s OR mobile=%s
        """

        cursor.execute(query, (login_id, login_id))
        user = cursor.fetchone()

        if not user:
            return "❌ Email or Mobile not found."

        # Generate OTP
        otp = str(random.randint(100000, 999999))

        # Save in session
        session["otp"] = otp
        session["login_id"] = login_id
        session["table"] = role

        # Send Mail
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

        # Go to Verify OTP page
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

import re

@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():

    # OTP verify pannama direct access panna kudathu
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

        # ================= OLD PASSWORD CHECK =================

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

        # ================= STRONG PASSWORD CHECK =================

        pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&#])[A-Za-z\d@$!%*?&#]{8,}$'

        if not re.match(pattern, password):
            return render_template(
                "reset_password.html",
                error="❌ Password must contain at least 8 characters, one uppercase letter, one lowercase letter, one number and one special character."
            )

        # ================= UPDATE PASSWORD =================

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

                # Save role before clearing session
        role = session["table"]

        # Clear session
        session.pop("otp", None)
        session.pop("otp_verified", None)
        session.pop("login_id", None)
        session.pop("table", None)

        # Success Page
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
    app.run(host="0.0.0.0", port=5000, debug=False)