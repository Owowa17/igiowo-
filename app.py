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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

@st.cache_data(ttl=86400)
def fetch_leagues():
    """Fetches all available leagues dynamically."""
    try:
        url = f"{BASE_URL}/leagues/"
        r = requests.get(url, headers=get_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        
        leagues = {}
        if isinstance(data, dict) and "leagues" in data:
            data = data["leagues"]
            
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    name = item.get("name")
                    key = item.get("league")
                    if name and key:
                        country = item.get("country", "")
                        display_name = f"{name} ({country})" if country else name
                        leagues[display_name] = str(key)
                        
        return leagues, None
    except Exception as e:
        return {}, f"Failed to fetch leagues: {str(e)}"

def fetch_fixtures(league_key):
    """Fetches fixtures with robust timeout and retry logic for Render cold starts."""
    try:
        url = f"{BASE_URL}/leagues/{league_key}/fixtures/"
        
        # 🔴 Configure a session that retries on timeouts
        session = requests.Session()
        retry_strategy = Retry(
            total=3, # Retry up to 3 times
            backoff_factor=2, # Wait 2s, 4s, 8s between retries
            status_forcelist=[500, 502, 503, 504], # Retry on server errors
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        # 🔴 Increase timeout to 60 seconds to survive cold starts
        r = session.get(url, headers=get_headers(), timeout=60)
        
        if r.status_code != 200:
            return None, f"API Error {r.status_code}: {r.text[:100]}"
            
        data = r.json()
        matches = data.get("matches", [])
        fixtures = []
        
        for match in matches:
            if not isinstance(match, dict):
                continue
                
            # Navigate to dc_v2 (check 'calibrated' first, then 'raw')
            model_preds = match.get("model_predictions") or {}
            dc_v2 = model_preds.get("dc_v2") or {}
            preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
            
            # 1. HT Over 1.5 -> HT Under 1.5
            ht_over = preds.get("ht_over_1.5")
            ht_under_prob = (1.0 - float(ht_over)) if ht_over is not None else 0.5
            
            # 2. BTTS Yes
            btts_yes = preds.get("btts_yes")
            btts_prob = float(btts_yes) if btts_yes is not None else 0.5
            
            # 3. FT Over 2.5 -> FT Under 2.5
            ft_over_25 = preds.get("over_2.5")
            ft_under_25_prob = (1.0 - float(ft_over_25)) if ft_over_25 is not None else 0.5
            
            # Only add the fixture if real predictions exist
            if ht_under_prob != 0.5:
                fixtures.append({
                    "team1": match.get("home_team", "Unknown"),
                    "team2": match.get("away_team", "Unknown"),
                    "league": league_key,
                    "time": match.get("time", ""),
                    "ht_under_prob": ht_under_prob,
                    "btts_prob": btts_prob,
                    "ft_under_25_prob": ft_under_25_prob
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
    st.caption("Filter by HT Under 1.5 probability, BTTS, and FT Under 2.5 to find the safest picks.")
    
    with st.spinner("Loading available leagues..."):
        league_map, league_err = fetch_leagues()
    
    use_manual = False
    if league_err or not league_map:
        use_manual = True
        st.warning(f"Could not load dynamic leagues. Falling back to manual input.")
        
    if not use_manual:
        selected_league_name = st.selectbox("Select a League", sorted(league_map.keys()))
        league_key = league_map[selected_league_name]
        st.info(f"Selected League Key: `{league_key}`")
    else:
        league_key = st.text_input("Enter League Key manually (e.g., 'premier', 'turkey2')", "turkey2")
    
    if st.button("📥 Fetch Fixtures", type="primary"):
        if not league_key:
            st.error("Please provide a league key.")
        else:
            with st.spinner(f"Fetching from Football-Charts for '{league_key}'..."):
                fixtures, err = fetch_fixtures(league_key)
                
            if err:
                st.error(err)
            elif not fixtures:
                st.warning(f"No upcoming fixtures found for '{league_key}'.")
            else:
                st.session_state.fixtures = fixtures
                st.success(f"Loaded {len(fixtures)} fixtures from '{league_key}'.")

    if "fixtures" in st.session_state and st.session_state.fixtures:
        fixtures = st.session_state.fixtures
        
        st.subheader("🔍 Multi-Factor Filters")
        col1, col2, col3 = st.columns(3)
        
        # Default values set to match actual API ranges
        with col1:
            min_ht_under = st.slider("Min HT Under 1.5 %", 30, 90, 60)
        with col2:
            max_btts = st.slider("Max BTTS %", 20, 80, 50, help="Lower BTTS means a more one-sided or defensive game.")
        with col3:
            min_ft_under = st.slider("Min FT Under 2.5 %", 30, 90, 55, help="High FT Under 2.5 supports a low-scoring game.")
            
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
        else:
            st.warning("No matches met all your filtering criteria. Try loosening the sliders.")
            
        with st.expander("🛠️ View All Fetched Data (Debug)"):
            raw_df_data = []
            for f in fixtures:
                raw_df_data.append({
                    "Match": f"{f['team1']} vs {f['team2']}",
                    "HT Under 1.5 %": round(f["ht_under_prob"] * 100, 1),
                    "BTTS %": round(f["btts_prob"] * 100, 1),
                    "FT Under 2.5 %": round(f["ft_under_25_prob"] * 100, 1)
                })
            st.dataframe(pd.DataFrame(raw_df_data), use_container_width=True, hide_index=True)

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
    
    if st.button("Clear App Cache"):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.session_state.fixtures = None
        st.rerun()
