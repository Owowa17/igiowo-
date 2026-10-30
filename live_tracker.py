import os
import requests
import json
from datetime import datetime, timezone

# --- CONFIGURATION ---
FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# We only want to run this during typical football hours (e.g., 12:00 PM to 10:00 PM WAT)
# WAT is UTC+1. So 11:00 UTC to 21:00 UTC.
def is_game_hour():
    current_hour_utc = datetime.now(timezone.utc).hour
    return 11 <= current_hour_utc <= 21

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
        print("📤 Live Alert Sent!")
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

def get_live_matches():
    """
    Fetches live matches. 
    ⚠️ YOU MUST CHANGE THIS URL TO YOUR API'S LIVE ENDPOINT.
    """
    # Example placeholder URL - You need to check your API documentation for the live endpoint
    url = "https://footballcharts-backend.onrender.com/api/v1/live/" 
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        print(f"❌ API Error: {e}")
        return []

def check_live_value(match):
    """
    Logic to determine if a live match is a high-value Under 2.5 bet.
    """
    # 1. Get Match Details (Adjust these keys based on your API)
    match_name = f"{match.get('home_team')} vs {match.get('away_team')}"
    minute = int(match.get("minute", 0))
    home_score = int(match.get("home_score", 0))
    away_score = int(match.get("away_score", 0))
    total_goals = home_score + away_score
    
    # 2. Get Pre-match Probability
    model_preds = match.get("model_predictions", {})
    dc_v2 = model_preds.get("dc_v2", {})
    preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
    
    ft_over = preds.get("ft_over_2.5")
    if not ft_over:
        return None
        
    ft_under_prob = (1.0 - float(ft_over)) * 100
    
    # 3. The Live Logic
    # We only care if the pre-match prediction was decent (e.g., > 65%)
    if ft_under_prob >= 65:
        
        # Scenario A: 0-0 at halftime or later (Very high value)
        if total_goals == 0 and minute >= 45:
            return {
                "match": match_name,
                "minute": minute,
                "score": f"{home_score}-{away_score}",
                "reason": "0-0 after HT. Under 2.5 is highly likely.",
                "original_prob": round(ft_under_prob, 1)
            }
            
        # Scenario B: 1-0 or 0-1 at 70th minute or later
        if total_goals == 1 and minute >= 70:
            return {
                "match": match_name,
                "minute": minute,
                "score": f"{home_score}-{away_score}",
                "reason": "Only 1 goal by 70th min. Under 2.5 is highly likely.",
                "original_prob": round(ft_under_prob, 1)
            }
            
    return None

def run_live_tracker():
    if not is_game_hour():
        print("💤 Outside game hours. Sleeping.")
        return

    print("⚡ Scanning Live Matches...")
    matches = get_live_matches()
    
    if not matches:
        print("No live matches found.")
        return

    alerts_sent = 0
    
    for match in matches:
        value_alert = check_live_value(match)
        
        if value_alert:
            message = (
                f"🚨 <b>LIVE VALUE ALERT</b> 🚨\n\n"
                f"⚽ <b>{value_alert['match']}</b>\n"
                f"⏱ Minute: <b>{value_alert['minute']}'</b>\n"
                f"📊 Score: <b>{value_alert['score']}</b>\n\n"
                f"🎯 Market: <b>LIVE Under 2.5 Goals</b>\n"
                f"💡 Reason: {value_alert['reason']}\n"
                f"📈 Pre-match AI Prob: {value_alert['original_prob']}%\n\n"
                f"🔥 <b>Place your live bet now:</b>\n"
                f"👉 <a href='https://1win.ng/?p=q70f'>Click here for 1win Live Betting</a>\n"
                f"🎁 <b>Promo Code: <code>IGIOWO</code></b>"
            )
            send_telegram_message(message)
            alerts_sent += 1
            
    print(f"✅ Live scan complete. Sent {alerts_sent} alerts.")

if __name__ == "__main__":
    run_live_tracker()
