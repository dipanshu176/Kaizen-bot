import streamlit as st
import gspread
import pandas as pd
from datetime import datetime, timedelta
import pytz
import json
import smtplib
from email.message import EmailMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from googleapiclient.errors import HttpError
import requests
import base64
import re
import uuid
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==========================================
# CONFIGURATION & AUTHENTICATION
# ==========================================
st.set_page_config(page_title="Kaizen Chronicles Portal", layout="wide")

@st.cache_resource
def get_google_credentials():
    creds_dict = dict(st.secrets["gcp_service_account"])
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    return service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)

# --- NEW: CACHED GOOGLE CONNECTION ---
@st.cache_resource
def connect_to_google():
    # Keep however you are currently defining 'gc' here
    gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"]) 
    return gc.open_by_key(st.secrets["spreadsheet_id"])

# --- UPDATED GET_SHEET WITH AUTOMATIC RETRY ---
def get_sheet(sheet_name):
    sh = connect_to_google()
    
    # Try to open the sheet up to 3 times before giving up
    for attempt in range(3):
        try:
            return sh.worksheet(sheet_name)
        except Exception as e:
            if attempt < 2:
                time.sleep(2) # Wait exactly 2 seconds and try again
            else:
                raise e # If it fails 3 times, then show the error

def generate_task_id():
    # Creates a short, random ID like #TASK-8A2F
    return f"#TASK-{str(uuid.uuid4())[:4].upper()}"

def update_task_in_sheet(task_id, new_status, feedback=""):
    tasks_sheet = get_sheet("Tasks")
    records = tasks_sheet.get_all_records()
    for i, row in enumerate(records):
        if str(row.get("Task ID")) == str(task_id):
            row_idx = i + 2 # Google Sheets is 1-indexed, and we skip the header
            tasks_sheet.update_cell(row_idx, 6, new_status) # Col 6 is Status
            tasks_sheet.update_cell(row_idx, 7, feedback)   # Col 7 is Feedback
            break

def upload_to_imgbb(uploaded_file):
    url = "https://api.imgbb.com/1/upload"
    payload = {
        "key": st.secrets["imgbb_key"],
        "image": base64.b64encode(uploaded_file.getvalue()).decode("utf-8")
    }
    res = requests.post(url, data=payload)
    return res.json()["data"]["url"]

def upload_to_drive(uploaded_file):
    creds = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    file_metadata = {
        'name': uploaded_file.name,
        'parents': [st.secrets["drive_folder_id"]]
    }
    media = MediaIoBaseUpload(io.BytesIO(uploaded_file.getvalue()), mimetype=uploaded_file.type, resumable=False)
    try:
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
    except HttpError as error:
        st.error(f"Google Error Details: {error.content}")
        st.stop()
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
    
    # Make file readable to anyone with the link
    drive_service.permissions().create(
        fileId=file.get('id'),
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()
    
    return file.get('webViewLink')

def send_immediate_email(to_email, subject, body):
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = st.secrets["email"]["sender"]
    msg['To'] = to_email

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(st.secrets["email"]["sender"], st.secrets["email"]["password"])
        smtp.send_message(msg)

def get_active_topics(sheet_name):
    try:
        df = pd.DataFrame(get_sheet(sheet_name).get_all_records())
        if df.empty: return []
        # Filter out "done" or "posted" (case insensitive)
        active = df[~df['Status'].str.lower().isin(['done', 'posted'])]
        return active['Topic'].tolist()
    except:
        return []

def send_email(to_email, subject, body):
    try:
        # Pulls from the [email] section in your TOML file
        sender_email = st.secrets["email"]["sender"]
        sender_password = st.secrets["email"]["password"]
        
        # Package the email
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        # Connect to Gmail and send it
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        raise e
        
def display_head_active_tasks():
    st.subheader("🎯 My Active Tasks")
    try:
        tasks_df = pd.DataFrame(get_sheet("Tasks").get_all_records())
    except Exception:
        tasks_df = pd.DataFrame()

    if not tasks_df.empty and 'Assigned To' in tasks_df.columns:
        # Show tasks that are Assigned or Rejected
        my_tasks = tasks_df[(tasks_df['Assigned To'] == st.session_state.name) & (tasks_df['Status'].isin(['Assigned', 'Rejected']))]
        
        if my_tasks.empty:
            st.success("✅ You have no active tasks pending. Great job!")
        else:
            for _, task in my_tasks.iterrows():
                # Calculate Days Pending safely
                try:
                    task_date = datetime.strptime(str(task['Timestamp']), "%Y-%m-%d %H:%M:%S").date()
                    # Get today's date strictly in IST
                    today_date = datetime.now(pytz.timezone('Asia/Kolkata')).date()
                    days_pending = (today_date - task_date).days
                except:
                    days_pending = 0
                
                with st.expander(f"Task: {str(task['Task Details'])[:30]}... | Pending: {days_pending} days", expanded=True):
                    st.write(f"**Assigned by:** {task['Assigned By']} | **Task ID:** {task['Task ID']}")
                    st.info(f"{task['Task Details']}")
                    
                    if task['Status'] == 'Rejected':
                        st.error(f"**Feedback from Core:** {task['Feedback']}")
                        
                    if st.button(f"Mark Completed", key=f"complete_{task['Task ID']}"):
                        update_task_in_sheet(task['Task ID'], "Pending Verification")
                        st.success("Sent to Senior Core for verification!")
                        st.rerun()
    st.markdown("---")

def get_dev_active_topics(sheet_name):
    try:
        df = pd.DataFrame(get_sheet(sheet_name).get_all_records())
        if df.empty: return []
        # Filter out "done" or "posted" (case insensitive)
        active = df[~df['Status'].str.lower().isin(['Drafting','Review','Done', 'Took the session'])]
        return active['Topic'].tolist()
    except:
        return []

def update_dev_topic_status(topic, status, taken_by=""):
    sheet = get_sheet("Dev_Sessions")
    records = sheet.get_all_records()
    today_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d")
    
    for idx, row in enumerate(records):
        if str(row.get('Topic', '')) == str(topic):
            sheet.update_cell(idx + 2, 2, status)    # Column B: Status
            sheet.update_cell(idx + 2, 3, today_str) # Column C: Last Updated
            
            if taken_by: # Only overwrite Column D if a name was provided
                sheet.update_cell(idx + 2, 4, taken_by)
            return

def update_topic_status(sheet_name, topic, status):
    sheet = get_sheet(sheet_name)
    records = sheet.get_all_records()
    today_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d")
    
    for idx, row in enumerate(records):
        if str(row.get('Topic', '')) == str(topic):
            sheet.update_cell(idx + 2, 2, status)
            sheet.update_cell(idx + 2, 3, today_str)  # Updates Column C
            return
            
    # If the topic doesn't exist yet, append it with today's date
    sheet.append_row([topic, status, today_str])

def display_project_table(sheet_name):
    try:
        df = pd.DataFrame(get_sheet(sheet_name).get_all_records())
        if not df.empty and 'Last Updated' in df.columns:
            today = pd.to_datetime(datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d"))
            # Convert to datetime safely, calculate difference
            df['Last Updated'] = pd.to_datetime(df['Last Updated'], errors='coerce')
            df['Days in Status'] = (today - df['Last Updated']).dt.days
            # Clean up the format for display
            df['Days in Status'] = df['Days in Status'].fillna(0).astype(int)
            df['Last Updated'] = df['Last Updated'].dt.strftime('%Y-%m-%d')
        st.dataframe(df, use_container_width=True, hide_index=True)
    except Exception as e:
        st.caption("No data or missing 'Last Updated' column yet.")

def display_performance_leaderboard(subs_df):
    try:
        # Prevent crashes if the sheet is completely blank or missing columns
        if subs_df is None or subs_df.empty or 'Name' not in subs_df.columns or 'Status' not in subs_df.columns:
            st.info("Leaderboard will appear once data is logged.")
            return
        
        scores = []
        # Loop through every unique Head in the database
        for name in subs_df['Name'].unique():
            user_data = subs_df[subs_df['Name'] == name]
            
            # Count their specific statuses safely
            verified = len(user_data[user_data['Status'] == 'Verified'])
            delayed = len(user_data[user_data['Status'] == 'Delayed'])
            missed = len(user_data[user_data['Status'] == 'Missed Deadline'])
            
            # Calculate the gamified math
            total_score = (verified * 5) + (delayed * -3) + (missed * -1)
            
            scores.append({
                "Head Name": name,
                "Total Score": total_score,
                "Verified (+5)": verified,
                "Delayed (-3)": delayed,
                "Missed (-1)": missed
            })
        
        # Sort the dataframe from highest score to lowest
        leaderboard_df = pd.DataFrame(scores).sort_values(by="Total Score", ascending=False).reset_index(drop=True)
        
        # Add medal emojis for the top 3 ranks
        leaderboard_df.index = leaderboard_df.index + 1
        def get_medal(rank):
            if rank == 1: return '🥇 1'
            if rank == 2: return '🥈 2'
            if rank == 3: return '🥉 3'
            return str(rank)
        
        leaderboard_df.insert(0, 'Rank', leaderboard_df.index.map(get_medal))
        
        # Display the final beautiful table
        st.dataframe(leaderboard_df, use_container_width=True, hide_index=True)
        
    except Exception as e:
        st.caption("Leaderboard calculating... Submit an update to generate scores.")
# ==========================================
# LOGIN SYSTEM
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("Kaizen Chronicles Portal")
    email_input = st.text_input("Email")
    pass_input = st.text_input("Password", type="password")
    
    if st.button("Login"):
        df_users = pd.DataFrame(get_sheet("Users").get_all_records())
        user_match = df_users[(df_users['Email'] == email_input) & (df_users['Password'] == str(pass_input))]
        
        if not user_match.empty:
            st.session_state.logged_in = True
            st.session_state.name = user_match.iloc[0]['Name']
            st.session_state.role = user_match.iloc[0]['Role']
            st.session_state.email = user_match.iloc[0]['Email']
            st.rerun()
        else:
            st.error("Invalid credentials.")

else:
    # ==========================================
    # NAVIGATION & DASHBOARD
    # ==========================================
    st.sidebar.title(f"Welcome, {st.session_state.name}")
    st.sidebar.write(f"**Role:** {st.session_state.role}")
    if st.sidebar.button("Logout"):
        st.session_state.clear()
        st.rerun()

    # ------------------------------------------
    # 1. HEADS VIEW (SUBMIT UPDATES)
    # ------------------------------------------
    if "Head" in st.session_state.role:
        st.header("Daily Update Submission")
        uploaded_links = []
        
        # --- NEW: FETCH DATA SAFELY ---
        try:
            # Connect to the sheet just for the leaderboard
            subs_df = pd.DataFrame(get_sheet("Submissions").get_all_records())
        except Exception:
            # If the connection fails, create a blank dataframe to prevent crashes
            subs_df = pd.DataFrame()
        
        # --- SHOW LEADERBOARD TO HEADS ---
        st.subheader("🏆 Current Rankings")
        display_performance_leaderboard(subs_df)
        st.markdown("---")
        # --- ATTENDANCE TRACKER (Moved OUTSIDE the form for instant updates) ---
        display_head_active_tasks()
        
        st.subheader("Daily Attendance")
        attendance = st.radio("Today's Status", ["Working normally", "Ill", "Vacation", "Others"], horizontal=True)
        
        absence_reason = ""
        expected_days = 0
        
        if attendance == "Others":
            absence_reason = st.text_input("Please specify your reason:")
            
        if attendance != "Working normally":
            expected_days = st.number_input("Expected number of days away:", min_value=1, step=1, value=1)
            
        with st.form(key="daily_update_form"):
            responses = {}
            
            # Map the attendance responses inside the form so it saves to the database
            if attendance == "Others":
                responses["Attendance Status"] = f"Others ({absence_reason}) | Expected return in: {expected_days} day(s)"
            elif attendance != "Working normally":
                responses["Attendance Status"] = f"{attendance} | Expected return in: {expected_days} day(s)"
            else:
                responses["Attendance Status"] = "Working normally"
                
            if attendance != "Working normally":
                st.info("You are marked as away. You can submit this update as-is to log your absence, or add voluntary notes below.")
                
            st.markdown("---")
        

            
                
            # Note: You can copy/paste this exact Research block for "Case Studies" or "Insight Series" as well!
            # ... (Your department-specific questions remain here) ...
            # ... (All of your other department-specific questions and the submit button go here!) ...
            # ... (KEEP ALL YOUR EXISTING HEAD-SPECIFIC LOGIC HERE: Mails Sent, Current Topic, etc.) ...
            # --- HEAD OF PROJECTS ---
            if st.session_state.role == "Head of Projects":
                st.subheader("Section 1: Projects")
                col1, col2, col3 = st.columns(3)
                responses["Mails Sent"] = col1.number_input("Mails Sent", min_value=0)
                responses["Calls Done"] = col2.number_input("Calls Done", min_value=0)
                responses["LinkedIn Msgs"] = col3.number_input("LinkedIn Messages", min_value=0)
                
                col4, col5 = st.columns(2)
                responses["Meetings Done"] = col4.number_input("Meetings Done", min_value=0)
                responses["Projects Converted"] = col5.number_input("Projects Converted", min_value=0)
                
                proof_files = st.file_uploader("Upload Proofs (Screenshots)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'])
            
            st.subheader("Section 2: Development Sessions")
            
            dev_topics = get_dev_active_topics("Dev_Sessions")
            
            # ADDED KEY: Locks this widget in memory so it never resets
            if dev_topics:
                dev_topic = st.selectbox("Current Topic", dev_topics, key="dev_topic_select")
            else:
                dev_topic = st.text_input("Current Topic (Type manually if empty)", key="dev_topic_text")
            
            # ADDED KEY: This is the magic fix that prevents the dropdown from resetting!
            dev_status = st.selectbox("Session Status", 
                                      ["Drafting", "Review", "Done", "Took the session"], 
                                      key="dev_status_select")
            
            dev_taken_list = []
            dev_taken_manual = ""
            
            # .strip().lower() makes it 100% immune to invisible spaces!
            if dev_status.strip().lower() == "took the session":
                
                if "eligible_leaders" not in st.session_state:
                    try:
                        u_df = pd.DataFrame(get_sheet("Users").get_all_records())
                        u_df.columns = u_df.columns.str.strip()
                        pattern = "head|advisory board|director"
                        st.session_state.eligible_leaders = u_df[u_df['Role'].str.contains(pattern, case=False, na=False)]['Name'].tolist()
                    except Exception:
                        st.session_state.eligible_leaders = []
                
                st.write("---")
                if len(st.session_state.eligible_leaders) > 0:
                    # ADDED KEY here too!
                    dev_taken_list = st.multiselect("Who took the session? (Select all that apply):", 
                                                    st.session_state.eligible_leaders,
                                                    key="dev_multi_select")
                else:
                    st.warning("Loading names from Google Sheets... please refresh.")
                    
                dev_taken_manual = st.text_input("Other senior members (Type names manually, if any):", 
                                                 key="dev_manual_text")
                st.write("---")
            # --- HEAD OF RESEARCH ---
            elif st.session_state.role == "Head of Research":
                st.subheader("Section 1: Industry Primer")
                ind_topics = get_active_topics("Industries")
                responses["Industry Topic"] = st.selectbox("Current Industry", ind_topics) if ind_topics else st.text_input("Current Industry (Type manually if list is empty)")
                responses["Industry Status"] = st.selectbox("Industry Status", ["Drafting", "review", "done"])
                
                st.subheader("Section 2: Case Study / Analysis")
                case_topics = get_active_topics("Case_Studies")
                responses["Case Study Topic"] = st.selectbox("Current Topic", case_topics) if case_topics else st.text_input("Current Topic (Type manually if list is empty)")
                responses["Case Study Status"] = st.selectbox("Case Study Status", ["Drafting", "review", "done", "posted"])

            # --- HEAD OF DIGITAL ---
            # --- HEAD OF DIGITAL ---
            elif st.session_state.role == "Head of Digital":
                st.subheader("Section 1: Podcast")
                col1, col2 = st.columns(2)
                responses["Podcast Mails"] = col1.number_input("Reachout Mails", min_value=0)
                responses["Podcast Calls"] = col2.number_input("Reachout Calls", min_value=0)
                
                st.subheader("Section 2: Insight Series")
                insight_topics = get_active_topics("Insight_Series")
                responses["Insight Topic"] = st.selectbox("Current Topic", insight_topics) if insight_topics else st.text_input("Current Topic (Type manually if list is empty)")
                responses["Insight Status"] = st.selectbox("Status", ["Drafting", "review", "done", "posted"])

            # --- VOLUNTARY CONTRIBUTION ---
            st.markdown("---")
            responses["Voluntary Contribution"] = st.text_area("Voluntary contribution outside your vertical (Optional)")

            # --- OTHERS ---
            st.markdown("---")
            responses["Notes"] = st.text_area("Notes, if any")

            # --- SUBMISSION LOGIC ---
            submitted = st.form_submit_button("Submit EOD Update")
            if submitted:
                with st.spinner("Processing submission..."):
                    
                    # --- HEAD OF PROJECTS LOGIC ---
                    if "Head of Projects" in st.session_state.role:
                        if dev_status and dev_topic:
                            responses["Development session Topic"] = dev_topic
                            responses["Session Status"] = dev_status
                        # 1. ALWAYS update the status in the tracker sheet (even without files)
                       
                            final_taken_by = ""
                            if dev_status == "Took the session":
                                combined_names = dev_taken_list.copy()
                                if dev_taken_manual.strip():
                                    combined_names.append(dev_taken_manual.strip())
                                final_taken_by = ", ".join(combined_names)

                                if final_taken_by:
                                    responses["Taken By"] = final_taken_by
                            
                            # Use our new dedicated Dev update function!
                            update_dev_topic_status(responses["Development session Topic"], responses["Session Status"], final_taken_by)
                        
                        # 2. ONLY upload files if they attached them
                        if proof_files:
                            for f in proof_files:
                                link = upload_to_imgbb(f)
                                uploaded_links.append(f"Proof: {link}")
                    
                    # --- COMPILE & SEND TO SUBMISSIONS SHEET ---
                    formatted_data = "\n".join([f"**{k}**: {v}" for k, v in responses.items() if v])
                    
                    if uploaded_links:
                        formatted_data += "\n\n**Proofs:**\n" + "\n".join(uploaded_links)
                        
                    ist_now = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M:%S")
                    sheet = get_sheet("Submissions")
                    sheet.append_row([ist_now, st.session_state.name, st.session_state.role, formatted_data, "Pending", "", ""])
                    
                    st.success("Update submitted successfully! The Advisory Board will review it.")
                    
                    if st.session_state.role == "Head of Research":
                        if responses.get("Industry Status"):
                            update_topic_status("Industries", responses["Industry Topic"], responses["Industry Status"])
                        if responses.get("Case Study Status"):
                            update_topic_status("Case_Studies", responses["Case Study Topic"], responses["Case Study Status"])
                    elif st.session_state.role == "Head of Digital":
                        if responses.get("Insight Status"):
                            update_topic_status("Insight_Series", responses["Insight Topic"], responses["Insight Status"])
                            
                    st.success("Update submitted successfully!")
                    
        # --- HEADS FEEDBACK DASHBOARD ---
        st.markdown("---")
        st.header("My Submissions & Feedback")
        subs_df = pd.DataFrame(get_sheet("Submissions").get_all_records())
        if not subs_df.empty:
            my_subs = subs_df[subs_df['Name'] == st.session_state.name].sort_values(by="Timestamp", ascending=False)
            for idx, row in my_subs.iterrows():
                with st.expander(f"{row['Timestamp']} - {row['Status']}"):
                    st.markdown(row['Submission_Data'])
                    if row['Status'] == 'Verified':
                        verifier = row.get('Verified_By', '')
                        if verifier:
                            st.success(f"✅ Verified by: {verifier}")
                        else:
                            st.success("✅ Verified by: Senior Core")
                    
                    elif row['Status'] == 'Rejected':
                        st.error(f"**Contribution Rejected by Senior Core:**\n{row.get('Feedback_Reason', 'No reason provided.')}")
                        
                    elif row['Status'] == 'Delayed':
                        st.warning(f"**Feedback/Reason from Senior Core:**\n{row.get('Feedback_Reason', 'No reason provided.')}")
                        head_reason = row.get('Head_Reason', '')
                        if not head_reason:
                            with st.form(key=f"head_reason_{idx}"):
                                reason_input = st.text_area("Enter your explanation for this delayed update:")
                                if st.form_submit_button("Submit Explanation"):
                                    if reason_input:
                                        sheet = get_sheet("Submissions")
                                        sheet.update_cell(idx + 2, 7, reason_input)
                                        st.success("Explanation sent to Senior Core!")
                                        st.rerun()
                                    else:
                                        st.error("Please type a reason before submitting.")
                        else:
                            st.info(f"**Your Submitted Explanation:**\n{head_reason}")
    # ------------------------------------------
    # 2. ADVISORY BOARD & ED VIEW (VERIFICATION)
    # ------------------------------------------
    # ------------------------------------------
    # 2. ADVISORY BOARD & ED VIEW (VERIFICATION)
    # ------------------------------------------
    if st.session_state.role in ["Advisory Board", "Executive Director"]:
        st.header("Senior Core Portal")
        
        sheet = get_sheet("Submissions")
        subs_df = pd.DataFrame(sheet.get_all_records())
        
        tab_dash, tab_pend, tab_res, tab_hist, tab_tasks = st.tabs(["📊 Analytics Dashboard", "✅ Pending Queue", "⏳ Resolution Queue", "🗂️ Audit History", "Tasks Assigned"])
        
        # --- TAB 1: DASHBOARD ---
        # --- TAB 1: DASHBOARD ---
        with tab_dash:
            st.subheader("🏆 Department Leaderboard")
            display_performance_leaderboard(subs_df)
            
            st.markdown("---")
            st.subheader("Team Output Volume")
            if not subs_df.empty:
                head_counts = subs_df['Name'].value_counts()
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Submissions", len(subs_df))
                col2.metric("Verified", len(subs_df[subs_df['Status'] == 'Verified']))
                col3.metric("Delayed", len(subs_df[subs_df['Status'] == 'Delayed']))
                col4.metric("Rejected", len(subs_df[subs_df['Status'] == 'Rejected']))
                
            # --- NEW: LIVE ABSENCE TRACKER ---
            st.markdown("---")
            st.subheader("Team Availability (Out of Office)")
            if not subs_df.empty:
                absence_data = []
                # Check consecutive absence days for each user
                for head in subs_df['Name'].unique():
                    head_subs = subs_df[subs_df['Name'] == head].sort_values(by='Timestamp', ascending=False)
                    consec_days = 0
                    current_stat = "Working normally"
                    
                    for _, row in head_subs.iterrows():
                        match = re.search(r'\*\*Attendance Status\*\*: (.*)', str(row['Submission_Data']))
                        status = match.group(1) if match else "Working normally"
                        
                        if consec_days == 0:
                            current_stat = status
                            if status == "Working normally":
                                break  # They are active, stop counting
                        
                        if status == current_stat:
                            consec_days += 1
                        else:
                            break
                            
                    if current_stat != "Working normally":
                        absence_data.append({"Name": head, "Status": current_stat, "Days Absent": consec_days})
                        
                if absence_data:
                    st.table(pd.DataFrame(absence_data))
                else:
                    st.success("All team members are currently active and working!")
            
            # --- SMART PROJECT DURATIONS ---
            st.markdown("---")
            st.subheader("Department Active Projects & Status Durations")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.write("**Research (Industries)**")
                display_project_table("Industries")
            with c2:
                st.write("**Research (Case Studies)**")
                display_project_table("Case_Studies")
            with c3:
                st.write("**Digital (Insight Series)**")
                display_project_table("Insight_Series")
            
            st.markdown("---")
            st.subheader("📘 Development Sessions Tracker")
            
            try:
                dev_df = pd.DataFrame(get_sheet("Dev_Sessions").get_all_records())
            except Exception:
                dev_df = pd.DataFrame()
                st.warning("Could not load Development Sessions. Please wait 60 seconds.")
                
            if not dev_df.empty:
                for _, row in dev_df.iterrows():
                    d_topic = row.get("Topic", "Unknown")
                    d_status = row.get("Status", "Unknown")
                    d_updated = row.get("Last Updated", "")
                    d_taken_by = row.get("Taken by", "")
                    
                    days_passed = 0
                    if d_updated:
                        try:
                            # Safely read the date format "%Y-%m-%d"
                            updated_date = datetime.strptime(str(d_updated), "%Y-%m-%d").date()
                            today_date = datetime.now(pytz.timezone('Asia/Kolkata')).date()
                            days_passed = (today_date - updated_date).days
                        except:
                            days_passed = 0

                        # Display the days passed in the expander title!
                    with st.expander(f"{d_topic} | {d_status} (for {days_passed} days)"):
                        st.write(f"**Current Status:** {d_status} (for {days_passed} days)")
                        if d_taken_by:
                            st.write(f"**Taken By:** {d_taken_by}")

                        
                        # Only show Verify button for these two specific statuses!
                        if d_status in ["Review", "Took the session"]:
                            if st.button(f"✅ Verify / Mark as Done", key=f"verify_dev_{d_topic}"):
                                update_dev_topic_status(d_topic, "Done")
                                st.success(f"'{d_topic}' successfully verified and closed!")
                                st.rerun()
            else:
                st.info("No active development sessions found.")
            
            # --- NEW: RETENTION & BURNOUT FLAGS ---
            st.subheader("🚩 Retention & Burnout Alerts")
            if not subs_df.empty:
                import re
                
                # Standardize timestamps for accurate 30-day math
                subs_df['Date_Obj'] = pd.to_datetime(subs_df['Timestamp'], errors='coerce')
                today_dt = pd.to_datetime(datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d"))
                thirty_days_ago = today_dt - timedelta(days=30)
                
                flags_triggered = False
                
                for head in subs_df['Name'].unique():
                    head_df = subs_df[subs_df['Name'] == head].sort_values(by="Date_Obj", ascending=False)
                    if head_df.empty:
                        continue
                    
                    # FLAG 1: Extended Leave Warning (>= 4 Days declared on latest entry)
                    latest_sub = head_df.iloc[0]
                    status_text = str(latest_sub.get('Submission_Data', ''))
                    leave_match = re.search(r'Expected return in: (\d+)', status_text)
                    
                    if leave_match:
                        leave_days = int(leave_match.group(1))
                        if leave_days >= 4:
                            st.error(f"**Action Required for {head}:** Declared an extended absence of {leave_days} days. *Recommendation: Review active projects and reallocate deliverables to prevent bottlenecks.*")
                            flags_triggered = True
                            
                    # FLAG 2: Rolling 30-Day Performance Drop (4+ Delays/Rejects)
                    head_30d = head_df[head_df['Date_Obj'] >= thirty_days_ago]
                    struggle_count = len(head_30d[head_30d['Status'].isin(['Delayed', 'Rejected'])])
                    
                    if struggle_count >= 4:
                        st.error(f"**Check-in Recommended for {head}:** Accumulated **{struggle_count} Delays/Rejections** in the last 30 days. *Recommendation: Schedule a 1-on-1 to discuss operational blockers and realign expectations.*")
                        flags_triggered = True
                
                if not flags_triggered:
                    st.success("✅ Team stability is strong. No retention or burnout risks detected across the Heads.")
            
            st.markdown("---")
                
          

            # --- PROJECTS METRICS DASHBOARD ---
            st.markdown("---")
            st.subheader("Head of Projects - Outreach & Conversion Metrics")
            projects_df = subs_df[subs_df['Role'] == 'Head of Projects']
            
            if not projects_df.empty:
                project_stats = []
                for _, p_row in projects_df.iterrows():
                    text = str(p_row['Submission_Data'])
                    
                    # Helper function to extract numbers from the text block
                    def extract_val(label, txt):
                        match = re.search(rf'\*\*{label}\*\*: (\d+)', txt)
                        return int(match.group(1)) if match else 0
                        
                    project_stats.append({
                        'Name': p_row['Name'],
                        'Mails Sent': extract_val('Mails Sent', text),
                        'Calls Done': extract_val('Calls Done', text),
                        'LinkedIn Msgs': extract_val('LinkedIn Msgs', text),
                        'Meetings Done': extract_val('Meetings Done', text),
                        'Projects Converted': extract_val('Projects Converted', text)
                    })
                    
                stats_df = pd.DataFrame(project_stats)
                
                # Group by Name to get total sums across all their submissions
                agg_stats = stats_df.groupby('Name').sum()
                
                if not agg_stats.empty:
                    # Display the raw numbers in a clean table
                    st.dataframe(agg_stats, use_container_width=True)
                    # Display a stacked bar chart comparing the Heads
                    st.bar_chart(agg_stats)
                else:
                    st.caption("No quantifiable project metrics found yet.")
            else:
                st.caption("No project metrics available yet.")

        # --- TAB 2: PENDING QUEUE ---
        with tab_pend:
            st.subheader("Verify New Submissions")
            if not subs_df.empty:
                pending = subs_df[subs_df['Status'] == 'Pending']
                if pending.empty:
                    st.info("No pending submissions to verify.")
                else:
                    for idx, row in pending.iterrows():
                        with st.expander(f"{row['Name']} - {row['Role']} ({row['Timestamp']})"):
                            submission_text = str(row['Submission_Data'])
                            urls = re.findall(r'(https?://[^\s]+)', submission_text)
                            clean_text = re.sub(r'Proof: https?://[^\s]+', '', submission_text)
                            st.markdown(clean_text)
                            if urls:
                                st.markdown("**Attached Proofs:**")
                                for url in urls: st.image(url, width=400)
                            
                            with st.form(key=f"form_{idx}"):
                                delay_reason = st.text_input("Feedback / Reason (Required if Delaying or Rejecting):")
                                col1, col2, col3 = st.columns(3)
                                verify_btn = col1.form_submit_button("Verify Contribution")
                                delay_btn = col2.form_submit_button("Delay Contribution")
                                reject_btn = col3.form_submit_button("Reject Contribution")
                                
                                if verify_btn:
                                    sheet.update_cell(idx + 2, 5, "Verified")
                                    sheet.update_cell(idx + 2, 8, st.session_state.name)
                                    st.success(f"Verified {row['Name']}'s update!")
                                    st.rerun()
                                    
                                if delay_btn or reject_btn:
                                    if not delay_reason:
                                        st.error("Please provide a reason to delay or reject.")
                                    else:
                                        new_status = "Rejected" if reject_btn else "Delayed"
                                        sheet.update_cell(idx + 2, 5, new_status)
                                        sheet.update_cell(idx + 2, 6, delay_reason)
                                        sheet.update_cell(idx + 2, 8, st.session_state.name)
                                        
                                        users_df = pd.DataFrame(get_sheet("Users").get_all_records())
                                        head_email = users_df[users_df['Name'] == row['Name']].iloc[0]['Email']
                                        
                                        body = f"Hi {row['Name']},\n\nYour Kaizen update submitted on {row['Timestamp']} has been {new_status} by the Senior Core.\n\nReason:\n{delay_reason}\n\nPlease log into the portal to review."
                                        send_immediate_email(head_email, f"Kaizen Portal: Contribution {new_status}", body)
                                        
                                        st.warning(f"{new_status} {row['Name']}'s update. Email sent.")
                                        st.rerun()

        # --- TAB 3: RESOLUTION QUEUE ---
        with tab_res:
            st.subheader("Review Delayed Explanations")
            if not subs_df.empty:
                delayed = subs_df[subs_df['Status'] == 'Delayed']
                if delayed.empty:
                    st.info("No delayed submissions waiting for review.")
                else:
                    for idx, row in delayed.iterrows():
                        with st.expander(f"{row['Name']} - {row['Role']} ({row['Timestamp']})"):
                            submission_text = str(row['Submission_Data'])
                            urls = re.findall(r'(https?://[^\s]+)', submission_text)
                            clean_text = re.sub(r'Proof: https?://[^\s]+', '', submission_text)
                            st.markdown(clean_text)
                            if urls:
                                for url in urls: st.image(url, width=400)
                                    
                            st.warning(f"**Senior Core Feedback:** {row.get('Feedback_Reason', '')}")
                            
                            head_reason = row.get('Head_Reason', '')
                            if head_reason:
                                st.info(f"**Head's Explanation:**\n{head_reason}")
                                with st.form(key=f"resolve_{idx}"):
                                    if st.form_submit_button("Accept Reason & Verify"):
                                        sheet.update_cell(idx + 2, 5, "Verified")
                                        sheet.update_cell(idx + 2, 8, st.session_state.name)
                                        st.success("Resolved and Verified!")
                                        st.rerun()
                                    if st.form_submit_button("Reject Explanation"):
                                        sheet.update_cell(idx + 2, 5, "Rejected")
                                        sheet.update_cell(idx + 2, 8, st.session_state.name)
                                        st.error("Explanation Rejected.")
                                        st.rerun()
                            else:
                                st.error("Waiting for the Head to provide their explanation.")
        
        # --- TAB 4: AUDIT HISTORY ---
        with tab_hist:
            st.subheader("Senior Core Action Logs")
            if not subs_df.empty:
                # Filter to only show items that have been acted upon
                history_df = subs_df[subs_df['Status'].isin(['Verified', 'Delayed', 'Rejected'])].copy()
                
                if not history_df.empty:
                    # Extract just the date (YYYY-MM-DD) from Timestamp for the filter
                    history_df['Date_Only'] = history_df['Timestamp'].apply(lambda x: str(x).split(' ')[0])
                    
                    # Create the layout for the filters
                    col1, col2 = st.columns(2)
                    available_dates = sorted(history_df['Date_Only'].unique(), reverse=True)
                    
                    # Date and Status Filters
                    selected_date = col1.selectbox("Filter by Date:", ["All Days"] + list(available_dates))
                    selected_statuses = col2.multiselect("Filter by Status:", ["Verified", "Delayed", "Rejected"], default=["Verified", "Delayed", "Rejected"])
                    
                    # Apply the chosen filters to the data
                    if selected_date != "All Days":
                        history_df = history_df[history_df['Date_Only'] == selected_date]
                    history_df = history_df[history_df['Status'].isin(selected_statuses)]
                    
                    if history_df.empty:
                        st.info("No records found for these filters.")
                    else:
                        st.markdown(f"**Showing {len(history_df)} record(s)**")
                        for idx, row in history_df.iterrows():
                            actor = row.get('Verified_By', 'Unknown Core Member')
                            status = row['Status']
                            
                            # Define the UI badge based on the status
                            if status == 'Verified':
                                badge = f"✅ **{status}** by **{actor}**"
                            elif status == 'Delayed':
                                badge = f"⏳ **{status}** by **{actor}**"
                            else:
                                badge = f"🚫 **{status}** by **{actor}**"
                                
                            with st.expander(f"{row['Name']} - {row['Role']} ({row['Timestamp']})"):
                                st.markdown(badge)
                                if status in ['Delayed', 'Rejected']:
                                    st.markdown(f"**Reason given:** {row.get('Feedback_Reason', 'No reason provided.')}")
                                    
                                st.markdown("---")
                                # Show the original submission data cleanly
                                submission_text = str(row['Submission_Data'])
                                urls = re.findall(r'(https?://[^\s]+)', submission_text)
                                clean_text = re.sub(r'Proof: https?://[^\s]+', '', submission_text)
                                st.markdown(clean_text)
                                
                                if urls:
                                    st.markdown("**Attached Proofs:**")
                                    for url in urls: st.image(url, width=400)
                else:
                    st.info("No actions have been logged yet.")

        # --- TAB: TASK DELEGATION ---
        with tab_tasks:
            st.header("🎯 Task Delegation Hub")
            
            try:
                tasks_df = pd.DataFrame(get_sheet("Tasks").get_all_records())
            except Exception:
                tasks_df = pd.DataFrame()

            # --- PART 1: ASSIGN NEW TASKS ---
            with st.expander("➕ Assign a New Task", expanded=True):
                # 1. Safe default variables so it NEVER crashes
                selected_heads = []
                task_details = ""
                all_heads_names = []
                
                # 2. Safely fetch Users and clean the data
                try:
                    users_df_tasks = pd.DataFrame(get_sheet("Users").get_all_records())
                    # Clean column names in case there are invisible spaces in Google Sheets
                    users_df_tasks.columns = users_df_tasks.columns.str.strip()
                    # Find roles containing "head" (case-insensitive)
                    heads_df = users_df_tasks[users_df_tasks['Role'].str.contains("head", case=False, na=False)]
                    all_heads_names = heads_df['Name'].tolist()
                except Exception:
                    st.warning("⚠️ Google Sheets rate limit reached. Waiting for connection...")
                
                # 3. Only show the form if we successfully got the names
                if len(all_heads_names) > 0:
                    selected_heads = st.multiselect("Assign to:", all_heads_names)
                    task_details = st.text_area("Task Details & Instructions:")
                    
                    if st.button("Assign Task"):
                        if selected_heads and task_details:
                            tasks_sheet = get_sheet("Tasks")
                            ist_now = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M:%S")
                            
                            for head in selected_heads:
                                task_id = generate_task_id()
                                tasks_sheet.append_row([task_id, ist_now, st.session_state.name, head, task_details, "Assigned", ""])
                                
                                # Send Instant Email Safely
                                try:
                                    # Strip spaces from the Name column to guarantee a perfect match
                                    users_df_tasks['Name'] = users_df_tasks['Name'].astype(str).str.strip()
                                    head_clean = str(head).strip()
                                    
                                    head_email = users_df_tasks[users_df_tasks['Name'] == head_clean]['Email'].values[0]
                                    
                                    subject = f"ACTION REQUIRED: New Task Assigned ({task_id})"
                                    body = f"Hi {head_clean},\n\nYou have been assigned a new task by {st.session_state.name}:\n\n'{task_details}'\n\nPlease log into the Kaizen Portal to view and complete it."
                                    
                                    send_email(head_email, subject, body)
                                except Exception as e:
                                    st.error(f"Task saved, but email alert failed to send: {e}")
                                    st.stop()
                                
                            st.success("Task(s) assigned successfully!")
                            st.rerun()
                        else:
                            st.error("Please select at least one Head and enter task details.")
                else:
                    st.info("Loading Heads... If this takes too long, wait exactly 60 seconds and refresh the page.")            
            # --- PART 2: PENDING VERIFICATIONS ---
            st.subheader("👀 Verify Completed Tasks")
            if not tasks_df.empty and 'Status' in tasks_df.columns:
                pending_tasks = tasks_df[tasks_df['Status'] == 'Pending Verification']
                
                if pending_tasks.empty:
                    st.info("No tasks are currently waiting for your verification.")
                else:
                    for _, task in pending_tasks.iterrows():
                        with st.container():
                            st.markdown(f"**{task['Task ID']}** | Assigned to: **{task['Assigned To']}**")
                            st.info(task['Task Details'])
                            
                            col1, col2 = st.columns(2)
                            # Verify Button
                            if col1.button(f"✅ Approve (Done)", key=f"approve_{task['Task ID']}"):
                                update_task_in_sheet(task['Task ID'], "Verified")
                                st.success("Task verified and closed!")
                                st.rerun()
                                
                            # Reject Button logic (Shows a text box if clicked)
                            reject_toggle = col2.checkbox("❌ Reject / Needs Work", key=f"toggle_{task['Task ID']}")
                            if reject_toggle:
                                feedback_note = st.text_input("Feedback Note:", key=f"note_{task['Task ID']}")
                                if st.button("Send Back to Head", key=f"sendback_{task['Task ID']}"):
                                    if feedback_note:
                                        update_task_in_sheet(task['Task ID'], "Rejected", feedback_note)
                                        try:
                                            # Fetch users to get the email address safely
                                            users_df_verify = pd.DataFrame(get_sheet("Users").get_all_records())
                                            users_df_verify.columns = users_df_verify.columns.str.strip()
                                            users_df_verify['Name'] = users_df_verify['Name'].astype(str).str.strip()
                                            
                                            assignee_clean = str(task['Assigned To']).strip()
                                            head_email = users_df_verify[users_df_verify['Name'] == assignee_clean]['Email'].values[0]
                                            
                                            subject = f"TASK REJECTED: Revisions needed for {task['Task ID']}"
                                            body = f"Hi {assignee_clean},\n\nYour task has been reviewed by {st.session_state.name} and requires revisions.\n\nFeedback: '{feedback_note}'\n\nPlease log into the Kaizen Portal to update it."
                                            send_email(head_email, subject, body)
                                            
                                        except Exception as e:
                                            st.warning(f"Status updated, but email alert failed: {e}")
                                        st.rerun()
                                    else:
                                        st.warning("Please provide a feedback note so they know what to fix.")
                            st.write("---")
