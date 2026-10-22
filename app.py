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
    """Fetches upcoming fixtures and extracts deep model predictions safely."""
    try:
        url = f"{BASE_URL}/leagues/{league_slug}/fixtures/"
        r = requests.get(url, headers=get_headers(), timeout=15)
        
        if r.status_code != 200:
            return None, f"API Error {r.status_code}: {r.text[:100]}"
            
        data = r.json()
        matches = data.get("matches", [])
        fixtures = []
        
        for match in matches:
            if not isinstance(match, dict):
                continue
                
            model_preds = match.get("model_predictions") or {}
            dc_v2 = model_preds.get("dc_v2") or {}
            raw = dc_v2.get("raw") or {}
            
            if not raw:
                continue
            
            ht_over = raw.get("ht_over_1_5") or raw.get("ht_over_15")
            ht_under = 1.0 - float(ht_over) if ht_over is not None else 0.5
            
            btts_prob = float(raw.get("btts", 0.5))
            ft_under_25 = float(raw.get("under_2_5", 0.5))
            
            fixtures.append({
                "team1": match.get("home_team", "Unknown"),
                "team2": match.get("away_team", "Unknown"),
                "league": league_slug,
                "time": match.get("time", ""),
                "date": match.get("match_date", ""),
                "ht_under_prob": ht_under,
                "btts_prob": btts_prob,
                "ft_under_25_prob": ft_under_25
            })
            
        return fixtures, None
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
    st.caption("Filter by HT Under 1.5 probability, BTTS, and Under 2.5 to find the safest picks.")
    
    # 🔴 NEW: Dropdown + Custom Text Input
    league_options = {
        "Premier League": "premier",
        "La Liga (Spain)": "spain/laliga",
        "Bundesliga (Germany)": "germany/bundesliga",
        "Serie A (Italy)": "italy/serie-a",
        "Ligue 1 (France)": "france/ligue-1",
        "Eredivisie (Netherlands)": "netherlands/eredivisie",
        "Custom Slug...": "custom"
    }
    
    selected_name = st.selectbox("Select a League", list(league_options.keys()))
    
    if selected_name == "Custom Slug...":
        league_slug = st.text_input("Enter Custom League Slug (e.g., 'england/championship')", "")
    else:
        league_slug = league_options[selected_name]
    
    if st.button("📥 Fetch Fixtures", type="primary"):
        if not league_slug:
            st.error("Please provide a league slug.")
        else:
            with st.spinner(f"Fetching from Football-Charts for '{league_slug}'..."):
                fixtures, err = fetch_fixtures(league_slug)
                
            if err:
                st.error(err)
            elif not fixtures:
                st.warning(f"No upcoming fixtures with predictions found for '{league_slug}'. Try a different slug.")
            else:
                st.session_state.fixtures = fixtures
                st.success(f"Loaded {len(fixtures)} fixtures from '{league_slug}'.")

    # Display Results with Multi-Filters
    if "fixtures" in st.session_state and st.session_state.fixtures:
        fixtures = st.session_state.fixtures
        
        st.subheader("🔍 Multi-Factor Filters")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            min_ht_under = st.slider("Min HT Under 1.5 %", 20, 80, 45)
        with col2:
            max_btts = st.slider("Max BTTS %", 20, 80, 45, help="Lower BTTS means a more one-sided or defensive game.")
        with col3:
            min_ft_under = st.slider("Min FT Under 2.5 %", 20, 80, 50, help="High Full-Time Under 2.5 supports a low-scoring game.")
            
        candidates = []
        for f in fixtures:
            ht_pct = round(f["ht_under_prob"] * 100, 1)
            btts_pct = round(f["btts_prob"] * 100, 1)
            ft_under_pct = round(f["ft_under_25_prob"] * 100, 1)
            
            if (ht_pct >= min_ht_under) and (btts_pct <= max_btts) and (ft_under_pct >= min_ft_under):
                candidates.append({
                    "Match": f"{f['team1']} vs {f['team2']}",
                    "Time": f["time"],
                    "HT Under 1.5 %": ht_pct,
                    "BTTS %": btts_pct,
                    "FT Under 2.5 %": ft_under_pct,
                    "Fair Odds": round(1 / f['ht_under_prob'], 2) if f['ht_under_prob'] > 0 else 0.0
                })
                
        if candidates:
            st.subheader(f"🎯 {len(candidates)} High-Confidence Picks")
            candidates = sorted(candidates, key=lambda x: x["HT Under 1.5 %"], reverse=True)
            st.dataframe(pd.DataFrame(candidates), use_container_width=True, hide_index=True)
            
            st.info("💡 **Pro Tip:** If BTTS is very low (< 35%) and HT Under 1.5 is > 50%, this is often a game where one team dominates possession but struggles to score early.")
        else:
            st.warning("No matches met all your filtering criteria. Try loosening the sliders.")

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
with st.settings:
    st.header("Settings & Diagnostics")
    
    if st.button("Clear App Cache"):
        st.cache_resource.clear()
        st.session_state.fixtures = None
        st.rerun()
