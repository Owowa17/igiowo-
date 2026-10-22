import streamlit as st
import requests
import os
import io
import json
import numpy as np
import pandas as pd
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime
from dotenv import load_dotenv

st.set_page_config(page_title="AI Sharp Bettor", page_icon="📈", layout="wide")

# ==========================================================
# SECRETS & CONSTANTS
# ==========================================================
load_dotenv()

def get_secret(key):
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.getenv(key)

GOOGLE_SHEET_URL = get_secret("GOOGLE_SHEET_URL")
GROQ_API_KEY = get_secret("GROQ_API_KEY")
FC_API_KEY = get_secret("FC_API_KEY")

if not all([GOOGLE_SHEET_URL, GROQ_API_KEY, FC_API_KEY]):
    st.error("⚠️ Missing Secrets! Please ensure GOOGLE_SHEET_URL, GROQ_API_KEY, and FC_API_KEY are set.")
    st.stop()

MODEL = "openai/gpt-oss-20b"
BASE_URL = "https://footballcharts-backend.onrender.com/api/v1"

# ==========================================================
# FOOTBALL-CHARTS API LAYER
# ==========================================================
def get_headers():
    return {"Authorization": f"Bearer {FC_API_KEY}"}

def fetch_fixtures(league_slug):
    """Fetches upcoming fixtures for a league with HT probabilities."""
    try:
        url = f"{BASE_URL}/leagues/{league_slug}/fixtures/"
        r = requests.get(url, headers=get_headers(), timeout=15)
        
        if r.status_code != 200:
            return None, f"API Error {r.status_code}: {r.text[:100]}"
            
        data = r.json()
        matches = []
        
        for match in data.get("matches", []):
            # Football-Charts provides HT Over 1.5 probability directly
            ht_over_15 = match.get("probabilities", {}).get("ht_over_1_5", 0.5)
            # We want HT Under 1.5
            ht_under_15 = 1.0 - ht_over_15
            
            matches.append({
                "team1": match.get("home_team", "Unknown"),
                "team2": match.get("away_team", "Unknown"),
                "league": league_slug,
                "time": match.get("kickoff", ""),
                "ht_under_prob": ht_under_15
            })
            
        return matches, None
    except Exception as e:
        return None, f"Exception: {str(e)}"

# ==========================================================
# CLIENTS
# ==========================================================
@st.cache_resource
def init_clients():
    return Groq(api_key=GROQ_API_KEY), RapidOCR()

groq_client, ocr_engine = init_clients()

def llm(prompt, temperature=0.1):
    r = groq_client.chat.completions.create(
        model=MODEL, 
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature
    )
    return r.choices[0].message.content

# ==========================================================
# MAIN UI
# ==========================================================
st.title("📈 AI Sharp Bettor & Auditor")
tab_daily, tab_manual, tab_auditor, tab_settings = st.tabs(
    ["🎯 Hunter & Daily Picks", "📸 Manual Bettor", "🔍 Auditor", "⚙️ Settings"]
)

# ---------------------------------------------------------- TAB 1
with tab_daily:
    st.header("Match Hunter: HALF-TIME Under 1.5 goals")
    st.caption("Probabilities are fetched directly from the Football-Charts model.")
    
    # League Selection
    league_options = {
        "Premier League": "premier",
        "La Liga": "laliga",
        "Bundesliga": "bundesliga",
        "Serie A": "seriea",
        "Ligue 1": "ligue1",
        "Eredivisie": "eredivisie"
    }
    
    selected_league_name = st.selectbox("Select a League", list(league_options.keys()))
    league_slug = league_options[selected_league_name]
    
    if st.button("📥 Fetch Fixtures", type="primary"):
        with st.spinner("Fetching from Football-Charts..."):
            fixtures, err = fetch_fixtures(league_slug)
            
        if err:
            st.error(err)
        elif not fixtures:
            st.warning("No upcoming fixtures found for this league.")
        else:
            st.session_state.fixtures = fixtures
            st.success(f"Loaded {len(fixtures)} fixtures from {selected_league_name}.")

    # Display Results
    if "fixtures" in st.session_state and st.session_state.fixtures:
        fixtures = st.session_state.fixtures
        
        # Filter Slider
        thr = st.slider("Minimum HT Under 1.5 Probability (%)", 20, 70, 40)
        
        # Filter matches based on threshold
        candidates = []
        for f in fixtures:
            prob_pct = round(f["ht_under_prob"] * 100, 1)
            if prob_pct >= thr:
                candidates.append({
                    "Match": f"{f['team1']} vs {f['team2']}",
                    "Time": f["time"],
                    "HT Under 1.5 %": prob_pct,
                    "Fair Odds": round(1 / f['ht_under_prob'], 2) if f['ht_under_prob'] > 0 else 0.0
                })
                
        if candidates:
            st.subheader(f"🎯 Top Picks (≥ {thr}%)")
            # Sort by highest probability
            candidates = sorted(candidates, key=lambda x: x["HT Under 1.5 %"], reverse=True)
            st.dataframe(pd.DataFrame(candidates), use_container_width=True, hide_index=True)
        else:
            st.info(f"No matches found with HT Under 1.5 probability above {thr}%.")

# ---------------------------------------------------------- TAB 2
with tab_manual:
    st.header("Manual Bet Analysis")
    st.caption("AI opinion only (no statistics).")
    uploaded_file = st.file_uploader("Upload Bet Slip", type=["png", "jpg", "jpeg"])
    
    if uploaded_file:
        image = Image.open(uploaded_file).convert("RGB")
        st.image(image, caption="Uploaded Slip", use_container_width=True)
        
        if st.button("Extract & Analyze"):
            with st.spinner("Scanning image and analyzing..."):
                result, _ = ocr_engine(np.array(image))
                text = "\n".join(item[1] for item in result) if result else ""
                
                if not text:
                    st.warning("No text found in the image.")
                else:
                    audit_prompt = f"Audit this bet slip. Say which legs look weakest and why, and note that this is opinion without verified statistics.\n\nDetails:\n{text}"
                    st.info(llm(audit_prompt, 0.2))

# ---------------------------------------------------------- TAB 3
with tab_auditor:
    st.header("Performance Auditor")
    if st.button("Fetch History from Google Sheets"):
        with st.spinner("Loading data..."):
            try:
                r = requests.get(GOOGLE_SHEET_URL, timeout=20)
                try:
                    data = r.json()
                    df = pd.DataFrame(data[1:], columns=data[0])
                except Exception:
                    df = pd.read_csv(io.StringIO(r.text))
                    
                st.dataframe(df, use_container_width=True)
                
                if "Result" in df.columns:
                    done = df[df["Result"].astype(str).str.lower().isin(["win", "loss"])]
                    if len(done):
                        win_rate = (done['Result'].astype(str).str.lower() == 'win').mean() * 100
                        st.metric("Win rate (settled bets)", f"{win_rate:.1f}%")
                        if "Profit" in df.columns:
                            total_profit = pd.to_numeric(done['Profit'], errors='coerce').fillna(0).sum()
                            st.metric("Profit (units)", f"{total_profit:.2f}")
            except Exception as e:
                st.error(f"Failed to load Google Sheet: {e}")

# ---------------------------------------------------------- TAB 4
with tab_settings:
    st.header("Settings & Diagnostics")
    
    if st.button("Test Football-Charts API Connection"):
        with st.spinner("Testing..."):
            # Test with premier league
            test_fixtures, err = fetch_fixtures("premier")
            if err:
                st.error(f"Connection failed: {err}")
            else:
                st.success(f"✅ Connected successfully! Found {len(test_fixtures)} upcoming Premier League matches.")
                st.json(test_fixtures[:2]) # Show first 2 as a sample
                
    if st.button("Clear App Cache"):
        st.cache_resource.clear()
        st.session_state.fixtures = None
        st.rerun()
