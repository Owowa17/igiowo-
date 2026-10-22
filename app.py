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
LEAGUE_AVG_GOALS = 1.35   # per team per match; small samples are pulled toward this
MIN_MATCHES = 5           # minimum recent games per team before the model is trusted

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
    ["Sharp Bettor", "Auditor", "Settings"]
)

st.sidebar.markdown("---")
st.sidebar.info(f"**Status:** Connected\n\n**Model:** {MODEL}")

# ==========================================================
# MODE 1: SHARP BETTOR
# ==========================================================
def run_sharp_bettor():
    st.title("📈 AI Sharp Bettor")
    st.markdown("Upload a bet slip image or enter match details to get sharp betting insights.")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("1. Input Data")
        uploaded_file = st.file_uploader("Upload Bet Slip (Image)", type=["png", "jpg", "jpeg"])
        
        if uploaded_file is not None:
            image = Image.open(uploaded_file)
            st.image(image, caption="Uploaded Image", use_column_width=True)
            
            if st.button("Extract Text via OCR"):
                with st.spinner("Processing image..."):
                    # --- INSERT YOUR RAPIDOCR LOGIC HERE ---
                    # result, _ = ocr_engine(np.array(image))
                    # st.session_state['ocr_text'] = result
                    st.success("OCR Extraction placeholder - add your logic here.")
        
        with st.expander("Or enter manually"):
            team_home = st.text_input("Home Team")
            team_away = st.text_input("Away Team")
            odds_home = st.number_input("Home Odds", min_value=1.0, value=2.0)
            odds_away = st.number_input("Away Odds", min_value=1.0, value=2.0)

    with col2:
        st.subheader("2. Analysis")
        if st.button("Run AI Analysis"):
            with st.spinner("Consulting Groq AI..."):
                # --- INSERT YOUR GROQ / API FOOTBALL LOGIC HERE ---
                # Example prompt construction:
                # prompt = f"Analyze the match {team_home} vs {team_away} with odds..."
                # response = groq_client.chat.completions.create(...)
                
                time.sleep(1.5) # Simulating API call
                st.success("Analysis Complete!")
                st.write("### AI Recommendation")
                st.info("Placeholder: The AI model suggests a value bet on the Home team based on recent form and sharp money indicators.")
                
                # Placeholder for Google Sheets logging
                if st.button("Log to Google Sheets"):
                    st.success("Data logged to Google Sheets (Placeholder).")

# ==========================================================
# MODE 2: AUDITOR
# ==========================================================
def run_auditor():
    st.title("🔍 Auditor Mode")
    st.markdown("Review historical performance, past bets, and model accuracy.")

    tab1, tab2 = st.tabs(["Performance Metrics", "Bet History"])

    with tab1:
        st.subheader("Key Metrics")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Bets", "124", "+12")
        c2.metric("Win Rate", "58%", "+2.1%")
        c3.metric("ROI", "12.4%", "+1.5%")
        c4.metric("Avg Odds", "2.15", "-0.05")

        st.markdown("### Model Confidence vs. Actual Outcome")
        # Placeholder chart
        chart_data = pd.DataFrame(
            np.random.randn(20, 2),
            columns=['Confidence Score', 'Actual Result']
        )
        st.line_chart(chart_data)

    with tab2:
        st.subheader("Historical Data (From Google Sheets)")
        if st.button("Fetch Latest Data"):
            with st.spinner("Fetching from Google Sheets..."):
                # --- INSERT GOOGLE SHEETS FETCH LOGIC HERE ---
                # response = requests.get(GOOGLE_SHEET_URL)
                # df = pd.read_csv(response.content)
                
                time.sleep(1)
                st.success("Data fetched successfully (Placeholder).")
                # Display dummy dataframe
                dummy_df = pd.DataFrame({
                    "Date": ["2023-10-25", "2023-10-26", "2023-10-27"],
                    "Match": ["Arsenal vs Chelsea", "Real Madrid vs Barca", "Bayern vs Dortmund"],
                    "Pick": ["Arsenal ML", "Over 2.5", "Bayern ML"],
                    "Result": ["Win", "Loss", "Win"]
                })
                st.dataframe(dummy_df, use_container_width=True)

# ==========================================================
# MODE 3: SETTINGS
# ==========================================================
def run_settings():
    st.title("⚙️ Settings & Configuration")
    
    st.subheader("Model Parameters")
    st.write(f"**League Average Goals:** {LEAGUE_AVG_GOALS}")
    st.write(f"**Minimum Matches for Trust:** {MIN_MATCHES}")
    st.write(f"**Groq Model:** {MODEL}")
    
    st.markdown("---")
    st.subheader("API Connections")
    st.success("✅ Groq API Key loaded")
    st.success("✅ API-Football Key loaded")
    st.success("✅ Google Sheets URL loaded")
    
    st.markdown("---")
    st.subheader("System Utilities")
    if st.button("Clear Cache & Restart"):
        st.cache_resource.clear()
        st.rerun()

# ==========================================================
# MAIN APP ROUTER
# ==========================================================
if __name__ == "__main__":
    if app_mode == "Sharp Bettor":
        run_sharp_bettor()
    elif app_mode == "Auditor":
        run_auditor()
    elif app_mode == "Settings":
        run_settings()
