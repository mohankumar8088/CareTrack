# CareTrack

A beginner-friendly Flask mini-project that stores personal health information in MongoDB. It is for record keeping and student demonstrations only. It does not diagnose conditions, recommend treatment, or replace a medical professional.

## Features

- Gmail-only account creation, username/email sign-in, and securely hashed passwords.
- Patient profile and emergency information, with optional profile photo.
- Dated health records with optional PDF/image attachment.
- Medicine schedules, reminder times, taken/skipped tracking, and a weekly Chart.js chart.
- Appointment calendar with upcoming and past lists.
- Dashboard and account-specific ZIP export.
- Per-user ownership checks and MongoDB indexes for unique accounts and common lookups.
- Upload validation for allowed extensions and file signatures, random safe filenames, and an 8 MB limit.

## Requirements

- Python 3.10 or newer
- A MongoDB database: a local MongoDB Community Server, or a MongoDB Atlas cluster
- Internet access for the Bootstrap and Chart.js CDN resources

The Python app connects with PyMongo. The default local connection is `mongodb://localhost:27017`, and the default database is `personal_health_records`. MongoDB creates the database and collections when the app first writes data. The app creates indexes when it starts.

## Configure MongoDB

### Option A: local MongoDB

Install and start MongoDB Community Server for your operating system. Keep the default address `mongodb://localhost:27017`. Confirm the MongoDB service is running before you start the Flask app.

### Option B: MongoDB Atlas

Create a cluster and a database user in Atlas, allow your computer's IP address in the network access list, and copy the Python driver connection string. In PowerShell, set it for the current terminal before starting the app:

```powershell
$env:MONGODB_URI = "mongodb+srv://mohankumar808859_db_user:cy1LKOIEHW4sLTFi@YOUR_CLUSTER.mongodb.net/?retryWrites=true&w=majority"
```

Replace the placeholders with your cluster details. If the password has special URL characters, percent-encode them. Keep this URI private and do not commit it to Git. The app uses the database name `personal_health_records` unless `MONGODB_DATABASE` is set.

MongoDB data is structured as documents inside collections. This project uses these collections:

| Collection | What it stores | How it connects to the account |
| --- | --- | --- |
| `users` | Login email, username, password hash | MongoDB `_id` is the account ID |
| `profiles` | Personal and emergency details | `user_id` stores the account ID as text |
| `health_records` | Report date, type, provider, notes, attachment filename | `user_id` |
| `medicines` | Medicine details, date range, reminder time list, active/pause state | `user_id` |
| `dose_logs` | Scheduled date/time and taken/skipped result | `user_id` and `medicine_id` |
| `appointments` | Appointment date/time, provider, notes | `user_id` |
| `uploaded_files` | PDF/image bytes, filename, content type | `user_id` |

The profile is a separate document because it is edited independently. Health records, medicines, dose history, and appointments are also separate documents so they can be added and queried as the user needs them. Queries always include the signed-in account's `user_id` for private data.

## Deploy to Vercel

Vercel currently detects Flask apps that expose a top-level `app` in `app.py`. This project includes `.python-version` for Python 3.12. Vercel's Python runtime is currently marked Beta. See [Vercel's Flask guide](https://vercel.com/docs/frameworks/backend/flask).

1. Push the project to GitHub. Do not commit `.venv`, your MongoDB URI, or other credentials.
2. Create a MongoDB Atlas cluster and database user. Configure Atlas network access for the deployment. Avoid broad access rules for a real service; use only fictional data for a student demonstration.
3. Import the GitHub repository at [vercel.com/new](https://vercel.com/new). Let Vercel detect the Flask app; no custom build command or output folder is needed.
4. In the Vercel project's **Settings → Environment Variables**, add:
   - `MONGODB_URI`: your Atlas Python connection string with your cluster, database user, and password.
   - `MONGODB_DATABASE`: `personal_health_records`
   - `SECRET_KEY`: generate a long random value locally with `python -c "import secrets; print(secrets.token_hex(32))"`.
   Add the variables for Production (and Preview if you want preview deployments to connect to the database).
5. Redeploy after saving the variables. Visit the Vercel URL, create an account, and try a fictional record, attachment, and backup ZIP.
6. Push future code changes to GitHub; Vercel creates a new deployment from each commit.

Vercel serves static assets from `public/` and ignores Flask's `static/` folder. The deployed stylesheet is [public/style.css](public/style.css). Vercel Functions have a read-only filesystem except for temporary `/tmp` storage, so this app saves uploaded file bytes in MongoDB's `uploaded_files` collection to keep them persistent. See [Vercel's static asset guidance](https://vercel.com/docs/frameworks/backend/flask#serving-static-assets) and [filesystem limits](https://vercel.com/docs/functions/runtimes#file-system-support).

For a deployment test, run the optional seed locally with the production `MONGODB_URI` temporarily set to your Atlas connection string, or simply register through the deployed app. Never seed a real account with the public demo password.

## Install and run on Windows (PowerShell)

Open PowerShell in this project folder. Start MongoDB first, then run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Visit <http://127.0.0.1:5000>. If PowerShell blocks virtual environment activation, run `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then use `.\.venv\Scripts\python.exe app.py`.

On macOS/Linux, create a virtual environment with `python3 -m venv .venv`, activate it with `source .venv/bin/activate`, install `requirements.txt`, and run `python app.py`.

## Optional fictional demo data

With MongoDB running and dependencies installed, run `python seed_demo.py`. It adds sample data if `demo@gmail.com` is not already in the database. Sign in with:

- Email: `demo@gmail.com`
- Username: `healthdemo`
- Password: `HealthDemo123!`

The example is fictional. Its password is public, so never use this demo account for real personal information.

## Beginner walkthrough

1. Register with a Gmail address, username, password, and password confirmation. The app checks the Gmail domain but does not send email verification.
2. Add personal and emergency information in **Profile**.
3. Add reports in **Health records**. Attachments are optional and limited to 8 MB.
4. Add a medicine schedule. Enter reminders like `08:00, 20:00` in 24-hour time. These are shown in the app; there is no phone notification service.
5. Use the dashboard to mark today's doses **Taken** or **Skipped**. Open **Medicines** for the weekly chart.
6. Add appointments and review them in upcoming or past lists.
7. Download a ZIP backup with your profile, records, schedules, dose history, appointments, and attachments.

## Project structure

```text
app.py                 Flask routes, PyMongo collections, validation, access checks
seed_demo.py           Fictional MongoDB demo account and example data
requirements.txt       Flask, Flask-Login, and PyMongo
templates/             Jinja HTML pages
public/style.css       Stylesheet served by Vercel and locally
static/style.css       Local source copy of stylesheet
```

## Notes and limitations

- Existing data in the old SQLite file is **not automatically migrated**. Start fresh in MongoDB, or export the old data and import it separately before switching if you need to preserve it.
- Uploaded file bytes (up to 8 MB each) are stored in MongoDB's `uploaded_files` collection. This avoids relying on serverless temporary storage and allows the ZIP export to include them.
- The app uses local computer time for dates. It does not send reminder notifications.
- This is a learning/demo app, not a production healthcare service. Do not put real patient data on a public server. For deployment, configure a long random `SECRET_KEY`, disable debug mode, use HTTPS, protect MongoDB credentials, and review privacy/security requirements.
- If MongoDB is unavailable or the URI is incorrect, the app cannot create its indexes or save data. Start MongoDB or correct `MONGODB_URI` before launching Flask.

## Suggested viva explanation

Flask receives a request and checks the signed-in account. PyMongo sends a query or update to MongoDB. MongoDB stores each item as a document in the appropriate collection, linked to the account through `user_id`. Jinja renders the page, Bootstrap styles it, and Chart.js draws the weekly dose summary. For a backup, Python reads only that account's documents and creates a ZIP in memory.
