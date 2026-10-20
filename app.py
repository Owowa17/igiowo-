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
st.set_page_config(page_title="AI Sharp Bettor", page_icon="📈", layout="wide")
st.title("📈 AI Sharp Bettor & Market Auditor")

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
        return client.open_by_url(GOOGLE_SHEET_URL).sheet1
    except Exception:
        return None

sheet = get_sheet_connection()

# --- REAL-TIME STATS API FUNCTION ---
def fetch_team_stats(team_name, api_key):
    headers = {"x-apisports-key": api_key}
    try:
        search_url = f"https://v3.football.api-sports.io/teams?search={team_name}"
        search_res = requests.get(search_url, headers=headers).json()
        if not search_res.get('response'): return None
        
        team_id = search_res['response'][0]['team']['id']
        fixtures_url = f"https://v3.football.api-sports.io/fixtures?team={team_id}&last=5"
        fixtures_res = requests.get(fixtures_url, headers=headers).json()
        
        if not fixtures_res.get('response'): return None
            
        goals_scored = 0
        goals_conceded = 0
        for fixture in fixtures_res['response']:
            home_id = fixture['teams']['home']['id']
            hg = fixture['goals']['home'] or 0
            ag = fixture['goals']['away'] or 0
            if home_id == team_id:
                goals_scored += hg
                goals_conceded += ag
            else:
                goals_scored += ag
                goals_conceded += hg
                
        return {"avg_scored": round(goals_scored / 5, 2), "avg_conceded": round(goals_conceded / 5, 2)}
    except Exception:
        return None

# --- TABS ---
tab1, tab2 = st.tabs(["📸 Scanner & Auditor", "📊 Tracker & Performance"])

# ==========================================
# TAB 1: THE SCANNER & AUDITOR
# ==========================================
with tab1:
    st.subheader("1. Choose Input Method")
    input_method = st.radio("How do you want to analyze?", ["Upload Screenshot", "Paste Text / Booking Code"])
    
    raw_text = ""
    
    if input_method == "Upload Screenshot":
        uploaded_file = st.file_uploader("Upload a screenshot", type=["png", "jpg", "jpeg"])
        if uploaded_file is not None:
            img = Image.open(uploaded_file)
            st.image(img, caption="Uploaded Screenshot", use_column_width=True)
            with st.spinner("👁️ Scanning image..."):
                ocr = RapidOCR()
                temp_path = "temp_screenshot.png"
                img.save(temp_path)
                result, _ = ocr(temp_path)
                if result:
                    raw_text = "\n".join([line[1] for line in result])
    else:
        raw_text = st.text_area("Paste your booking details here:", height=200)

    # --- PROCESSING LOGIC ---
    if raw_text:
        st.write("---")
        app_mode = st.radio("Select Strategy:", ["🎯 Find +EV Value", "🕵️ Audit My Booking"])
        
        if st.button("🚀 Run Analysis"):
            client = Groq(api_key=GROQ_API_KEY)
            
            # MODE A: FIND +EV VALUE (Guru Mode)
            if "Find +EV Value" in app_mode:
                extract_prompt = f"Extract matches from: {raw_text}. Return JSON array with: time, league, team1, team2, odds_1, odds_x, odds_2. ONLY JSON."
                try:
                    res = client.chat.completions.create(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": extract_prompt}], temperature=0.1)
                    match = re.search(r'\[.*\]', res.choices[0].message.content, re.DOTALL)
                    
                    if match:
                        matches = json.loads(match.group(0))
                        st.success(f"✅ Found {len(matches)} matches. Calculating True Value...")
                        today_date = datetime.now().strftime("%Y-%m-%d")
                        
                        for m in matches:
                            t1, t2 = m.get('team1', ''), m.get('team2', '')
                            s1 = fetch_team_stats(t1, API_FOOTBALL_KEY)
                            s2 = fetch_team_stats(t2, API_FOOTBALL_KEY)
                            time.sleep(0.5)
                            
                            ctx = ""
                            if s1: ctx += f"{t1}: Scored {s1['avg_scored']}, Conceded {s1['avg_conceded']}. "
                            if s2: ctx += f"{t2}: Scored {s2['avg_scored']}, Conceded {s2['avg_conceded']}. "
                            
                            # CALCULATE IMPLIED PROBABILITY
                            try:
                                o1 = float(m.get('odds_1', 0))
                                ox = float(m.get('odds_x', 0))
                                o2 = float(m.get('odds_2', 0))
                                imp_prob_1 = round((1/o1)*100, 1) if o1 > 0 else 0
                                imp_prob_2 = round((1/o2)*100, 1) if o2 > 0 else 0
                            except:
                                imp_prob_1, imp_prob_2 = 0, 0

                            # THE GURU PROMPT
                            pred_prompt = f"""
                            You are a ruthless sharp bettor. The public bets with emotion; you bet with math. 
                            Match: {t1} vs {t2}. 
                            Odds: Home {m.get('odds_1')} (Implied Prob: {imp_prob_1}%), Draw {m.get('odds_x')}, Away {m.get('odds_2')} (Implied Prob: {imp_prob_2}%).
                            Stats Context: {ctx}
                            
                            Rules:
                            1. The 1X2 market is highly efficient. ONLY pick 'Home Win' or 'Away Win' if the True Probability (based on stats) is at least 10% higher than the Implied Probability.
                            2. Focus on 'Weak Markets': Pick 'Over 2.5 Goals' or 'Both Teams to Score (BTTS)' if the stats show both teams have leaky defenses. 
                            3. If the odds look like a trap (e.g., a heavy favorite with terrible recent stats), write 'TRAP - AVOID'.
                            
                            Return JSON with keys: "pick", "confidence" (1-100), "edge_reason" (why the bookie is wrong).
                            """
                            p_res = client.chat.completions.create(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": pred_prompt}], temperature=0.2)
                            p_data = json.loads(re.search(r'\{.*\}', p_res.choices[0].message.content, re.DOTALL).group(0))
                            
                            with st.expander(f"⚽ {t1} vs {t2} - {p_data.get('pick')} ({p_data.get('confidence')}%)"):
                                st.write(f"**Edge:** {p_data.get('edge_reason')}")
                                st.info(f"📊 {ctx}")
                                
                            payload = {"date": today_date, "time": m.get('time', ''), "league": m.get('league', ''), "team1": t1, "team2": t2, "odds": m.get('odds_1', ''), "m1": m.get('odds_x', ''), "m2": m.get('odds_2', ''), "Pick": p_data.get('pick'), "Confidence": str(p_data.get('confidence'))}
                            requests.post(GOOGLE_SHEET_URL, json=payload)
                except Exception as e:
                    st.error(f"Error: {e}")

            # MODE B: AUDIT BOOKING
            elif "Audit My Booking" in app_mode:
                slip_prompt = f"Extract selections from this booking text: {raw_text}. Return JSON array with keys: 'match', 'user_pick'. ONLY JSON."
                try:
                    res = client.chat.completions.create(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": slip_prompt}], temperature=0.1)
                    match = re.search(r'\[.*\]', res.choices[0].message.content, re.DOTALL)
                    
                    if match:
                        slips = json.loads(match.group(0))
                        st.success(f"✅ Found {len(slips)} legs. Auditing...")
                        
                        today_date = datetime.now().strftime("%Y-%m-%d")
                        total_confidence = 0
                        
                        for s in slips:
                            match_str = s.get('match', '')
                            t1, t2 = (match_str.split(' vs ')[0], match_str.split(' vs ')[1]) if ' vs ' in match_str else (match_str, "")
                            
                            st1 = fetch_team_stats(t1, API_FOOTBALL_KEY)
                            st2 = fetch_team_stats(t2, API_FOOTBALL_KEY) if t2 else None
                            time.sleep(0.5)
                            
                            ctx = ""
                            if st1: ctx += f"{t1}: Scored {st1['avg_scored']}/game. "
                            if st2: ctx += f"{t2}: Scored {st2['avg_scored']}/game. "
                            
                            audit_prompt = f"""
                            Review this user's bet: {s.get('user_pick')} for {match_str}.
                            Stats: {ctx}
                            Is this a safe bet, risky, or a trap? Give confidence (1-100).
                            Return JSON: "verdict" (Safe/Risky/Trap), "confidence", "reason".
                            """
                            
                            a_res = client.chat.completions.create(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": audit_prompt}], temperature=0.1)
                            a_data = json.loads(re.search(r'\{.*\}', a_res.choices[0].message.content, re.DOTALL).group(0))
                            
                            conf = int(float(a_data.get('confidence', 0)))
                            total_confidence += conf
                            
                            with st.expander(f"📝 {match_str} - Pick: {s.get('user_pick')}"):
                                st.write(f"**Verdict:** {a_data.get('verdict')} | **Confidence:** {conf}%")
                                st.write(f"**Reasoning:** {a_data.get('reason')}")
                                st.info(f"📊 {ctx}")
                                
                            payload = {"date": today_date, "time": "SLIP", "league": "AUDIT", "team1": t1, "team2": t2, "odds": "", "m1": "", "m2": "", "Pick": s.get('user_pick'), "Confidence": str(conf)}
                            requests.post(GOOGLE_SHEET_URL, json=payload)
                        
                        if slips:
                            avg_conf = total_confidence / len(slips)
                            st.write("---")
                            st.subheader("⚖️ Overall Booking Verdict")
                            if avg_conf >= 70:
                                st.success(f"🌟 **APPROVE** (Avg: {avg_conf:.1f}%) - Solid slip.")
                            elif avg_conf >= 50:
                                st.warning(f"⚠️ **CAUTION** (Avg: {avg_conf:.1f}%) - Some legs are risky.")
                            else:
                                st.error(f"❌ **REJECT** (Avg: {avg_conf:.1f}%) - High trap potential.")
                except Exception as e:
                    st.error(f"Audit Error: {e}")

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
                col4.metric("Total Profit", f"{total_profit:.2f}")
                
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
                                try: profit = float(row['Odds']) - 1.0
                                except: profit = 0.0
                                sheet.update_cell(index + 2, 11, "Win")
                                sheet.update_cell(index + 2, 12, profit)
                                st.rerun()
                            if btn_col2.button("❌ Loss", key=f"loss_{index}"):
                                sheet.update_cell(index + 2, 11, "Loss")
                                sheet.update_cell(index + 2, 12, -1.0)
                                st.rerun()
                                
                st.write("---")
                st.subheader("📜 Full History")
                st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.error(f"Error: {e}")
