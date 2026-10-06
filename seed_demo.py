"""Create fictional sample records in MongoDB for a project demonstration."""
from datetime import date, datetime, timedelta

from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash

from app import (appointments_collection, dose_logs, health_records,
                 medicine_collection, profiles, users)

EMAIL = "demo@gmail.com"
USERNAME = "healthdemo"
PASSWORD = "HealthDemo123!"

account = users.find_one({"email": EMAIL})
if account:
    print(f"Demo account already exists: {EMAIL} / {PASSWORD}")
else:
    try:
        created = users.insert_one({"email": EMAIL, "username": USERNAME,
                                    "password_hash": generate_password_hash(PASSWORD)})
        user_id = str(created.inserted_id)
        profiles.insert_one({"user_id": user_id, "full_name": "Alex Demo", "date_of_birth": None,
            "phone": "555-0100", "address": "Demo address", "emergency_name": "Sam Demo",
            "emergency_phone": "555-0101", "allergies": "Example: seasonal pollen",
            "important_notes": "Sample information only. Replace with your own.", "photo_filename": None})
        today = date.today()
        health_records.insert_one({"user_id": user_id, "record_date": (today - timedelta(days=3)).isoformat(),
            "record_type": "Sample check-up", "provider": "Example Family Clinic",
            "notes": "Fictional demo record. No real medical information.", "attachment_filename": None})
        medicine_result = medicine_collection.insert_one({"user_id": user_id, "name": "Demo vitamin",
            "dose": "1 tablet", "instructions": "Example schedule only.",
            "start_date": (today - timedelta(days=3)).isoformat(),
            "end_date": (today + timedelta(days=14)).isoformat(),
            "reminder_times": ["08:00", "20:00"], "active": True})
        medicine_id = str(medicine_result.inserted_id)
        appointments_collection.insert_one({"user_id": user_id,
            "appointment_at": (datetime.now() + timedelta(days=5)).isoformat(timespec="minutes"),
            "provider": "Example Clinic", "notes": "Fictional demo appointment."})
        for offset in range(1, 7):
            dose_logs.insert_one({"user_id": user_id, "medicine_id": medicine_id,
                "scheduled_date": (today - timedelta(days=offset)).isoformat(), "scheduled_time": "08:00",
                "status": "taken" if offset % 2 else "skipped", "updated_at": datetime.utcnow().isoformat()})
        print("Created fictional MongoDB demo account:")
        print(f"  Email:    {EMAIL}\n  Username: {USERNAME}\n  Password: {PASSWORD}")
    except DuplicateKeyError:
        print("A demo email or username already exists. No duplicate account was added.")
