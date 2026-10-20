import streamlit as st
import io
import json
import re
import requests
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Volatile Market Hunter", page_icon="🦈", layout="wide")
st.title("🦈 AI High-Value Market Hunter")

# --- CONFIGURATION ---
GOOGLE_SHEET_URL = st.secrets["GOOGLE_SHEET_URL"]
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]

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
        
        with st.spinner("👁️ Scanning image with OCR..."):
            ocr = RapidOCR()
            temp_path = "temp_screenshot.png"
            img.save(temp_path)
            result, _ = ocr(temp_path)
            
            if not result:
                st.warning("⚠️ No text found.")
            else:
                extracted_lines = [line[1] for line in result]
                raw_ocr_text = "\n".join(extracted_lines)
                
                with st.spinner("🧠 Hunting for high-value volatility..."):
                    client = Groq(api_key=GROQ_API_KEY)
                    
                    prompt = f"""
                    You are a ruthless sports betting analyst. Hunt for high-value traps and explosive odds.
                    Raw text: {raw_ocr_text}
                    Return a JSON array with keys: "time", "league", "team1", "team2", "odds_1", "odds_x", "odds_2", "pick", "confidence".
                    Return ONLY valid JSON.
                    """
                    
                    try:
                        response = client.chat.completions.create(
                            model="openai/gpt-oss-120b",
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.2,
                        )
                        content = response.choices[0].message.content
                        match = re.search(r'\[.*\]', content, re.DOTALL)
                        
                        if match:
                            best_picks = json.loads(match.group(0))
                            st.success(f"🦈 Found {len(best_picks)} opportunities!")
                            
                            today_date = datetime.now().strftime("%Y-%m-%d")
                            
                            for pick in best_picks:
                                conf = int(pick.get('confidence', 0))
                                risk = "LOW" if conf >= 75 else "MED" if conf >= 50 else "HIGH"
                                
                                with st.expander(f"⚽ {pick.get('team1')} vs {pick.get('team2')} - {risk} RISK"):
                                    st.write(f"**Pick:** {pick.get('pick')} | **Confidence:** {pick.get('confidence')}%")
                                    
                                    # Send to Google Sheets
  payload = {
    "date": str(today_date),
    "time": str(pick.get('time', '')),
    "league": str(pick.get('league', '')),
    "team1": str(pick.get('team1', '')),
    "team2": str(pick.get('team2', '')),
    "odds": str(pick.get('odds_1', '')),
    "m1": str(pick.get('odds_x', '')),
    "m2": str(pick.get('odds_2', '')),
    "Pick": str(pick.get('pick', '')),
    "Confidence": str(pick.get('confidence', ''))
}
                        requests.post(GOOGLE_SHEET_URL, json=payload)
                        else:
                        st.error("❌ AI did not return JSON.")
                    except Exception as e:
                        st.error(f"❌ AI Error: {e}")

# ==========================================
# TAB 2: THE TRACKER & PERFORMANCE
# ==========================================
with tab2:
    st.header("📊 Betting Tracker")
    
    if sheet:
        try:
            # Fetch data and convert to DataFrame
            data = sheet.get_all_records()
            if data:
                df = pd.DataFrame(data)
                
                # Ensure Result and Profit columns exist
                if 'Result' not in df.columns:
                    df['Result'] = ''
                if 'Profit' not in df.columns:
                    df['Profit'] = 0.0

                # --- PERFORMANCE METRICS ---
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
                
                # --- SETTLE MATCHES ---
                st.subheader("⚖️ Settle Pending Matches")
                
                # Find rows where Result is empty
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
                            
                            # Buttons for Win/Loss
                            btn_col1, btn_col2 = cols[3].columns(2)
                            
                            if btn_col1.button("✅ Win", key=f"win_{index}"):
                                # Update Sheet
                                row_num = index + 2 # +2 because of 0-index and header row
                                profit = float(row['Odds']) - 1.0
                                sheet.update_cell(row_num, 11, "Win") # Column K
                                sheet.update_cell(row_num, 12, profit) # Column L
                                st.rerun()
                                
                            if btn_col2.button("❌ Loss", key=f"loss_{index}"):
                                row_num = index + 2
                                sheet.update_cell(row_num, 11, "Loss")
                                sheet.update_cell(row_num, 12, -1.0) # Assumes 1 unit stake
                                st.rerun()
                                
                st.write("---")
                st.subheader("📜 Full History")
                st.dataframe(df, use_container_width=True)
                
            else:
                st.info("No data yet. Scan a screenshot to get started.")
        except Exception as e:
            st.error(f"Error loading tracker: {e}")
    else:
        st.error("Could not connect to Google Sheets. Check secrets.")
