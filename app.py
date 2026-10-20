import streamlit as st
import io
import json
import re
import requests
import gspread
import pandas as pd
import time
from google.oauth2.service_account import Credentials
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Volatile Market Hunter PRO", page_icon="🦈", layout="wide")
st.title("🦈 AI Market Hunter + Real-Time Stats")

# --- CONFIGURATION ---
GOOGLE_SHEET_URL = st.secrets["GOOGLE_SHEET_URL"]
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
API_FOOTBALL_KEY = st.secrets["API_FOOTBALL_KEY"]

# --- GOOGLE SHEETS CONNECTION ---
@st.cache_resource
def get_sheet_connection():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_url(GOOGLE_SHEET_URL).sheet1
        return sheet
    except Exception as e:
        return None

sheet = get_sheet_connection()

# --- REAL-TIME STATS API FUNCTION ---
def fetch_team_stats(team_name, api_key):
    """Fetches the last 5 matches for a team using Direct API-Football"""
    headers = {
        "x-apisports-key": api_key # Direct API key header
    }
    
    # 1. Search for the team ID
    search_url = f"https://v3.football.api-sports.io/teams?search={team_name}"
    try:
        search_res = requests.get(search_url, headers=headers).json()
        if not search_res.get('response'):
            return None 
        
        team_id = search_res['response'][0]['team']['id']
        
        # 2. Get last 5 matches
        fixtures_url = f"https://v3.football.api-sports.io/fixtures?team={team_id}&last=5"
        fixtures_res = requests.get(fixtures_url, headers=headers).json()
        
        if not fixtures_res.get('response'):
            return None
            
        goals_scored = 0
        goals_conceded = 0
        
        for fixture in fixtures_res['response']:
            home_id = fixture['teams']['home']['id']
            home_goals = fixture['goals']['home'] or 0
            away_goals = fixture['goals']['away'] or 0
            
            if home_id == team_id:
                goals_scored += home_goals
                goals_conceded += away_goals
            else:
                goals_scored += away_goals
                goals_conceded += home_goals
                
        return {
            "avg_scored": round(goals_scored / 5, 2),
            "avg_conceded": round(goals_conceded / 5, 2),
            "total_goals_last_5": goals_scored + goals_conceded
        }
    except Exception as e:
        return None

# --- CREATE TABS ---
tab1, tab2 = st.tabs(["📸 Scanner", "📊 Tracker & Performance"])

# ==========================================
# TAB 1: THE SCANNER
# ==========================================
with tab1:
    uploaded_file = st.file_uploader("Upload a screenshot (PNG or JPG)", type=["png", "jpg", "jpeg"])

    if uploaded_file is not None:
        img = Image.open(uploaded_file)
        st.image(img, caption="Uploaded Screenshot", use_column_width=True)
        
        with st.spinner("👁️ Step 1: Extracting matches from image..."):
            ocr = RapidOCR()
            temp_path = "temp_screenshot.png"
            img.save(temp_path)
            result, _ = ocr(temp_path)
            
            if not result:
                st.warning("⚠️ No text found.")
            else:
                extracted_lines = [line[1] for line in result]
                raw_ocr_text = "\n".join(extracted_lines)
                
                # STEP 1: AI EXTRACTS BASIC DATA
                client = Groq(api_key=GROQ_API_KEY)
                extract_prompt = f"""
                Extract the matches from this text: {raw_ocr_text}
                Return a JSON array with keys: "time", "league", "team1", "team2", "odds_1", "odds_x", "odds_2".
                Return ONLY valid JSON.
                """
                
                try:
                    response = client.chat.completions.create(
                        model="openai/gpt-oss-120b",
                        messages=[{"role": "user", "content": extract_prompt}],
                        temperature=0.1,
                    )
                    content = response.choices[0].message.content
                    match = re.search(r'\[.*\]', content, re.DOTALL)
                    
                    if match:
                        matches = json.loads(match.group(0))
                        st.success(f"✅ Extracted {len(matches)} matches. Fetching real-time stats...")
                        
                        # STEP 2: FETCH STATS AND PREDICT
                        with st.spinner("🧠 Step 2: Fetching Team Stats and Predicting..."):
                            today_date = datetime.now().strftime("%Y-%m-%d")
                            final_picks = []
                            
                            for m in matches:
                                t1 = m.get('team1', '')
                                t2 = m.get('team2', '')
                                
                                # Fetch stats
                                stats1 = fetch_team_stats(t1, API_FOOTBALL_KEY)
                                time.sleep(0.5) # Delay to respect free tier limits
                                stats2 = fetch_team_stats(t2, API_FOOTBALL_KEY)
                                time.sleep(0.5)
                                
                                # Build context string
                                context = ""
                                if stats1: context += f"{t1} last 5 games: Scored {stats1['avg_scored']}/game, Conceded {stats1['avg_conceded']}/game. "
                                if stats2: context += f"{t2} last 5 games: Scored {stats2['avg_scored']}/game, Conceded {stats2['avg_conceded']}/game. "
                                
                                if not context:
                                    context = "No historical stats available. Rely on odds."
                                
                                # Final AI Prediction with Context
                                predict_prompt = f"""
                                You are a betting expert. Match: {t1} vs {t2}.
                                Odds: 1={m.get('odds_1')}, X={m.get('odds_x')}, 2={m.get('odds_2')}.
                                Context: {context}
                                
                                Based on the odds and the goal stats, pick the best market: 'Over 2.5 Goals', 'Under 2.5 Goals', 'Home Win', 'Away Win', or 'Draw'.
                                Return ONLY JSON with keys: "pick" and "confidence" (1-100).
                                """
                                
                                p_res = client.chat.completions.create(
                                    model="openai/gpt-oss-120b",
                                    messages=[{"role": "user", "content": predict_prompt}],
                                    temperature=0.1,
                                )
                                
                                p_match = re.search(r'\{.*\}', p_res.choices[0].message.content, re.DOTALL)
                                pick_data = json.loads(p_match.group(0)) if p_match else {"pick": "No Value", "confidence": 0}
                                
                                # Combine everything
                                final_entry = {
                                    "time": m.get('time'), "league": m.get('league'),
                                    "team1": t1, "team2": t2,
                                    "odds": m.get('odds_1'), "m1": m.get('odds_x'), "m2": m.get('odds_2'),
                                    "Pick": pick_data.get('pick'), "Confidence": int(float(pick_data.get('confidence', 0))),
                                    "Context": context
                                }
                                final_picks.append(final_entry)
                                
                                # Send to Sheet
                                payload = {
                                    "date": today_date, "time": final_entry["time"], "league": final_entry["league"],
                                    "team1": final_entry["team1"], "team2": final_entry["team2"],
                                    "odds": final_entry["odds"], "m1": final_entry["m1"], "m2": final_entry["m2"],
                                    "Pick": final_entry["Pick"], "Confidence": str(final_entry["Confidence"])
                                }
                                requests.post(GOOGLE_SHEET_URL, json=payload)
                            
                            # Display Results
                            for p in final_picks:
                                conf = p['Confidence']
                                risk = "LOW" if conf >= 75 else "MED" if conf >= 50 else "HIGH"
                                with st.expander(f"⚽ {p['team1']} vs {p['team2']} - {risk} RISK"):
                                    st.write(f"**Pick:** {p['Pick']} | **Confidence:** {conf}%")
                                    st.info(f"📊 **Stats Context:** {p['Context']}")
                                    
                    else:
                        st.error("❌ AI could not extract matches.")
                except Exception as e:
                    st.error(f"❌ Error: {e}")

# ==========================================
# TAB 2: THE TRACKER & PERFORMANCE
# ==========================================
with tab2:
    st.header("📊 Betting Tracker")
    
    if sheet:
        try:
            data = sheet.get_all_records()
            if data:
                df = pd.DataFrame(data)
                
                if 'Result' not in df.columns: df['Result'] = ''
                if 'Profit' not in df.columns: df['Profit'] = 0.0

                df['Profit'] = pd.to_numeric(df['Profit'], errors='coerce').fillna(0)

                total_bets = len(df)
                settled_bets = len(df[df['Result'].isin(['Win', 'Loss'])])
                wins = len(df[df['Result'] == 'Win'])
                win_rate = (wins / settled_bets * 100) if settled_bets > 0 else 0
                total_profit = df['Profit'].sum()
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Scans", total_bets)
                col2.metric("Settled Bets", settled_bets)
                col3.metric("Win Rate", f"{win_rate:.1f}%")
                col4.metric("Total Profit (Units)", f"{total_profit:.2f}")

                st.write("---")
                st.subheader("⚖️ Settle Pending Matches")
                
                pending = df[df['Result'] == '']
                
                if pending.empty:
                    st.success("🎉 All matches are settled!")
                else:
                    for index, row in pending.iterrows():
                        with st.container():
                            cols = st.columns([4, 2, 2, 2])
                            cols[0].write(f"**{row['Team 1']} vs {row['Team 2']}**")
                            cols[1].write(f"Pick: {row['Pick']}")
                            cols[2].write(f"Odds: {row['Odds']}")
                            
                            btn_col1, btn_col2 = cols[3].columns(2)
                            
                            if btn_col1.button("✅ Win", key=f"win_{index}"):
                                row_num = index + 2
                                try: profit = float(row['Odds']) - 1.0
                                except: profit = 0.0
                                sheet.update_cell(row_num, 11, "Win")
                                sheet.update_cell(row_num, 12, profit)
                                st.rerun()
                                
                            if btn_col2.button("❌ Loss", key=f"loss_{index}"):
                                row_num = index + 2
                                sheet.update_cell(row_num, 11, "Loss")
                                sheet.update_cell(row_num, 12, -1.0)
                                st.rerun()
                                
                st.write("---")
                st.subheader("📜 Full History")
                st.dataframe(df, use_container_width=True)
                
            else:
                st.info("No data yet. Scan a screenshot to get started.")
        except Exception as e:
            st.error(f"Error loading tracker: {e}")
