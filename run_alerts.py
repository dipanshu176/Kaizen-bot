import os
import smtplib
from email.message import EmailMessage
import gspread
import pandas as pd
from datetime import datetime
import re
import pytz
import json
import pytz
from datetime import datetime, timedelta
# Setup Email Credentials from GitHub Secrets
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
GCP_CREDENTIALS = json.loads(os.environ.get("GCP_CREDENTIALS"))

# Connect to Google Sheet
gc = gspread.service_account_from_dict(GCP_CREDENTIALS)
sh = gc.open_by_key("1_WmKztvT7p2X0Yj6M7fbwlLQ3SFbDFT7xMlhiLr1kQ8")
spreadsheet = gc.open("Kaizen_Management_Database")
sheet = spreadsheet.worksheet("Submissions")

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
if current_hour in [0,1,2]: 
    heads = users_df[users_df['Role'].str.contains("Head")]
    
    # Check if they have an entry in the sheet with today's date
    subs_data = sheet.get_all_records()
    subs_df = pd.DataFrame(subs_data)
    
    # We must check YESTERDAY'S date because it is currently past midnight
    yesterday_date = (ist_now - timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Check if the sheet is completely empty to prevent the KeyError
    if subs_df.empty or 'Timestamp' not in subs_df.columns:
        submitted_names = [] # No one has submitted anything today
    else:
        # Filter for yesterday's date, not today's
        todays_subs = subs_df[subs_df['Timestamp'].astype(str).str.contains(yesterday_date)]
        submitted_names = todays_subs['Name'].tolist()
    
    for _, head in heads.iterrows():
        if head['Name'] not in submitted_names:
            
            # --- ADDED: LEAVE IMMUNITY CHECK ---
            on_leave = False
            if not subs_df.empty:
                # Pull this specific Head's entire history
                head_history = subs_df[subs_df['Name'] == head['Name']].sort_values(by='Timestamp', ascending=False)
                if not head_history.empty:
                    last_sub = head_history.iloc[0]
                    status_text = str(last_sub.get('Submission_Data', ''))
                    
                    # Scan their last submission for the expected return days
                    match = re.search(r'Expected return in: (\d+)', status_text)
                    if match:
                        leave_days = int(match.group(1))
                        last_date_str = str(last_sub['Timestamp']).split(' ')[0]
                        try:
                            # Calculate exactly when they are supposed to return
                            last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
                            return_date = last_date + timedelta(days=leave_days)
                            
                            # Check if today is still covered by their leave
                            if ist_now.date() < return_date:
                                on_leave = True
                        except Exception:
                            pass 
            
            if on_leave:
                print(f"Skipping {head['Name']} - currently on approved leave.")
                continue # Stops right here, bypassing the email and penalty!
            # -----------------------------------
            
            # --- MODIFIED: EMAIL & PENALTY LOGGING ---
            # Email body updated to explicitly state 'yesterday_date'
            body = f"Hi {head['Name']},\n\nYou missed your EOD update deadline for {yesterday_date}. Please update the Kaizen Portal immediately."
            send_email(head['Email'], "ACTION REQUIRED: Missing EOD Update", body)
            
            # Inject the -1 Penalty Row directly into the Google Sheet
            # Note: Ensure these 5 columns match the exact layout of your Submissions sheet
            sheet.append_row([yesterday_date, head['Name'], head['Role'], "System Auto-Log: Failed to submit EOD update.", "Missed Deadline"])
            time.sleep(2)
            print(f"Warning sent and penalty logged for {head['Name']}")

# ==========================================
# 12:00 PM LOGIC: Did Senior Core verify yesterday's?
# ==========================================
elif current_hour in [12,13,14]:
    # Fetch the data
    subs_data = sheet.get_all_records()
    subs_df = pd.DataFrame(subs_data)

   
    
    # Check if the sheet is empty to prevent crashes
    if not subs_df.empty and 'Status' in subs_df.columns:
        
        # Look specifically for submissions that are still 'Pending'
        pending_subs = subs_df[subs_df['Status'] == 'Pending']
        
        # Only send the email if the pending list is NOT empty
        if not pending_subs.empty:
            pending_count = len(pending_subs)
            
            # Setup your email to the Advisory Board
            subject = "Kaizen Portal: Pending Verifications"
            body = f"Hello Senior Core,\n\nThere are currently {pending_count} pending updates waiting to be verified in the portal.\n\nPlease log in to review them."
            
            # Send the email (assuming you loop through your Advisory board emails here)
            for board_email in advisory_emails:
                send_email(board_email, subject, body)
                
            print(f"Alert sent to Advisory Board for {pending_count} pending items.")
        else:
            print("No pending submissions. Skipping Advisory Board email.")
    else:
        print("Sheet is empty. Skipping Advisory Board email.")
