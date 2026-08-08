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
import re

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

            # --- SUBMISSION LOGIC ---
            submitted = st.form_submit_button("Submit EOD Update")
            if submitted:
                with st.spinner("Processing submission..."):
                    if "Head of Projects" in st.session_state.role and proof_files:
                        for f in proof_files:
                            link = upload_to_imgbb(f)
                            uploaded_links.append(f"Proof: {link}")
                    
                    formatted_data = "\n".join([f"**{k}**: {v}" for k, v in responses.items() if v])
                    if uploaded_links:
                        formatted_data += "\n\n**Proofs:**\n" + "\n".join(uploaded_links)
                        
                    ist_now = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M:%S")
                    sheet = get_sheet("Submissions")
                    sheet.append_row([ist_now, st.session_state.name, st.session_state.role, formatted_data, "Pending", "", ""])
                    
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
                    
                    if row['Status'] == 'Rejected':
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
        
        tab_dash, tab_pend, tab_res = st.tabs(["📊 Analytics Dashboard", "✅ Pending Queue", "⏳ Resolution Queue"])
        
        # --- TAB 1: DASHBOARD ---
        with tab_dash:
            st.subheader("Team Output Volume")
            if not subs_df.empty:
                # Graph showing how many submissions each head has made
                head_counts = subs_df['Name'].value_counts()
                st.bar_chart(head_counts)
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Submissions", len(subs_df))
                col2.metric("Verified", len(subs_df[subs_df['Status'] == 'Verified']))
                col3.metric("Delayed", len(subs_df[subs_df['Status'] == 'Delayed']))
                col4.metric("Rejected", len(subs_df[subs_df['Status'] == 'Rejected']))
            
            st.markdown("---")
            st.subheader("Department Active Projects (What they are doing vs Done)")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.write("**Research (Industries)**")
                try: st.dataframe(pd.DataFrame(get_sheet("Industries").get_all_records()), use_container_width=True, hide_index=True)
                except: st.caption("No data yet.")
            with c2:
                st.write("**Research (Case Studies)**")
                try: st.dataframe(pd.DataFrame(get_sheet("Case_Studies").get_all_records()), use_container_width=True, hide_index=True)
                except: st.caption("No data yet.")
            with c3:
                st.write("**Digital (Insight Series)**")
                try: st.dataframe(pd.DataFrame(get_sheet("Insight_Series").get_all_records()), use_container_width=True, hide_index=True)
                except: st.caption("No data yet.")

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
                                    st.success(f"Verified {row['Name']}'s update!")
                                    st.rerun()
                                    
                                if delay_btn or reject_btn:
                                    if not delay_reason:
                                        st.error("Please provide a reason to delay or reject.")
                                    else:
                                        new_status = "Rejected" if reject_btn else "Delayed"
                                        sheet.update_cell(idx + 2, 5, new_status)
                                        sheet.update_cell(idx + 2, 6, delay_reason)
                                        
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
                                        st.success("Resolved and Verified!")
                                        st.rerun()
                                    if st.form_submit_button("Reject Explanation"):
                                        sheet.update_cell(idx + 2, 5, "Rejected")
                                        st.error("Explanation Rejected.")
                                        st.rerun()
                            else:
                                st.error("Waiting for the Head to provide their explanation.")
