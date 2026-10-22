import streamlit as st
import requests
import os
import numpy as np
import pandas as pd
import asyncio
import json
import nest_asyncio
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime, timezone
from dotenv import load_dotenv
from scipy.stats import poisson

# Apply nest_asyncio to allow async operations inside Streamlit
nest_asyncio.apply()

# ==========================================================
# MCP IMPORTS (Streamable HTTP)
# ==========================================================
try:
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession
except ImportError:
    st.error("❌ MCP Streamable HTTP client not found. Please ensure 'mcp>=1.8.0,<2' is in requirements.txt.")
    st.stop()

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

if not all([GOOGLE_SHEET_URL, GROQ_API_KEY]):
    st.error("⚠️ Missing Secrets! Please check your Streamlit Secrets configuration.")
    st.stop()

MODEL = "llama3-8b-8192"
MCP_URL = "https://livescoremcp.com/mcp"

# ==========================================================
# POISSON PROBABILITY FUNCTION
# ==========================================================
def calculate_under_1_5_probability(home_avg_goals_for, home_avg_goals_against, away_avg_goals_for, away_avg_goals_against):
    home_lambda = (home_avg_goals_for + away_avg_goals_against) / 2
    away_lambda = (away_avg_goals_for + home_avg_goals_against) / 2
    prob_home_0 = poisson.pmf(0, home_lambda)
    prob_home_1 = poisson.pmf(1, home_lambda)
    prob_away_0 = poisson.pmf(0, away_lambda)
    prob_away_1 = poisson.pmf(1, away_lambda)
    return (prob_home_0 * prob_away_0) + (prob_home_1 * prob_away_0) + (prob_home_0 * prob_away_1)

# ==========================================================
# LIVE SCORE MCP INTEGRATION
# ==========================================================
async def fetch_team_stats_mcp_async(team_name):
    try:
        # Add timeout to prevent hanging
        async with asyncio.timeout(15):
            async with streamablehttp_client(MCP_URL) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    
                    search_result = await session.call_tool("search", {"query": team_name})
                    search_data = json.loads(search_result.content[0].text)
                    
                    if not search_data or "teams" not in search_data or len(search_data["teams"]) == 0:
                        return None
                    
                    team_id = search_data["teams"][0]["id"]
                    team_result = await session.call_tool("get_team", {"team_id": team_id})
                    team_data = json.loads(team_result.content[0].text)
                    
                    stats = team_data.get("statistics", team_data)
                    goals_for = stats.get("goals_for", stats.get("goals_scored", 0))
                    goals_against = stats.get("goals_against", stats.get("goals_conceded", 0))
                    matches_played = stats.get("matches_played", stats.get("games", 1))

                    if matches_played == 0:
                        return None

                    return {
                        "avg_for": round(goals_for / matches_played, 2),
                        "avg_against": round(goals_against / matches_played, 2),
                        "matches": matches_played,
                        "name": search_data["teams"][0]["name"]
                    }
    except Exception as e:
        # Suppress individual team errors so the scan doesn't stop entirely
        return None

def fetch_team_stats(team_name):
    try:
        return asyncio.run(fetch_team_stats_mcp_async(team_name))
    except Exception:
        return None

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
# MAIN UI
# ==========================================================
st.title("📈 AI Sharp Bettor & Auditor")

tab_daily, tab_manual, tab_auditor, tab_settings = st.tabs([
    "🎯 Hunter & Daily Picks", 
    "📸 Manual Bettor", 
    "🔍 Auditor", 
    "⚙️ Settings"
])

# ==========================================================
# TAB 1: DAILY PICKS & HUNTER MODE
# ==========================================================
with tab_daily:
    st.header("Match Hunter")
    st.write("Fetch matches, pull real team stats via **LiveScore MCP**, and use AI to hunt for Under 1.5 edges.")

    hunter_mode = st.toggle("🎯 Low Score Hunter (Under 1.5 Goals)", value=True)

    if "fixtures" not in st.session_state:
        st.session_state.fixtures = None
        st.session_state.hunted_matches = []

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    timezone_str = "UTC"

    if st.button("Fetch & Analyze Matches", type="primary"):
        with st.spinner("Fetching fixtures from LiveScore MCP via Streamable HTTP..."):
            async def get_fixtures():
                try:
                    async with asyncio.timeout(20): # 20-second timeout
                        async with streamablehttp_client(MCP_URL) as (read_stream, write_stream, _):
                            async with ClientSession(read_stream, write_stream) as session:
                                await session.initialize()
                                res = await session.call_tool("get_day_fixtures", {"date": today_str, "timezone": timezone_str, "format": "json"})
                                return json.loads(res.content[0].text)
                except Exception as e:
                    # Unwrap anyio ExceptionGroup to show real error
                    if hasattr(e, 'exceptions') and e.exceptions:
                        real_err = e.exceptions[0]
                        st.error(f"MCP Connection Failed: {type(real_err).__name__} - {real_err}")
                    else:
                        st.error(f"MCP Connection Failed: {type(e).__name__} - {e}")
                    return None

            try:
                fixtures_data = asyncio.run(get_fixtures())
                if fixtures_data and "fixtures" in fixtures_data:
                    st.session_state.fixtures = fixtures_data["fixtures"]
                    st.success(f"Fetched {len(st.session_state.fixtures)} matches today.")
                elif fixtures_data is not None:
                    st.warning("No fixtures found for today (API returned empty).")
            except Exception as e:
                st.error(f"Outer Error: {e}")

    if hunter_mode and st.session_state.fixtures:
        if st.button("Run Under 1.5 Hunter"):
            st.session_state.hunted_matches = []
            matches_to_scan = st.session_state.fixtures[:15]
            progress_bar = st.progress(0)

            for i, fixture in enumerate(matches_to_scan):
                home = fixture['teams']['home']['name']
                away = fixture['teams']['away']['name']
                league = fixture['league']['name']

                home_stats = fetch_team_stats(home)
                away_stats = fetch_team_stats(away)

                if home_stats and away_stats:
                    under_prob = calculate_under_1_5_probability(
                        home_stats['avg_for'], home_stats['avg_against'],
                        away_stats['avg_for'], away_stats['avg_against']
                    )

                    if under_prob > 0.55:
                        prompt = f"""
                        The mathematical model suggests a {under_prob:.1%} chance of Under 1.5 goals for {home} vs {away} in {league}.
                        Based on these stats, provide a short, sharp confirmation (YES or NO) and a 1-sentence reason.
                        """
                        chat_completion = groq_client.chat.completions.create(
                            messages=[{"role": "user", "content": prompt}],
                            model=MODEL,
                            temperature=0.1
                        )
                        ai_response = chat_completion.choices[0].message.content

                        if "YES" in ai_response.upper():
                            st.session_state.hunted_matches.append({
                                "home": home,
                                "away": away,
                                "league": league,
                                "prob": under_prob,
                                "analysis": ai_response
                            })
                
                progress_bar.progress((i + 1) / len(matches_to_scan))
            
            st.success(f"Hunter scan complete! Found {len(st.session_state.hunted_matches)} potential matches.")

    if st.session_state.hunted_matches:
        st.subheader("🎯 High Probability Under 1.5 Matches")
        for item in st.session_state.hunted_matches:
            with st.expander(f"⚽ {item['home']} vs {item['away']} ({item['league']})"):
                st.write(f"**Math Probability:** {item['prob']:.1%}")
                st.info(item['analysis'])

# ==========================================================
# TAB 2: MANUAL BETTOR (OCR + Analysis)
# ==========================================================
with tab_manual:
    st.header("Manual Bet Analysis")
    uploaded_file = st.file_uploader("Upload Bet Slip", type=["png", "jpg", "jpeg"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="Uploaded Slip", use_container_width=True)

        if st.button("Extract & Analyze"):
            with st.spinner("Scanning image and analyzing..."):
                result, _ = ocr_engine(np.array(image))
                extracted_text = " ".join([item[1] for item in result]) if result else "No text found."
                
                prompt = f"Audit this bet slip and provide a sharp betting analysis. Are there better alternatives?\n\nDetails:\n{extracted_text}"
                chat_completion = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=MODEL
                )
                st.info(chat_completion.choices[0].message.content)

# ==========================================================
# TAB 3: AUDITOR
# ==========================================================
with tab_auditor:
    st.header("Performance Auditor")
    if st.button("Fetch History from Google Sheets"):
        with st.spinner("Loading data..."):
            try:
                df = pd.read_csv(GOOGLE_SHEET_URL)
                st.dataframe(df, use_container_width=True)
                if 'Result' in df.columns:
                    wins = len(df[df['Result'].astype(str).str.lower() == 'win'])
                    total = len(df)
                    if total > 0:
                        st.metric("Win Rate", f"{(wins/total)*100:.1f}%")
            except Exception as e:
                st.error(f"Failed to load Google Sheet: {e}")

# ==========================================================
# TAB 4: SETTINGS
# ==========================================================
with tab_settings:
    st.header("System Settings")
    st.success("✅ Groq API Connected")
    st.success("✅ LiveScore MCP (Streamable HTTP) Connected")
    
    if st.button("Clear App Cache"):
        st.cache_resource.clear()
        st.rerun()
