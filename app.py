import streamlit as st
import json
import re
import math
import time
import difflib
import requests
import numpy as np
import pandas as pd
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime, timezone, timedelta

# ==========================================================
# PAGE CONFIGURATION
# ==========================================================
st.set_page_config(page_title="AI Sharp Bettor", page_icon="📈", layout="wide")

# ==========================================================
# SECRETS & CONSTANTS
# ==========================================================
try:
    GOOGLE_SHEET_URL = st.secrets["GOOGLE_SHEET_URL"]
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
    API_FOOTBALL_KEY = st.secrets["API_FOOTBALL_KEY"]
except KeyError:
    st.error("⚠️ Missing Secrets! Please check your Streamlit Secrets configuration.")
    st.stop()

MODEL = "openai/gpt-oss-120b"
LEAGUE_AVG_GOALS = 1.35 
MIN_MATCHES = 5           

# ==========================================================
# INITIALIZE CLIENTS
# ==========================================================
@st.cache_resource
def init_clients():
    groq_client = Groq(api_key=GROQ_API_KEY)
    ocr_engine = RapidOCR()
    return groq_client, ocr_engine

groq_client, ocr_engine = init_clients()

# ==========================================================
# SIDEBAR MODE SELECTION
# ==========================================================
st.sidebar.title("Navigation")
app_mode = st.sidebar.radio(
    "Select Mode",
    ["Sharp Bettor", "Daily Picks", "Auditor", "Settings"] # <-- ADDED "Daily Picks"
)

st.sidebar.markdown("---")
st.sidebar.info(f"**Status:** Connected\n\n**Model:** {MODEL}")

# ==========================================================
# MODE 1: SHARP BETTOR (Manual Input)
# ==========================================================
def run_sharp_bettor():
    st.title("📈 AI Sharp Bettor")
    st.markdown("Upload a bet slip image or enter match details to get sharp betting insights.")
    # ... (keep your existing Sharp Bettor code here) ...
    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("1. Input Data")
        uploaded_file = st.file_uploader("Upload Bet Slip (Image)", type=["png", "jpg", "jpeg"])
        # ... (OCR logic) ...
    with col2:
        st.subheader("2. Analysis")
        if st.button("Run AI Analysis"):
            # ... (Groq analysis logic) ...
            st.success("Analysis Complete!")
            st.info("Placeholder: The AI model suggests a value bet.")

# ==========================================================
# MODE 2: DAILY PICKS (NEW MODE)
# ==========================================================
def run_daily_picks():
    st.title("📅 Daily Picks")
    st.markdown("Fetch today's fixtures and get AI-driven insights.")

    # API-Football endpoint for fixtures on a specific date
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    if st.button("Fetch Today's Matches"):
        with st.spinner("Fetching fixtures from API-Football..."):
            url = f"https://v3.football.api-sports.io/fixtures?date={today_str}"
            headers = {
                'x-apisports-key': API_FOOTBALL_KEY
            }
            
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                
                if data.get('response'):
                    fixtures = data['response']
                    
                    # Display matches in a list
                    st.subheader(f"Found {len(fixtures)} Matches Today")
                    
                    for fixture in fixtures[:10]: # Limiting to first 10 for display
                        home_team = fixture['teams']['home']['name']
                        away_team = fixture['teams']['away']['name']
                        league_name = fixture['league']['name']
                        match_time = fixture['fixture']['date']
                        
                        with st.expander(f"{home_team} vs {away_team} ({league_name})"):
                            st.write(f"**Kickoff:** {match_time}")
                            # Placeholder for AI analysis button for this specific match
                            if st.button(f"Analyze {home_team} vs {away_team}"):
                                st.info("AI analysis will appear here (Connect your Groq logic).")
                else:
                    st.warning("No matches found for today.")
                    
            except Exception as e:
                st.error(f"Error fetching data: {e}")

# ==========================================================
# MODE 3: AUDITOR
# ==========================================================
def run_auditor():
    # ... (keep your existing Auditor code here) ...
    st.title("🔍 Auditor Mode")
    st.markdown("Review historical performance.")

# ==========================================================
# MODE 4: SETTINGS
# ==========================================================
def run_settings():
    # ... (keep your existing Settings code here) ...
    st.title("⚙️ Settings & Configuration")

# ==========================================================
# MAIN APP ROUTER
# ==========================================================
if __name__ == "__main__":
    if app_mode == "Sharp Bettor":
        run_sharp_bettor()
    elif app_mode == "Daily Picks":      # <-- ADDED ROUTING
        run_daily_picks()
    elif app_mode == "Auditor":
        run_auditor()
    elif app_mode == "Settings":
        run_settings()
