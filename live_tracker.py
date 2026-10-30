import os
import requests
import json
from datetime import datetime, timezone

# --- CONFIGURATION ---
FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ALERTS_FILE = "live_alerts.json" # Keeps track of alerts to prevent spam

LEAGUES_TO_SCAN = [
    "premier", "germany2", "turkey2", "laliga", "seriea", "ligue1", "championship", "eredivisie"
]

def is_game_hour():
    """Only run between 11:00 UTC and 21:00 UTC (12 PM - 10 PM WAT)"""
    current_hour_utc = datetime.now(timezone.utc).hour
    return 11 <= current_hour_utc <= 21

def load_sent_alerts():
    """Loads previously sent alerts to prevent spamming the same match."""
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent_alert(match_name, minute):
    """Saves a match to the alert history."""
    alerts = list(load_sent_alerts())
    alerts.append(f"{match_name}_{minute}")
    # Keep only the last 100 alerts to keep the file small
    alerts = alerts[-100:]
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=4)

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
        print("📤 Live Alert Sent!")
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

def get_live_matches(league_key):
    """
    Fetches live matches for a specific league.
    We use the fixtures endpoint and filter for live status.
    """
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data.get("matches", [])
    except Exception as e:
        print(f"❌ API Error for {league_key}: {e}")
        return []

def check_live_value(match, league_key):
    """
    Logic to determine if a live match is a high-value Under 2.5 bet.
    """
    # 1. Safely extract data (API keys might be different)
    match_name = f"{match.get('home_team', 'Unknown')} vs {match.get('away_team', 'Unknown')}"
    
    # Check status (Must be live/in-progress)
    status = str(match.get("status", "")).lower()
    if status not in ["live", "in_play", "1h", "2h", "ht"]:
        return None
        
    # Extract minute and scores safely
    try:
        minute = int(match.get("minute", match.get("time_elapsed", 0)))
        home_score = int(match.get("home_score", match.get("score", {}).get("home", 0)))
        away_score = int(match.get("away_score", match.get("score", {}).get("away", 0)))
    except (ValueError, TypeError):
        return None
        
    total_goals = home_score + away_score
    
    # 2. Get Pre-match Probability
    model_preds = match.get("model_predictions", {})
    dc_v2 = model_preds.get("dc_v2", {})
    preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
    
    ft_over = preds.get("ft_over_2.5")
    if not ft_over:
        return None
        
    try:
        ft_under_prob = (1.0 - float(ft_over)) * 100
    except (ValueError, TypeError):
        return None
    
    # 3. The Live Logic
    if ft_under_prob >= 65:
        
        # Scenario A: 0-0 at halftime or later
        if total_goals == 0 and minute >= 45:
            return {
                "match": match_name,
                "league": league_key.capitalize(),
                "minute": minute,
                "score": f"{home_score}-{away_score}",
                "reason": "0-0 after HT. Under 2.5 is highly likely.",
                "original_prob": round(ft_under_prob, 1)
            }
            
        # Scenario B: 1-0 or 0-1 at 70th minute or later
        if total_goals == 1 and minute >= 70:
            return {
                "match": match_name,
                "league": league_key.capitalize(),
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
    
    sent_alerts = load_sent_alerts()
    alerts_sent_now = 0
    
    # Loop through all 8 leagues
    for league in LEAGUES_TO_SCAN:
        matches = get_live_matches(league)
        
        for match in matches:
            value_alert = check_live_value(match, league)
            
            if value_alert:
                # Check if we already sent an alert for this match in the last few minutes
                alert_id = f"{value_alert['match']}_{value_alert['minute']}"
                
                if alert_id not in sent_alerts:
                    message = (
                        f"🚨 <b>LIVE VALUE ALERT</b> 🚨\n\n"
                        f"⚽ <b>{value_alert['match']}</b> ({value_alert['league']})\n"
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
                    save_sent_alert(value_alert['match'], value_alert['minute'])
                    alerts_sent_now += 1
                    
    print(f"✅ Live scan complete. Sent {alerts_sent_now} new alerts.")

if __name__ == "__main__":
    run_live_tracker()
