import streamlit as st
import gspread
import pandas as pd
from datetime import datetime
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

def get_sheet(sheet_name):
    creds = get_google_credentials()
    gc = gspread.authorize(creds)
    # Replace with your actual Spreadsheet ID
    sh = gc.open_by_key(st.secrets["spreadsheet_id"]) 
    return sh.worksheet(sheet_name)

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

def update_topic_status(sheet_name, topic, new_status):
    sheet = get_sheet(sheet_name)
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row['Topic'] == topic:
            sheet.update_cell(i + 2, 2, new_status) # Column 2 is Status
            break

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
        responses = {}
        uploaded_links = []
        
        with st.form("update_form"):
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
                
                st.subheader("Section 2: Development")
                responses["Dev Topic"] = st.text_input("PPT Topic")
                dev_status = st.selectbox("Status", ["drafting ppt", "ppt drafted, session pending", "took the session"])
                responses["Dev Status"] = dev_status
                
                if dev_status == "took the session":
                    responses["Session Date"] = st.date_input("Select Session Date").strftime("%Y-%m-%d")

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
            elif st.session_state.role == "Head of Digital":
                st.subheader("Section 1: Podcast")
                col1, col2 = st.columns(2)
                responses["Podcast Mails"] = col1.number_input("Reachout Mails", min_value=0)
                responses["Podcast Calls"] = col2.number_input("Reachout Calls", min_value=0)
                
                st.subheader("Section 2: Insight Series")
                insight_topics = get_active_topics("Insight_Series")
                responses["Insight Topic"] = st.selectbox("Current Topic", insight_topics) if insight_topics else st.text_input("Current Topic (Type manually if list is empty)")
                responses["Insight Status"] = st.selectbox("Status", ["Drafting", "review", "done", "posted"])

            # --- SUBMISSION LOGIC ---
            submitted = st.form_submit_button("Submit EOD Update")
            if submitted:
                with st.spinner("Processing submission and uploading files..."):
                    # Handle multiple file uploads
                    if "Head of Projects" in st.session_state.role and proof_files:
                        for f in proof_files:
                            link = upload_to_imgbb(f)
                            uploaded_links.append(f"Proof: {link}")
                    
                    # Format data
                    formatted_data = "\n".join([f"**{k}**: {v}" for k, v in responses.items() if v])
                    if uploaded_links:
                        formatted_data += "\n\n**Proofs:**\n" + "\n".join(uploaded_links)
                        
                    # Push to DB
                    ist_now = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M:%S")
                    sheet = get_sheet("Submissions")
                    sheet.append_row([ist_now, st.session_state.name, st.session_state.role, formatted_data, "Pending", ""])
                    
                    # Update dynamic sheets if marked as done/posted
                    if st.session_state.role == "Head of Research":
                        if responses["Industry Status"].lower() == "done":
                            update_topic_status("Industries", responses["Industry Topic"], "done")
                        if responses["Case Study Status"].lower() in ["done", "posted"]:
                            update_topic_status("Case_Studies", responses["Case Study Topic"], responses["Case Study Status"])
                    elif st.session_state.role == "Head of Digital":
                        if responses["Insight Status"].lower() in ["done", "posted"]:
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
                    if row['Status'] == 'Delayed':
                        st.error(f"**Feedback/Reason from Senior Core:**\n{row['Feedback_Reason']}")

    # ------------------------------------------
    # 2. ADVISORY BOARD & ED VIEW (VERIFICATION)
    # ------------------------------------------
    if st.session_state.role in ["Advisory Board", "Executive Director"]:
        st.header("Verify Team Submissions")
        sheet = get_sheet("Submissions")
        subs_df = pd.DataFrame(sheet.get_all_records())
        
        if not subs_df.empty:
            pending = subs_df[subs_df['Status'] == 'Pending']
            if pending.empty:
                st.info("No pending submissions to verify.")
            else:
                for idx, row in pending.iterrows():
                    with st.expander(f"{row['Name']} - {row['Role']} ({row['Timestamp']})"):
                        st.markdown(row['Submission_Data'])
                        
                        # Use forms for row-specific button handling
                        with st.form(key=f"form_{idx}"):
                            delay_reason = st.text_input("Reason for delay (Required if clicking Delay):")
                            col1, col2 = st.columns(2)
                            verify_btn = col1.form_submit_button("Verify Contribution")
                            delay_btn = col2.form_submit_button("Delay Contribution")
                            
                            if verify_btn:
                                sheet.update_cell(idx + 2, 5, "Verified")
                                st.success(f"Verified {row['Name']}'s update!")
                                st.rerun()
                                
                            if delay_btn:
                                if not delay_reason:
                                    st.error("Please provide a reason to delay the contribution.")
                                else:
                                    sheet.update_cell(idx + 2, 5, "Delayed")
                                    sheet.update_cell(idx + 2, 6, delay_reason)
                                    
                                    # Fetch email of the Head
                                    users_df = pd.DataFrame(get_sheet("Users").get_all_records())
                                    head_email = users_df[users_df['Name'] == row['Name']].iloc[0]['Email']
                                    
                                    # Send immediate email
                                    body = f"Hi {row['Name']},\n\nYour Kaizen update submitted on {row['Timestamp']} has been Delayed by the Senior Core.\n\nReason:\n{delay_reason}\n\nPlease check the portal and submit a new update addressing this feedback."
                                    send_immediate_email(head_email, "Kaizen Portal: Contribution Delayed", body)
                                    
                                    st.warning(f"Delayed {row['Name']}'s update. Email sent.")
                                    st.rerun()
