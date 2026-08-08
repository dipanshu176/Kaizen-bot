import os
import smtplib
from email.message import EmailMessage
import gspread
import pandas as pd
from datetime import datetime
import pytz
import json

# Setup Email Credentials from GitHub Secrets
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
GCP_CREDENTIALS = json.loads(os.environ.get("GCP_CREDENTIALS"))

# Connect to Google Sheet
gc = gspread.service_account_from_dict(GCP_CREDENTIALS)
sh = gc.open_by_key("1_WmKztvT7p2X0Yj6M7fbwlLQ3SFbDFT7xMlhiLr1kQ8")

def send_email(to_email, subject, body):
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(SENDER_EMAIL, APP_PASSWORD)
        smtp.send_message(msg)

# Get current time in IST
ist_now = datetime.now(pytz.timezone('Asia/Kolkata'))
current_hour = ist_now.hour

users_df = pd.DataFrame(sh.worksheet("Users").get_all_records())
subs_df = pd.DataFrame(sh.worksheet("Submissions").get_all_records())

# Convert timestamps to dates for filtering
today_date = ist_now.strftime("%Y-%m-%d")

# ==========================================
# MIDNIGHT LOGIC: Did Heads submit today?
# ==========================================
if current_hour == 0: 
    heads = users_df[users_df['Role'].str.contains("Head")]
    
    # Check if they have an entry in the sheet with today's date
    todays_subs = subs_df[subs_df['Timestamp'].str.contains(today_date)]
    submitted_names = todays_subs['Name'].tolist()
    
    for _, head in heads.iterrows():
        if head['Name'] not in submitted_names:
            body = f"Hi {head['Name']},\n\nYou missed your EOD update deadline for {today_date}. Please update the Kaizen Portal immediately."
            send_email(head['Email'], "ACTION REQUIRED: Missing EOD Update", body)
            print(f"Warning sent to {head['Name']}")

# ==========================================
# 12:00 PM LOGIC: Did Senior Core verify yesterday's?
# ==========================================
elif current_hour == 12: 
    pending = subs_df[subs_df['Status'] == 'Pending']
    
    if not pending.empty:
        # Alert all Advisory Board members and the ED
        advisors = users_df[users_df['Role'].isin(["Advisory Board", "Executive Director"])]
        advisor_emails = advisors['Email'].tolist()
        
        body = f"Hello Senior Core,\n\nThere are {len(pending)} unverified updates remaining in the Kaizen Portal. Please log in and verify them."
        
        for email in advisor_emails:
            send_email(email, "ACTION REQUIRED: Pending Verifications", body)
        print("Warning sent to Senior Core.")
