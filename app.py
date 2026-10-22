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
from datetime import datetime

# ==========================================================
# PAGE + SECRETS
# ==========================================================
st.set_page_config(page_title="AI Sharp Bettor", page_icon="📈", layout="wide")
st.title("📈 AI Sharp Bettor & Auditor")

try:
    GOOGLE_SHEET_URL = st.secrets["GOOGLE_SHEET_URL"]
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
    API_FOOTBALL_KEY = st.secrets["API_FOOTBALL_KEY"]
except KeyError:
    st.error("⚠️ Missing Secrets! Please check your Streamlit Secrets configuration.")
    st.stop()

MODEL = "openai/gpt-oss-120b"
LEAGUE_AVG_GOALS = 1.35   # per team per match
MIN_MATCHES = 5           # minimum recent games per team

# ==========================================================
# MATHEMATICAL & API HELPERS
# ==========================================================
def safe_int(val):
    try:
        val = str(val).strip().lower()
        if 'high' in val: return 80
        if 'medium' in val: return 50
        if 'low' in val: return 20
        return int(float(val))
    except:
        return 0

def safe_float(val):
    try:
        # Removes any weird OCR artifacts like '1.50*' or spaces
        clean_val = re.sub(r'[^0-9.]', '', str(val))
        return float(clean_val)
    except:
        return 0.0

def extract_json(text, is_array=True):
    try:
        pattern = r'\[.*\]' if is_array else r'\{.*\}'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return [] if is_array else {}
    except Exception:
        return [] if is_array else {}

def poisson_probability(k, lam):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def calculate_poisson_probs(home_attack, home_defense, away_attack, away_defense):
    """Calculates exact probabilities for Over 1.5, 2.5, and 3.5 goals."""
    home_exp = (home_attack + away_defense) / 2
    away_exp = (away_attack + home_defense) / 2
    
    # Calculate exact goal probabilities (0 to 5 goals for each team)
    probs = {}
    for i in range(6):
        probs[f"h{i}"] = poisson_probability(i, home_exp)
        probs[f"a{i}"] = poisson_probability(i, away_exp)
    
    # Total match goal probabilities
    total_probs = {}
    for total_goals in range(6):
        prob = 0
        for h in range(total_goals + 1):
            a = total_goals - h
            prob += probs[f"h{h}"] * probs[f"a{a}"]
        total_probs[total_goals] = prob
        
    # Probability of Under X.5 is the sum of 0, 1, 2... goals
    under_1_5 = total_probs[0] + total_probs[1]
    under_2_5 = under_1_5 + total_probs[2]
    under_3_5 = under_2_5 + total_probs[3]
    
    return {
        "over_1_5": round((1 - under_1_5) * 100, 1),
        "over_2_5": round((1 - under_2_5) * 100, 1),
        "over_3_5": round((1 - under_3_5) * 100, 1)
    }

def fetch_team_stats(team_name, api_key):
    if not team_name or not api_key: return None
    headers = {"x-apisports-key": api_key}
    try:
        search_url = f"https://v3.football.api-sports.io/teams?search={team_name}"
        search_res = requests.get(search_url, headers=headers, timeout=10).json()
        
        if not search_res.get('response'): return None
        
        api_teams = [t['team']['name'] for t in search_res['response']]
        best_match = difflib.get_close_matches(team_name, api_teams, n=1, cutoff=0.6)
        
        if not best_match: return None
        team_id = next(t['team']['id'] for t in search_res['response'] if t['team']['name'] == best_match[0])
        
        fixtures_url = f"https://v3.football.api-sports.io/fixtures?team={team_id}&last=10"
        fixtures_res = requests.get(fixtures_url, headers=headers, timeout=10).json()
        
        if not fixtures_res.get('response'): return None
            
        goals_scored, goals_conceded = 0, 0
        matches_played = len(fixtures_res['response'])
        
        if matches_played < MIN_MATCHES:
            return {"error": f"Only {matches_played} matches found. Need {MIN_MATCHES}."}
        
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
                
        weight = matches_played / (matches_played + 2)
        raw_attack = goals_scored / matches_played
        raw_defense = goals_conceded / matches_played
        
        adj_attack = (raw_attack * weight) + (LEAGUE_AVG_GOALS * (1 - weight))
        adj_defense = (raw_defense * weight) + (LEAGUE_AVG_GOALS * (1 - weight))
        
        return {
            "attack": round(adj_attack, 2),
            "defense": round(adj_defense, 2),
            "matches_played": matches_played,
            "name": best_match[0]
        }
    except Exception as e:
        return None

# ==========================================================
# UI & PROCESSING
# ==========================================================
tab1, tab2 = st.tabs(["📸 Scanner & Auditor", "📊 Tracker & Performance"])

with tab1:
    st.subheader("1. Choose Input Method")
    input_method = st.radio("How do you want to analyze?", ["Upload Screenshot", "Paste Text"])
    
    raw_text = ""
    
    if input_method == "Upload Screenshot":
        uploaded_file = st.file_uploader("Upload a screenshot", type=["png", "jpg", "jpeg"])
        if uploaded_file is not None:
            img = Image.open(uploaded_file)
            st.image(img, caption="Uploaded Screenshot", use_column_width=True)
            with st.spinner("👁️ Scanning image..."):
                try:
                    ocr = RapidOCR()
                    temp_path = "temp_screenshot.png"
                    img.save(temp_path)
                    result, _ = ocr(temp_path)
                    if result:
                        raw_text = "\n".join([line[1] for line in result])
                    else:
                        st.warning("⚠️ No text found in image.")
                except Exception as e:
                    st.error(f"OCR Error: {e}")
    else:
        raw_text = st.text_area("Paste your booking details here:", height=200)

    if raw_text:
        st.write("---")
        app_mode = st.radio("Select Strategy:", ["🎯 Find +EV Value", "🕵️ Audit My Booking"])
        
        if st.button("🚀 Run Analysis"):
            client = Groq(api_key=GROQ_API_KEY)
            
            # MODE A: FIND +EV VALUE (The Quant Engine)
            if "Find +EV Value" in app_mode:
                extract_prompt = f"Extract matches from: {raw_text}. Return JSON array with: time, league, team1, team2, odds_1, odds_x, odds_2. ONLY JSON."
                try:
                    res = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": extract_prompt}], temperature=0.1)
                    matches = extract_json(res.choices[0].message.content, is_array=True)
                    
                    if not matches:
                        st.error("❌ Could not find any valid matches.")
                    else:
                        st.success(f"✅ Found {len(matches)} matches. Calculating Quantitative True Value...")
                        today_date = datetime.now().strftime("%Y-%m-%d")
                        
                        for m in matches:
                            t1_raw, t2_raw = m.get('team1', ''), m.get('team2', '')
                            s1 = fetch_team_stats(t1_raw, API_FOOTBALL_KEY)
                            s2 = fetch_team_stats(t2_raw, API_FOOTBALL_KEY)
                            time.sleep(0.5) 
                            
                            ctx = ""
                            math_data = {}
                            if s1 and s2 and "error" not in s1 and "error" not in s2:
                                math_data = calculate_poisson_probs(s1['attack'], s1['defense'], s2['attack'], s2['defense'])
                                ctx += f"{s1['name']} (Attack: {s1['attack']}, Def: {s1['defense']}). "
                                ctx += f"{s2['name']} (Attack: {s2['attack']}, Def: {s2['defense']}). "
                                ctx += f"True Math Probabilities: Over 1.5 ({math_data['over_1_5']}%), Over 2.5 ({math_data['over_2_5']}%), Over 3.5 ({math_data['over_3_5']}%). "
                            else:
                                ctx = "Not enough mathematical data to calculate Poisson. Rely on odds."

                            # Safe Float Extraction
                            o1 = safe_float(m.get('odds_1', 0))
                            ox = safe_float(m.get('odds_x', 0))
                            o2 = safe_float(m.get('odds_2', 0))
                            
                            imp_prob_1 = round((1/o1)*100, 1) if o1 > 1.01 else 0
                            imp_prob_2 = round((1/o2)*100, 1) if o2 > 1.01 else 0

                            # The Quant Prompt
                            pred_prompt = f"""
                            You are a ruthless sharp bettor. You bet with math, not emotion.
                            Match: {t1_raw} vs {t2_raw}. 
                            Odds: Home {o1} (Implied: {imp_prob_1}%), Draw {ox}, Away {o2} (Implied: {imp_prob_2}%).
                            Stats & Math Context: {ctx}
                            
                            Rules:
                            1. Focus entirely on 'Over Goals' markets (Over 1.5, 2.5, or 3.5). 
                            2. Compare the True Math Probability to the Implied Probability of the odds. 
                            3. If the True Math Probability for a line is AT LEAST 10% HIGHER than the Implied Probability, you MUST pick that 'Over' market.
                            4. If the odds are extremely low (below 1.30) and there is no mathematical edge, write 'TRAP - AVOID'.
                            5. If there is no math data, use your best judgment based on the 1X2 odds.
                            
                            Return JSON with keys: "pick", "confidence" (INTEGER 1-100 ONLY), "edge_reason" (Explain the Math vs Odds).
                            """
                            p_res = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": pred_prompt}], temperature=0.2)
                            p_data = extract_json(p_res.choices[0].message.content, is_array=False)
                            
                            pick_val = p_data.get('pick', 'No Value')
                            conf_val = safe_int(p_data.get('confidence', 0))
                            
                            risk_label = "HIGH RISK / TRAP" if "TRAP" in pick_val.upper() else ("LOW" if conf_val >= 75 else "MED" if conf_val >= 50 else "HIGH")
                            
                            with st.expander(f"⚽ {t1_raw} vs {t2_raw} - {pick_val} ({conf_val}%) - {risk_label}"):
                                st.write(f"**Edge:** {p_data.get('edge_reason', 'N/A')}")
                                st.info(f"📊 **Stats Context:** {ctx}")
                                
                            payload = {"date": today_date, "time": m.get('time', ''), "league": m.get('league', ''), "team1": t1_raw, "team2": t2_raw, "odds": str(o1), "m1": str(ox), "m2": str(o2), "Pick": pick_val, "Confidence": str(conf_val)}
                            requests.post(GOOGLE_SHEET_URL, json=payload)
                except Exception as e:
                    st.error(f"AI Error: {e}")

            # MODE B: AUDIT BOOKING
            elif "Audit My Booking" in app_mode:
                slip_prompt = f"""
                Extract the football selections from this text: {raw_text}
                CRITICAL RULE: SportyBet lists the market ABOVE the team names. Do NOT default to 'Home'.
                Do NOT output '1X2'. Look for 'Over', 'Under', 'Home', 'Away'.
                Return a JSON array with keys: "match", "user_pick". ONLY JSON.
                """
                try:
                    res = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": slip_prompt}], temperature=0.1)
                    slips = extract_json(res.choices[0].message.content, is_array=True)
                    
                    if len(slips) == 0:
                        st.error("❌ Invalid Image. Please load the code in SportyBet and screenshot the ACTUAL MATCHES.")
                    else:
                        st.success(f"✅ Found {len(slips)} legs. Auditing...")
                        today_date = datetime.now().strftime("%Y-%m-%d")
                        total_confidence = 0
                        
                        for s in slips:
                            match_str = s.get('match', '')
                            user_pick = s.get('user_pick', '')
                            
                            t1, t2 = (match_str.split(' vs ')[0], match_str.split(' vs ')[1]) if ' vs ' in match_str else (match_str, "")
                            
                            st1 = fetch_team_stats(t1, API_FOOTBALL_KEY)
                            st2 = fetch_team_stats(t2, API_FOOTBALL_KEY) if t2 else None
                            time.sleep(0.5)
                            
                            ctx = ""
                            if st1 and "error" not in st1: ctx += f"{t1}: Attack {st1['attack']}, Def {st1['defense']}. "
                            if st2 and "error" not in st2: ctx += f"{t2}: Attack {st2['attack']}, Def {st2['defense']}. "
                            if not ctx: ctx = "No stats available."
                            
                            audit_prompt = f"Review bet: {user_pick} for {match_str}. Stats: {ctx}. Is this safe, risky, or trap? Return JSON: 'verdict', 'confidence' (INTEGER 1-100), 'reason'."
                            a_res = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": audit_prompt}], temperature=0.1)
                            a_data = extract_json(a_res.choices[0].message.content, is_array=False)
                            
                            conf = safe_int(a_data.get('confidence', 0))
                            total_confidence += conf
                            
                            with st.expander(f"📝 {match_str} - Pick: {user_pick}"):
                                st.write(f"**Verdict:** {a_data.get('verdict', 'N/A')} | **Confidence:** {conf}%")
                                st.write(f"**Reasoning:** {a_data.get('reason', 'N/A')}")
                                st.info(f"📊 {ctx}")
                                
                            payload = {"date": today_date, "time": "SLIP", "league": "AUDIT", "team1": t1, "team2": t2, "odds": "", "m1": "", "m2": "", "Pick": user_pick, "Confidence": str(conf)}
                            requests.post(GOOGLE_SHEET_URL, json=payload)
                        
                        if slips:
                            avg_conf = total_confidence / len(slips)
                            st.write("---")
                            st.subheader("⚖️ Overall Booking Verdict")
                            if avg_conf >= 70: st.success(f"🌟 **APPROVE** (Avg: {avg_conf:.1f}%)")
                            elif avg_conf >= 50: st.warning(f"⚠️ **CAUTION** (Avg: {avg_conf:.1f}%)")
                            else: st.error(f"❌ **REJECT** (Avg: {avg_conf:.1f}%) - High trap potential.")
                except Exception as e:
                    st.error(f"Audit Error: {e}")

# ==========================================================
# TRACKER TAB
# ==========================================================
with tab2:
    st.header("📊 Betting Tracker")
    try:
        response = requests.get(GOOGLE_SHEET_URL)
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 1:
                headers = data[0]
                rows = data[1:]
                df = pd.DataFrame(rows, columns=headers)
                
                if 'Result' not in df.columns: df['Result'] = ''
                if 'Profit' not in df.columns: df['Profit'] = 0.0
                
                df['Profit'] = pd.to_numeric(df['Profit'], errors='coerce').fillna(0)
                df['Odds'] = pd.to_numeric(df['Odds'], errors='coerce').fillna(0)
                
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
                                requests.post(GOOGLE_SHEET_URL, json={"action": "u", "rowNum": str(index + 2), "result": "Win", "profit": profit})
                                st.rerun()
                            if btn_col2.button("❌ Loss", key=f"loss_{index}"):
                                requests.post(GOOGLE_SHEET_URL, json={"action": "u", "rowNum": str(index + 2), "result": "Loss", "profit": -1.0})
                                st.rerun()
                                
                st.write("---")
                st.subheader("📜 Full History")
                st.dataframe(df, use_container_width=True)
            else:
                st.info("No data yet. Scan a screenshot to get started.")
        else:
            st.error(f"Failed to fetch data. Status: {response.status_code}")
    except Exception as e:
        st.error(f"Error loading tracker: {e}")
