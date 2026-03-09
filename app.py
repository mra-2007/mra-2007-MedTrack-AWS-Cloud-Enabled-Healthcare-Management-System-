from flask import Flask, render_template, request, redirect, session
import boto3
import uuid
import logging

app = Flask(__name__)
app.secret_key = "medtrack_secret_key"

# ----------------------------
# Logging Setup
# ----------------------------
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ----------------------------
# AWS Configuration
# ----------------------------
REGION = "us-east-1"  # Change if needed N. Virginia 
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:050451373350:Medtrack"

dynamodb = boto3.resource('dynamodb', region_name=REGION)
users_table = dynamodb.Table('UsersTable')
appointments_table = dynamodb.Table('AppointmentsTable')

sns = boto3.client('sns', region_name=REGION)

# ----------------------------
# Home
# ----------------------------
@app.route("/")
def home():
    return render_template("index.html")

# ----------------------------
# Register
# ----------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        users_table.put_item(
            Item={
                "email": request.form["email"],
                "name": request.form["name"],
                "password": request.form["password"],
                "role": request.form["role"],
                "login_count": 0
            }
        )
        logging.info("New user registered")
        return redirect("/login")

    return render_template("register.html")

# ----------------------------
# Login
# ----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        response = users_table.get_item(Key={"email": email})

        if "Item" in response and response["Item"]["password"] == password:
            session["user"] = email
            session["role"] = response["Item"]["role"]

            users_table.update_item(
                Key={"email": email},
                UpdateExpression="SET login_count = login_count + :val",
                ExpressionAttributeValues={":val": 1}
            )

            logging.info(f"{email} logged in")

            if session["role"] == "doctor":
                return redirect("/doctor_dashboard")
            else:
                return redirect("/patient_dashboard")

        return "Invalid Credentials"

    return render_template("login.html")

# ----------------------------
# Logout
# ----------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ----------------------------
# Dashboards
# ----------------------------
@app.route("/doctor_dashboard")
def doctor_dashboard():
    if "user" not in session:
        return redirect("/login")
    return render_template("doctor_dashboard.html")

@app.route("/patient_dashboard")
def patient_dashboard():
    if "user" not in session:
        return redirect("/login")
    return render_template("patient_dashboard.html")

# ----------------------------
# Book Appointment
# ----------------------------
@app.route("/book_appointment", methods=["GET", "POST"])
def book_appointment():
    if "user" not in session:
        return redirect("/login")

    if request.method == "POST":
        appointment_id = str(uuid.uuid4())

        appointments_table.put_item(
            Item={
                "appointment_id": appointment_id,
                "patient_email": session["user"],
                "doctor_email": request.form["doctor_email"],
                "date": request.form["date"],
                "time": request.form["time"],
                "status": "Scheduled"
            }
        )

        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=f"New appointment booked on {request.form['date']} at {request.form['time']}",
            Subject="New Appointment"
        )

        logging.info("Appointment booked")

        return redirect("/view_appointment_patient")

    return render_template("book_appointment.html")

# ----------------------------
# View Doctor Appointments
# ----------------------------
@app.route("/view_appointment_doctor")
def view_appointment_doctor():
    if "user" not in session:
        return redirect("/login")

    doctor_email = session["user"]

    response = appointments_table.scan()
    appointments = [
        item for item in response.get("Items", [])
        if item.get("doctor_email") == doctor_email
    ]

    return render_template("view_appointment_doctor.html", appointments=appointments)

# ----------------------------
# View Patient Appointments
# ----------------------------
@app.route("/view_appointment_patient")
def view_appointment_patient():
    if "user" not in session:
        return redirect("/login")

    patient_email = session["user"]

    response = appointments_table.scan()
    appointments = [
        item for item in response.get("Items", [])
        if item.get("patient_email") == patient_email
    ]

    return render_template("view_appointment_patient.html", appointments=appointments)

# ----------------------------
# Submit Diagnosis
# ----------------------------
@app.route("/submit_diagnosis", methods=["GET", "POST"])
def submit_diagnosis():
    if "user" not in session:
        return redirect("/login")

    if request.method == "POST":
        appointment_id = request.form["appointment_id"]
        diagnosis = request.form["diagnosis"]

        appointments_table.update_item(
            Key={"appointment_id": appointment_id},
            UpdateExpression="SET diagnosis = :d, #s = :status",
            ExpressionAttributeValues={
                ":d": diagnosis,
                ":status": "Completed"
            },
            ExpressionAttributeNames={
                "#s": "status"
            }
        )

        logging.info("Diagnosis submitted")

        return redirect("/view_appointment_doctor")

    appointment_id = request.args.get("appointment_id")
    return render_template("submit_diagnosis.html", appointment_id=appointment_id)

# ----------------------------
# Search Appointment
# ----------------------------
@app.route("/search", methods=["POST"])
def search():
    search_date = request.form["date"]

    response = appointments_table.scan()
    results = [
        item for item in response.get("Items", [])
        if item.get("date") == search_date
    ]

    return render_template("search_results.html", appointments=results)

# ----------------------------
# Health Check
# ----------------------------
@app.route("/health")
def health():
    return {"status": "Application Running"}, 200

# ----------------------------
# Run App
# ----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)