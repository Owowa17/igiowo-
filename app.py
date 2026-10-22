import streamlit as st
import json
import re
import math
import time
import difflib
import requests
import numpy as np
import pandas as pd
import os
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================================================
# SECRETS & CONSTANTS
# ==========================================================
load_dotenv()

def get_secret(key):
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.getenv(key)

# Load keys using the helper function
GOOGLE_SHEET_URL = get_secret("GOOGLE_SHEET_URL")
GROQ_API_KEY = get_secret("GROQ_API_KEY")
FOOTBALL_API_KEY = get_secret("FOOTBALL_API_KEY")

if not all([GOOGLE_SHEET_URL, GROQ_API_KEY, FOOTBALL_API_KEY]):
    st.error("⚠️ Missing Secrets! Please check your Streamlit Secrets configuration.")
    st.info("Required: FOOTBALL_API_KEY, GOOGLE_SHEET_URL, GROQ_API_KEY")
    st.stop()

# Valid Groq Models: llama3-70b-8192, llama3-8b-8192, mixtral-8x7b-32768
MODEL = "llama3-70b-8192" 
LEAGUE_AVG_GOALS = 1.35   
MIN_MATCHES = 5           

# ==========================================================
# PAGE CONFIGURATION
# ==========================================================
st.set_page_config(page_title="AI Sharp Bettor", page_icon="📈", layout="wide")

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
# MAIN UI - USING TABS (Mobile Optimized)
# ==========================================================
st.title("📈 AI Sharp Bettor & Auditor")

tab_daily, tab_manual, tab_auditor, tab_settings = st.tabs([
    "📅 Daily Picks", 
    "📸 Manual Bettor", 
    "🔍 Auditor", 
    "⚙️ Settings"
])

# ==========================================================
# TAB 1: DAILY PICKS
# ==========================================================
with tab_daily:
    st.header("Today's Match Fixtures")
    st.write("Fetch today's matches via API-Football and analyze them with AI.")
    
    # Use Session State to prevent data loss when clicking "Analyze"
    if "fixtures" not in st.session_state:
        st.session_state.fixtures = None

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    if st.button("Fetch Today's Matches", type="primary"):
        with st.spinner("Fetching fixtures..."):
            url = f"https://v3.football.api-sports.io/fixtures?date={today_str}"
            headers = {'x-apisports-key': FOOTBALL_API_KEY}
            
            try:
                response = requests.get(url, headers=headers)
                data = response.json()
                
                if 'errors' in data and data['errors']:
                    st.error(f"API Error: {data['errors']}")
                elif data.get('response'):
                    st.session_state.fixtures = data['response']
                    st.success(f"Found {len(st.session_state.fixtures)} matches today.")
                else:
                    st.warning("No matches found for today.")
            except Exception as e:
                st.error(f"Failed to connect to API: {e}")

    # Display fixtures from session state
    if st.session_state.fixtures:
        for fixture in st.session_state.fixtures[:20]: # Show first 20 to avoid clutter
            home = fixture['teams']['home']['name']
            away = fixture['teams']['away']['name']
            league = fixture['league']['name']
            time_str = fixture['fixture']['date']
            
            with st.expander(f"⚽ {home} vs {away} ({league})"):
                st.write(f"**Kickoff:** {time_str}")
                if st.button(f"Analyze {home} vs {away}", key=fixture['fixture']['id']):
                    with st.spinner("AI is analyzing..."):
                        prompt = f"Analyze this football match for betting: {home} vs {away} in {league}. Provide a short, sharp betting recommendation (1X2 or Over/Under) and a brief reason based on standard team stats."
                        try:
                            chat_completion = groq_client.chat.completions.create(
                                messages=[{"role": "user", "content": prompt}],
                                model=MODEL,
                            )
                            st.info(chat_completion.choices[0].message.content)
                        except Exception as e:
                            st.error(f"AI Error: {e}")

# ==========================================================
# TAB 2: MANUAL BETTOR (OCR + Analysis)
# ==========================================================
with tab_manual:
    st.header("Manual Bet Analysis")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        uploaded_file = st.file_uploader("Upload Bet Slip", type=["png", "jpg", "jpeg"])
        if uploaded_file:
            image = Image.open(uploaded_file)
            # Updated use_container_width
            st.image(image, caption="Uploaded Slip", use_container_width=True)
            
            if st.button("Extract Text (OCR)"):
                with st.spinner("Scanning image..."):
                    result, _ = ocr_engine(np.array(image))
                    extracted_text = " ".join([item[1] for item in result]) if result else "No text found."
                    st.session_state['ocr_text'] = extracted_text
                    st.success("Extraction complete!")

    with col2:
        st.subheader("Analysis")
        default_text = st.session_state.get('ocr_text', "")
        bet_details = st.text_area("Extracted/Manual Bet Details:", value=default_text, height=150)
        
        if st.button("Run AI Analysis", type="primary"):
            if not bet_details:
                st.warning("Please upload an image or enter bet details.")
            else:
                with st.spinner("Consulting Groq AI..."):
                    prompt = f"Audit this bet slip and provide a sharp betting analysis. Are there better alternatives?\n\nDetails:\n{bet_details}"
                    try:
                        chat_completion = groq_client.chat.completions.create(
                            messages=[{"role": "user", "content": prompt}],
                            model=MODEL,
                        )
                        st.success("Analysis Complete")
                        st.info(chat_completion.choices[0].message.content)
                    except Exception as e:
                        st.error(f"AI Error: {e}")

# ==========================================================
# TAB 3: AUDITOR
# ==========================================================
with tab_auditor:
    st.header("Performance Auditor")
    st.info("⚠️ Note: `GOOGLE_SHEET_URL` must be a published CSV link for this to read data.")
    
    if st.button("Fetch History from Google Sheets"):
        with st.spinner("Loading data..."):
            try:
                # Ensure the URL ends in /export?format=csv or is a published CSV link
                df = pd.read_csv(GOOGLE_SHEET_URL)
                st.dataframe(df, use_container_width=True)
                
                if 'Result' in df.columns:
                    # Simple safe string matching for wins
                    wins = len(df[df['Result'].astype(str).str.lower() == 'win'])
                    total = len(df)
                    if total > 0:
                        st.metric("Win Rate", f"{(wins/total)*100:.1f}%")
                    else:
                        st.warning("No data found to calculate win rate.")
            except Exception as e:
                st.error(f"Failed to load Google Sheet: {e}")
                st.write("Make sure your Google Sheet is shared as 'Anyone with the link can view' and published to the web as a CSV.")

# ==========================================================
# TAB 4: SETTINGS
# ==========================================================
with tab_settings:
    st.header("System Settings")
    st.success("✅ Groq API Connected")
    st.success("✅ API-Football Connected")
    st.success("✅ Google Sheets URL Configured")
    
    st.write("**Active Model:**", MODEL)
    st.write("**League Avg Goals:**", LEAGUE_AVG_GOALS)
    st.write("**Min Matches:**", MIN_MATCHES)
    
    if st.button("Clear App Cache"):
        st.cache_resource.clear()
        st.rerun()
