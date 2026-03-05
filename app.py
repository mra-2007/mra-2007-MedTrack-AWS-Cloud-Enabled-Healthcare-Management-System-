from flask import Flask, request, session, redirect, url_for, render_template, flash
import boto3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
import os
import uuid
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ------------------------------
# Flask App Initialization
# ------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'temporary_key_for_development')

# ------------------------------
# App Configuration
# ------------------------------
AWS_REGION_NAME = os.environ.get('AWS_REGION_NAME', 'ap-south-1')

# Email Configuration
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SENDER_EMAIL = os.environ.get('SENDER_EMAIL')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD')
ENABLE_EMAIL = os.environ.get('ENABLE_EMAIL', 'False').lower() == 'true'

# Table Names from .env
USERS_TABLE_NAME = os.environ.get('USERS_TABLE_NAME', 'UsersTable')
APPOINTMENTS_TABLE_NAME = os.environ.get('APPOINTMENTS_TABLE_NAME', 'AppointmentsTable')

# SNS Configuration
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
ENABLE_SNS = os.environ.get('ENABLE_SNS', 'False').lower() == 'true'

# Backwards-compatible flags used in code
EMAIL_NOTIFICATIONS = ENABLE_EMAIL
SMS_NOTIFICATIONS = ENABLE_SNS

# -----------------------------------
# AWS Resources
# -----------------------------------
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION_NAME)
sns = boto3.client('sns', region_name=AWS_REGION_NAME)

# DynamoDB Tables
user_table = dynamodb.Table(USERS_TABLE_NAME)
appointment_table = dynamodb.Table(APPOINTMENTS_TABLE_NAME)

# -----------------------------------
# Logging
# -----------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# -----------------------------------
# Helper Functions
# -----------------------------------
def is_logged_in():
    return 'email' in session

def get_user_role(email):
    try:
        response = user_table.get_item(Key={'email': email})
    except Exception as e:
        logger.error(f"Error fetching role: {e}")
        return None

def send_email(to_email, subject, body):
    if not ENABLE_EMAIL:
        logger.info(f"[Email Skipped] Subject: {subject} to {to_email}")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()

        logger.info(f"Email sent to {to_email}")
    except Exception as e:
        logger.error(f"Email sending failed: {e}")

def publish_to_sns(message, subject="Salon Notification"):
    if not ENABLE_SNS:
        logger.info("[SNS Skipped] Message: {}".format(message))
        return

    try:
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject=subject
        )
        logger.info(f"SNS published : {response['MessageId']}")
    except Exception as e:
        logger.error(f"SNS publish failed: {e}")

# Home Page
@app.route('/')
def index():
    if is_logged_in():
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# Register User (doctor/patient)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if is_logged_in():  # Check if already logged in
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # Form validation
        required_fields = ['name', 'email', 'password', 'age', 'gender', 'role']
        for field in required_fields:
            if field not in request.form or not request.form[field]:
                flash(f'Please provide {field}', 'danger')
                return render_template('register.html')

        # Check if passwords match
        if 'confirm_password' in request.form and request.form['password'] != request.form['confirm_password']:
            flash('Passwords do not match', 'danger')
            return render_template('register.html')

        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])  # Hash password
        age = request.form['age']
        gender = request.form['gender']
        role = request.form['role']  # 'doctor' or 'patient'

        # Check if user already exists
        existing_user = user_table.get_item(Key={'email': email}).get('Item')

        if existing_user:
            flash('Email already registered', 'danger')
            return render_template('register.html')

        # Create user record
        user_item = {
            'email': email,
            'name': name,
            'password': password,
            'age': age,
            'gender': gender,
            'role': role,
            'created_at': datetime.now().isoformat()
        }

        try:
            user_table.put_item(Item=user_item)
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            flash('Registration failed. Please try again.', 'danger')
            return render_template('register.html')

        # Send welcome email if enabled
        if ENABLE_EMAIL:
            welcome_msg = f"Welcome to HealthCare App, {name}! Your account has been created successfully."
            send_email(email, "Welcome to HealthCare App", welcome_msg)

        # Send admin notification via SNS if configured
        if ENABLE_SNS and SNS_TOPIC_ARN:
            try:
                sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Message=f"New user registered: {name} ({email}) as {role}",
                    Subject="New User Registration - HealthCare App"
                )
            except Exception as e:
                logger.error(f"Failed to publish to SNS: {e}")

        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


# Login User (Doctor/Patient)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_logged_in():  # If the user is already logged in, redirect to dashboard
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        if not request.form.get('email') or not request.form.get('password') or not request.form.get('role'):
            flash('All fields are required.', 'danger')
            return render_template('login.html')

        email = request.form['email']
        password = request.form['password']
        role = request.form['role']  # Get the selected role (doctor or patient)

        # Validate user credentials
        user = user_table.get_item(Key={'email': email}).get('Item')

        if user:
            # Check password and role
            if check_password_hash(user['password'], password):  # Use check_password_hash to verify hashed password
                if user['role'] == role:
                    session['email'] = email
                    session['role'] = role  # Store the role in the session
                    session['name'] = user.get('name', '')

                    # Update login count
                    try:
                        user_table.update_item(
                            Key={'email': email},
                            UpdateExpression="SET login_count = if_not_exists(login_count, :zero) + :inc",
                            ExpressionAttributeValues={':inc': 1, ':zero': 0}
                        )
                    except Exception as e:
                        logger.error(f"Failed to update login count: {e}")

                    flash('Login successful.', 'success')
                    return redirect(url_for('dashboard'))
                else:
                    flash('Invalid role selected.', 'danger')
            else:
                flash('Invalid password.', 'danger')
        else:
            flash('Email not found.', 'danger')

        return render_template('login.html')


# Logout User
@app.route('/logout')
def logout():
    session.pop('email', None)
    session.pop('role', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

# Dashboard for both Doctors and Patients
@app.route('/dashboard')
def dashboard():
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    role = session.get('role')
    email = session.get('email')

    appointments = []

    if role == 'doctor':
        # Use GSI instead of scan for better performance
        try:
            response = appointment_table.query(
                IndexName='DoctorEmailIndex',
                KeyConditionExpression="doctor_email = :email",
                ExpressionAttributeValues={":email": email}
            )
            appointments = response.get('Items', [])
        except Exception as e:
            logger.error(f"Failed to fetch appointments: {e}")
            # Fallback to scan if GSI is not yet created
            try:
                response = appointment_table.scan(
                    FilterExpression="#doctor_email = :email",
                    ExpressionAttributeNames={"#doctor_email": "doctor_email"},
                    ExpressionAttributeValues={":email": email}
                )
                appointments = response.get('Items', [])
            except Exception as ex:
                logger.error(f"Fallback scan failed: {ex}")

        return render_template('doctor_dashboard.html', appointments=appointments)

    elif role == 'patient':
        # Use GSI instead of scan for better performance
        try:
            response = appointment_table.query(
                IndexName='PatientEmailIndex',
                KeyConditionExpression="patient_email = :email",
                ExpressionAttributeValues={":email": email}
            )
            appointments = response.get('Items', [])
        except Exception as e:
            logger.error(f"Failed to query appointments: {e}")
            # Fallback to scan if GSI is not yet created
            try:
                response = appointment_table.scan(
                    FilterExpression="#patient_email = :email",
                    ExpressionAttributeNames={"#patient_email": "patient_email"},
                    ExpressionAttributeValues={":email": email}
                )
                appointments = response.get('Items', [])
            except Exception as ex:
                logger.error(f"Fallback scan failed: {ex}")

        # Get list of doctors for booking new appointments
        try:
            doctor_response = user_table.scan(
                FilterExpression="#role = :role",
                ExpressionAttributeNames={"#role": "role"},
                ExpressionAttributeValues={":role": "doctor"}
            )
            doctors = doctor_response.get('Items', [])
        except Exception as e:
            logger.error(f"Failed to fetch doctors: {e}")
            doctors = []

        return render_template('patient_dashboard.html', appointments=appointments, doctors=doctors)

    # Book an Appointment (Patient)
@app.route('/book_appointment', methods=['GET', 'POST'])
def book_appointment():
    if not is_logged_in() or session['role'] != 'patient':
        flash('Only patients can book appointments.', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        # Form validation
        if not request.form.get('doctor_email') or not request.form.get('symptoms'):
            flash('Please fill all required fields.', 'danger')
            return redirect(url_for('book_appointment'))

        doctor_email = request.form['doctor_email']
        symptoms = request.form['symptoms']
        appointment_date = request.form.get('appointment_date', datetime.now().isoformat())
        patient_email = session['email']

        # Get patient and doctor information for notifications
        try:
            patient = user_table.get_item(Key={'email': patient_email}).get('Item', {})
            doctor = user_table.get_item(Key={'email': doctor_email}).get('Item', {})

            patient_name = patient.get('name', 'Patient')
            doctor_name = doctor.get('name', 'Doctor')

            # Create a new appointment
            appointment_id = str(uuid.uuid4())
            appointment_item = {
                'appointment_id': appointment_id,
                'doctor_email': doctor_email,
                'doctor_name': doctor_name,
                'patient_email': patient_email,
                'patient_name': patient_name,
                'symptoms': symptoms,
                'status': 'pending',
                'appointment_date': appointment_date,
                'created_at': datetime.now().isoformat(),
            }

            appointment_table.put_item(Item=appointment_item)

            # Send email notifications if enabled
            if EMAIL_NOTIFICATIONS:
                # Send email notification to doctor
                doctor_subject = "New Appointment Booked"
                doctor_message = (
                    f"Hello {doctor_name},\n\n"
                    f"A new appointment has been booked by {patient_name}.\n"
                    f"Symptoms: {symptoms}\n"
                    f"Date: {appointment_date}\n\n"
                    f"Please log in to your dashboard to manage appointments."
                )
                send_email(doctor_email, doctor_subject, doctor_message)

                # Send confirmation email to patient
                patient_subject = "Your Appointment Confirmation"
                patient_message = (
                    f"Hello {patient_name},\n\n"
                    f"Your appointment with Dr. {doctor_name} has been booked successfully.\n"
                    f"Date: {appointment_date}\n\n"
                    f"Thank you for using our service."
                )
                send_email(patient_email, patient_subject, patient_message)

            # Send SMS notification if configured
            if SMS_NOTIFICATIONS:
                try:
                    sns.publish(
                        PhoneNumber=patient.get('phone'),
                        Message=f"Appointment booked: {patient_name} with Dr. {doctor_name} for date {appointment_date}"
                    )
                except Exception as e:
                    logger.error(f"Failed to publish to SNS: {e}")

            flash('Appointment booked successfully.', 'success')
            return redirect(url_for('dashboard'))

        except Exception as e:
            logger.error(f"Error booking appointment: {e}")
            flash('An error occurred while booking the appointment. Please try again.', 'danger')
            return redirect(url_for('book_appointment'))

    # Get list of doctors for selection
    try:
        response = user_table.scan(
            FilterExpression="#role = :role",
            ExpressionAttributeNames={"#role": "role"},
            ExpressionAttributeValues={":role": "doctor"}
        )
        doctors = response.get('Items', [])
    except Exception as e:
        logger.error(f"Failed to fetch doctors: {e}")
        doctors = []

    return render_template('book_appointment.html', doctors=doctors)


# View Appointment (Doctor)
@app.route('/view_appointment/<appointment_id>', methods=['GET', 'POST'])
def view_appointment(appointment_id):
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    try:
        response = appointment_table.get_item(Key={'appointment_id': appointment_id})
        appointment = response.get('Item')

        if not appointment:
            flash('Appointment not found.', 'danger')
            return redirect(url_for('dashboard'))

        # Authorization check
        if session.get('role') == 'doctor' and appointment.get('doctor_email') != session.get('email'):
            flash('You are not authorized to view this appointment.', 'danger')
            return redirect(url_for('dashboard'))
        if session.get('role') == 'patient' and appointment.get('patient_email') != session.get('email'):
            flash('You are not authorized to view this appointment.', 'danger')
            return redirect(url_for('dashboard'))

        # Doctor submits diagnosis from this view
        if request.method == 'POST' and session.get('role') == 'doctor':
            diagnosis = request.form.get('diagnosis', '')
            treatment_plan = request.form.get('treatment_plan', '')
            prescription = request.form.get('prescription', '')

            appointment_table.update_item(
                Key={'appointment_id': appointment_id},
                UpdateExpression="SET diagnosis = :diagnosis, treatment_plan = :treatment, prescription = :prescription, #s = :status, completed_at = :datetime",
                ExpressionAttributeValues={
                    ':diagnosis': diagnosis,
                    ':treatment': treatment_plan,
                    ':prescription': prescription,
                    ':status': 'completed',
                    ':datetime': datetime.now().isoformat()
                },
                ExpressionAttributeNames={
                    '#s': 'status'
                }
            )

            # Notify patient via email if configured
            if ENABLE_EMAIL:
                patient_email = appointment.get('patient_email')
                patient_name = appointment.get('patient_name', 'Patient')
                doctor_name = appointment.get('doctor_name', session.get('name', 'Doctor'))

                patient_msg = (
                    f"Dear {patient_name},\n\n"
                    f"Your appointment with Dr. {doctor_name} has been completed.\n\n"
                    f"Diagnosis: {diagnosis}\n"
                    f"Treatment Plan: {treatment_plan}\n\n"
                    f"Login to view full details.\n\n"
                    f"- Clinic Management System"
                )
                send_email(patient_email, "Appointment Completed - Diagnosis Available", patient_msg)

            flash("Diagnosis submitted successfully.", "success")
            return redirect(url_for('dashboard'))

        # Render appropriate view
        if session.get('role') == 'doctor':
            return render_template('view_appointment_doctor.html', appointment=appointment)
        else:
            return render_template('view_appointment_patient.html', appointment=appointment)

    except Exception as e:
        logger.error(f"Error in view_appointment: {e}")
        flash("An error occurred. Please try again.", "danger")
        return redirect(url_for('dashboard'))


# Search functionality for appointments
@app.route('/search_appointments', methods=['GET', 'POST'])
def search_appointments():
    if not is_logged_in():
        flash("Please login to continue.", "danger")
        return redirect(url_for('login'))

    search_term = ''
    if request.method == 'POST':
        search_term = request.form.get('search_term', '')

    try:
        if session.get('role') == 'doctor':
            # Doctors can search their patients by name
            response = appointment_table.scan(
                FilterExpression="#doctor_email = :email AND contains(#patient_name, :search)",
                ExpressionAttributeNames={
                    "#doctor_email": "doctor_email",
                    "#patient_name": "patient_name"
                },
                ExpressionAttributeValues={
                    ":email": session.get('email'),
                    ":search": search_term
                }
            )
        else:  # patient
            # Patients can search their appointments by doctor name or status
            response = appointment_table.scan(
                FilterExpression="#patient_email = :email AND (contains(#doctor_name, :search) OR contains(#status, :search))",
                ExpressionAttributeNames={
                    "#patient_email": "patient_email",
                    "#doctor_name": "doctor_name",
                    "#status": "status"
                },
                ExpressionAttributeValues={
                    ":email": session.get('email'),
                    ":search": search_term
                }
            )

        appointments = response.get('Items', [])
        return render_template(
            'search_results.html',
            appointments=appointments,
            search_term=search_term
        )

    except Exception as e:
        logger.error(f"Search failed: {e}")
        flash("Search failed. Please try again.", "danger")

    return redirect(url_for('dashboard'))


# profile page
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    email = session.get('email')
    try:
        user = user_table.get_item(Key={'email': email}).get('Item', {})

        if request.method == 'POST':
            # Update user profile
            name = request.form.get('name')
            age = request.form.get('age')
            gender = request.form.get('gender')

            update_expression = "SET #name = :name, age = :age, gender = :gender"
            expression_values = {
                ':name': name,
                ':age': age,
                ':gender': gender
            }

            # Update specialization only for doctors
            if session.get('role') == 'doctor' and 'specialization' in request.form:
                update_expression += ", specialization = :spec"
                expression_values[':spec'] = request.form['specialization']

            user_table.update_item(
                Key={'email': email},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames={"#name": "name"}
            )

            # Update session name
            session['name'] = name

            flash('Profile updated successfully.', 'success')
            return redirect(url_for('profile'))

        return render_template('profile.html', user=user)

    except Exception as e:
        logger.error(f"Profile error: {e}")
        flash('An error occurred. Please try again.', 'danger')
        return redirect(url_for('dashboard'))


# Submit Diagnosis (Doctor) - For form submission
@app.route('/submit_diagnosis/<appointment_id>', methods=['POST'])
def submit_diagnosis(appointment_id):
    if not is_logged_in() or session.get('role') != 'doctor':
        flash('Unauthorized.', 'danger')
        return redirect(url_for('login'))

    try:
        diagnosis = request.form.get('diagnosis', '')
        treatment_plan = request.form.get('treatment_plan', '')
        prescription = request.form.get('prescription', '')

        appointment_table.update_item(
            Key={'appointment_id': appointment_id},
            UpdateExpression="SET diagnosis = :diagnosis, treatment_plan = :treatment, prescription = :prescription, #s = :status, completed_at = :datetime",
            ExpressionAttributeValues={
                ':diagnosis': diagnosis,
                ':treatment': treatment_plan,
                ':prescription': prescription,
                ':status': 'completed',
                ':datetime': datetime.now().isoformat()
            },
            ExpressionAttributeNames={
                '#s': 'status'
            }
        )

        # Fetch appointment to get patient details for notifications
        appt = appointment_table.get_item(Key={'appointment_id': appointment_id}).get('Item', {})

        if ENABLE_EMAIL:
            patient_email = appt.get('patient_email')
            patient_name = appt.get('patient_name', 'Patient')
            doctor_name = session.get('name', 'Your doctor')

            patient_msg = f"Dear {patient_name},\n\nYour appointment with Dr. {doctor_name} has been completed.\n\nDiagnosis: {diagnosis}\n\nTreatment Plan: {treatment_plan}\n\nPlease login to your account to see full details.\n\nRegards,\nDoctor Appointment System"
            send_email(patient_email, "Appointment Completed - Diagnosis Available", patient_msg)

        flash("Diagnosis submitted successfully.", "success")
        return redirect(url_for('dashboard'))

    except Exception as e:
        logger.error(f"Submit diagnosis error: {e}")
        flash("An error occurred while submitting the diagnosis. Please try again.", "danger")
        return redirect(url_for('view_appointment', appointment_id=appointment_id))


# Health check endpoint for AWS load balancers
@app.route('/health')
def health():
    return {"status": "healthy"}, 200


# Run the Flask app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)