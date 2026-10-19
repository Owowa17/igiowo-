import streamlit as st
import io
import json
import requests
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from groq import Groq
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Volatile Market Hunter", page_icon="🦈", layout="centered")
st.title("🦈 AI High-Value Market Hunter")
st.write("Upload a screenshot. The AI will hunt for weak markets, traps, and volatile high-odds opportunities.")

# --- CONFIGURATION ---
GOOGLE_SHEET_URL = st.secrets["GOOGLE_SHEET_URL"]
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]

# --- FILE UPLOADER ---
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
            st.warning("⚠️ No text found in the image. Please try a clearer screenshot.")
        else:
            extracted_lines = [line[1] for line in result]
            raw_ocr_text = "\n".join(extracted_lines)
            
            with st.spinner("🧠 Hunting for high-value volatility with Groq..."):
                client = Groq(api_key=GROQ_API_KEY)
                
                # --- THE ULTIMATE VOLATILE PROMPT ---
                prompt = f"""
                You are a ruthless, highly experienced sports betting analyst known as the "Volatile Market Hunter". Your job is to find high-value traps, weak markets, and explosive odds. 
                
                I have scanned a live/pre-match football betting screen. Here is the raw text:
                {raw_ocr_text}

                LEAGUE-SPECIFIC RULES:
                - If the league is 'Serie A', favor 'Under 2.5 Goals'.
                - If the league is 'Eredivisie', favor 'Over 3.5 Goals'.
                - If the league is in a low-tier or obscure division, look for 'Draw (High Value)' if the odds are completely unbalanced.

                HOW TO THINK (FEW-SHOT EXAMPLES):
                - Trap Spotting: If Team A is winning 2-0 at 80:00 but their odds to win are still 4.50, this is a massive trap. The bookie is begging you to bet on Team A. Pick 'Under 3.5 Goals' or 'Next Goal - Team 2'.
                - HT/FT Value: If it's 0-0 at Half Time, and the draw odds are > 3.00, pick 'Draw (High Value)'. If a heavy favorite is 0-0 at HT, pick 'Over 1.5 Goals (2nd Half)'.
                - Volatile Markets: If you see Correct Score, Half-Time Winner, or Quarter Winner odds in the text, you are allowed to pick them if the value is explosive (e.g., picking a Correct Score of 3-2 at odds of 25.00).

                YOUR TASK:
                Extract the matches and hunt for the BEST high-value picks. Be aggressive with high-risk markets if the odds justify it, but calculate your confidence based on the volatility.

                Return a JSON array. For each match, provide exactly these keys:
                - "time"
                - "league"
                - "team1"
                - "team2"
                - "odds_1"
                - "odds_x"
                - "odds_2"
                - "pick": Your specific high-value pick (e.g., 'Correct Score 2-1', 'Half-Time Draw', 'Under 3.5 Goals', 'Live Boost Value').
                - "confidence": An integer (1-100). Be honest. If it's a wild Correct Score pick, confidence should be low (e.g., 15%). If it's a trap Under 3.5, confidence should be high (e.g., 85%).

                Return ONLY valid JSON. Do not include markdown, backticks, or conversational text. Just the JSON array.
                """
                
                try:
                    response = client.chat.completions.create(
                        model="openai/gpt-oss-120b",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.2,
                    )
                    
                    content = response.choices[0].message.content
                    start = content.find('[')
                    end = content.rfind(']')
                    
                    if start != -1 and end != -1:
                        clean_json = content[start:end+1]
                        best_picks = json.loads(clean_json)
                        
                        st.success(f"🦈 AI Hunter found {len(best_picks)} volatile opportunities!")
                        
                        today_date = datetime.now().strftime("%Y-%m-%d")
                        
                        for pick in best_picks:
                            conf = int(pick.get('confidence', 0))
                            if conf >= 75:
                                conf_color = "green"
                                risk_level = "LOW RISK (High Confidence)"
                            elif conf >= 50:
                                conf_color = "orange"
                                risk_level = "MEDIUM RISK"
                            else:
                                conf_color = "red"
                                risk_level = "HIGH RISK / VOLATILE"

                            with st.expander(f"⚽ {pick.get('team1')} vs {pick.get('team2')} - {risk_level}"):
                                st.write(f"**League:** {pick.get('league')} | **Time:** {pick.get('time')}")
                                st.write(f"**1X2 Odds:** {pick.get('odds_1')} / {pick.get('odds_x')} / {pick.get('odds_2')}")
                                st.markdown(f"**Pick:** <span style='font-size:20px; color:blue; font-weight:bold;'>{pick.get('pick')}</span>", unsafe_allow_html=True)
                                st.markdown(f"**Confidence:** <span style='color:{conf_color}; font-weight:bold;'>{pick.get('confidence')}%</span>", unsafe_allow_html=True)
                                
                                payload = {
                                    "date": today_date,
                                    "time": pick.get('time'),
                                    "league": pick.get('league'),
                                    "team1": pick.get('team1'),
                                    "team2": pick.get('team2'),
                                    "odds": pick.get('odds_1'),
                                    "m1": pick.get('odds_x'),
                                    "m2": pick.get('odds_2'),
                                    "Pick": pick.get('pick'),
                                    "Confidence": pick.get('confidence')
                                }
                                
                                try:
                                    sheet_response = requests.post(GOOGLE_SHEET_URL, json=payload)
                                    if sheet_response.status_code == 200:
                                        st.write("✅ Saved to Google Sheets")
                                except Exception as e:
                                    pass
                    else:
                        st.error("❌ AI did not return valid JSON.")
                except Exception as e:
                    st.error(f"❌ AI Error: {e}")
