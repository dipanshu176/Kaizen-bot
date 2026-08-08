import streamlit as st
import gspread
import pandas as pd
from datetime import datetime
import pytz
import json

# ==========================================
# 1. EASY-EDIT FORM CONFIGURATION
# Modify these lists to change the questions asked to each role.
# ==========================================
FORM_CONFIG = {
    "Head of Projects": [
        {"label": "Emails Sent Today", "type": "number"},
        {"label": "Calls Made Today", "type": "number"},
        {"label": "Future Plans & Blockers", "type": "text"}
    ],
    "Head of Research": [
        {"label": "Current Primer/Case Study Topic", "type": "text"},
        {"label": "Current Status", "type": "dropdown", "options": ["Drafting", "Review", "Final"]},
        {"label": "Future Plans", "type": "text"}
    ],
    "Head of Digital": [
        {"label": "Content/Instagram URLs", "type": "text"},
        {"label": "Future Content Plans", "type": "text"}
    ]
}
# ==========================================

# Connect to Google Sheets
def get_sheet(sheet_name):
    credentials = dict(st.secrets["gcp_service_account"])
    gc = gspread.service_account_from_dict(credentials)
    # REPLACE with your actual Google Sheet exact name or URL
    sh = gc.open("Kaizen_Management_Database") 
    return sh.worksheet(sheet_name)

# --- Authentication ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("Kaizen Chronicles Portal")
    email_input = st.text_input("Email")
    pass_input = st.text_input("Password", type="password")
    
    if st.button("Login"):
        users_sheet = get_sheet("Users")
        users_data = users_sheet.get_all_records()
        df_users = pd.DataFrame(users_data)
        
        user_match = df_users[(df_users['Email'] == email_input) & (df_users['Password'] == str(pass_input))]
        
        if not user_match.empty:
            st.session_state.logged_in = True
            st.session_state.name = user_match.iloc[0]['Name']
            st.session_state.role = user_match.iloc[0]['Role']
            st.rerun()
        else:
            st.error("Invalid credentials.")

else:
    # --- Sidebar Navigation ---
    st.sidebar.title(f"Welcome, {st.session_state.name}")
    st.sidebar.write(f"**Role:** {st.session_state.role}")
    
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    # --- VIEWS BASED ON ROLE ---

    # 1. HEADS VIEW (Submit Updates)
    if st.session_state.role in FORM_CONFIG:
        st.header("Daily Update Submission")
        
        with st.form("update_form"):
            responses = {}
            for field in FORM_CONFIG[st.session_state.role]:
                if field["type"] == "text":
                    responses[field["label"]] = st.text_area(field["label"])
                elif field["type"] == "number":
                    responses[field["label"]] = st.number_input(field["label"], min_value=0)
                elif field["type"] == "dropdown":
                    responses[field["label"]] = st.selectbox(field["label"], field["options"])
            
            submitted = st.form_submit_button("Submit EOD Update")
            if submitted:
                ist_now = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M:%S")
                # Format answers cleanly so you can easily read them in the sheet
                formatted_data = "\n".join([f"{k}: {v}" for k, v in responses.items()])
                
                sheet = get_sheet("Submissions")
                sheet.append_row([ist_now, st.session_state.name, st.session_state.role, formatted_data, "Pending"])
                st.success("Update submitted successfully!")

    # 2. ADVISORY BOARD & ED VIEW (Verification)
    if st.session_state.role in ["Advisory Board", "Executive Director"]:
        st.header("Verify Team Submissions")
        sheet = get_sheet("Submissions")
        df_subs = pd.DataFrame(sheet.get_all_records())
        
        if not df_subs.empty:
            pending = df_subs[df_subs['Status'] == 'Pending']
            if pending.empty:
                st.info("No pending submissions to verify.")
            else:
                for idx, row in pending.iterrows():
                    with st.expander(f"{row['Name']} - {row['Role']} ({row['Timestamp']})"):
                        st.text(row['Submission_Data'])
                        if st.button("Verify Contribution", key=f"verify_{idx}"):
                            # Update Google Sheet directly (index + 2 for headers and 0-indexing)
                            sheet.update_cell(idx + 2, 5, "Verified")
                            st.success("Verified!")
                            st.rerun()
                            
    # 3. EXECUTIVE DIRECTOR (Admin Panel)
    if st.session_state.role == "Executive Director":
        st.markdown("---")
        st.header("Admin Panel: Manage Users")
        
        col1, col2 = st.columns(2)
        with col1:
            new_name = st.text_input("New User Name")
            new_email = st.text_input("New User Email")
        with col2:
            new_pass = st.text_input("New User Password (Plain Text)")
            new_role = st.selectbox("Role", ["Head of Projects", "Head of Research", "Head of Digital", "Advisory Board", "Executive Director"])
            
        if st.button("Add User"):
            u_sheet = get_sheet("Users")
            u_sheet.append_row([new_name, new_email, new_pass, new_role])
            st.success(f"Added {new_name} to the database.")