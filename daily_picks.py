import os
import requests
import time
import json
from datetime import datetime, timezone, timedelta

# --- CONFIGURATION ---
FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Expanded to 8 Leagues
LEAGUES_TO_SCAN = [
    "premier", "germany2", "turkey2", "laliga", "seriea", "ligue1", "championship", "eredivisie"
]
HISTORY_FILE = "picks_history.json"

def get_fixtures(league_key):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    print(f"🔄 Fetching API for {league_key}...")
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=20) 
            r.raise_for_status()
            data = r.json()
            matches = data.get("matches", [])
            
            if not matches:
                return []
                
            if matches:
                computed_at_str = matches[0].get("computed_at")
                if computed_at_str:
                    computed_date = computed_at_str.split("T")[0]
                    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if computed_date != today_utc:
                        print(f"⚠️ {league_key}: Data is STALE (Computed: {computed_date})")
                        return None 
            print(f"✅ {league_key}: Fetched {len(matches)} matches.")
            return matches 
        except Exception as e:
            print(f"❌ Attempt {attempt+1} failed: {e}")
            time.sleep(3) 
    return None 

def filter_picks(matches):
    ht_candidates, ft_candidates, dc_candidates = [], [], []
    
    for match in matches:
        # --- FIX 1: Sanitize the Date ---
        raw_date = match.get("date") or match.get("match_date") or match.get("start_time") or ""
        try:
            # Try to format it properly. If API says 2026, we fix it to today's year
            parsed_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d")
            if parsed_date.year > datetime.now().year + 1:
                match_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            else:
                match_date = parsed_date.strftime("%Y-%m-%d")
        except:
            match_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        model_preds = match.get("model_predictions") or {}
        dc_v2 = model_preds.get("dc_v2") or {}
        preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
        
        match_name = f"{match.get('home_team', 'Unknown')} vs {match.get('away_team', 'Unknown')}"
        match_time = match.get("time", "N/A")
        
        ht_over = preds.get("ht_over_1.5")
        if ht_over:
            try:
                ht_under = (1.0 - float(ht_over)) * 100
                if ht_under >= 70:
                    ht_candidates.append({"match": match_name, "time": match_time, "prob": round(ht_under, 1), "date": match_date, "market": "HT Under 1.5"})
            except: pass
                
        ft_over = preds.get("ft_over_2.5")
        if ft_over:
            try:
                ft_under = (1.0 - float(ft_over)) * 100
                if ft_under >= 65: 
                    ft_candidates.append({"match": match_name, "time": match_time, "prob": round(ft_under, 1), "date": match_date, "market": "FT Under 2.5"})
            except: pass

        dc_1x = preds.get("dc_1x") or preds.get("1x")
        dc_x2 = preds.get("dc_x2") or preds.get("x2")
        if not dc_1x and not dc_x2:
            try:
                p_home, p_draw, p_away = preds.get("ft_home_win"), preds.get("ft_draw"), preds.get("ft_away_win")
                if p_home and p_draw: dc_1x = float(p_home) + float(p_draw)
                if p_away and p_draw: dc_x2 = float(p_away) + float(p_draw)
            except: pass
                
        if dc_1x:
            try:
                dc_1x_prob = float(dc_1x) * 100 if float(dc_1x) <= 1.0 else float(dc_1x)
                if dc_1x_prob >= 80:
                    dc_candidates.append({"match": match_name, "time": match_time, "prob": round(dc_1x_prob, 1), "date": match_date, "market": "Double Chance (1X)"})
            except: pass
                
        if dc_x2:
            try:
                dc_x2_prob = float(dc_x2) * 100 if float(dc_x2) <= 1.0 else float(dc_x2)
                if dc_x2_prob >= 80:
                    dc_candidates.append({"match": match_name, "time": match_time, "prob": round(dc_x2_prob, 1), "date": match_date, "market": "Double Chance (X2)"})
            except: pass
                
    return ht_candidates, ft_candidates, dc_candidates

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if not response.json().get("ok"): return False
        return True
    except: return False

def generate_picks():
    print("🤖 Starting daily AI scan...")
    all_ht, all_ft, all_dc = [], [], []
    api_failed = False
    today_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    # 1. Load History
    picked_matches = set()
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history_data = json.load(f)
                cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
                history = [item for item in history_data if item.get("date", "1970-01-01") >= cutoff]
                for item in history:
                    picked_matches.add(item.get("match"))
        except Exception as e: print(f"⚠️ History error: {e}")
    
    # 2. Scan Leagues
    for league in LEAGUES_TO_SCAN:
        matches = get_fixtures(league)
        time.sleep(1) # API Protection
        if matches is None:
            api_failed = True
            continue
        if not matches: continue
            
        ht, ft, dc = filter_picks(matches)
        
        # Filter out history
        ht = [p for p in ht if p["match"] not in picked_matches]
        ft = [p for p in ft if p["match"] not in picked_matches]
        dc = [p for p in dc if p["match"] not in picked_matches]
        
        for p in ht: p["league"] = league.capitalize()
        for p in ft: p["league"] = league.capitalize()
        for p in dc: p["league"] = league.capitalize()
            
        all_ht.extend(ht); all_ft.extend(ft); all_dc.extend(dc)
    
    # 3. Sort
    all_ht.sort(key=lambda x: (x["date"], -x["prob"]))
    all_ft.sort(key=lambda x: (x["date"], -x["prob"]))
    all_dc.sort(key=lambda x: (x["date"], -x["prob"]))
    
    dates = [p["date"] for p in all_ht + all_ft + all_dc]
    target_date = min(dates) if dates else None
        
    if target_date and (all_ht or all_ft or all_dc):
        top_ht = [p for p in all_ht if p["date"] == target_date][:3]
        top_ft = [p for p in all_ft if p["date"] == target_date][:3]
        top_dc = [p for p in all_dc if p["date"] == target_date][:3]
        
        header_title = f"🎯 <b>Daily AI Picks - {today_date}</b>" if target_date == today_date else f"🎯 <b>Early AI Picks for {target_date}</b>"
        message = f"{header_title}\n\n"
        
        # --- FIX 2: Dynamic Formatting (No more "Top 3" if there is only 1) ---
        if top_ht:
            title = "Top Pick" if len(top_ht) == 1 else f"Top {len(top_ht)}"
            message += f"⚡ <b>{title} HT Under 1.5</b> ⚡\n"
            for p in top_ht:
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | <b>{p['prob']}%</b>\n\n"
                
        if top_ft:
            title = "Top Pick" if len(top_ft) == 1 else f"Top {len(top_ft)}"
            message += f"🛡️ <b>{title} FT Under 2.5</b> 🛡️\n"
            for p in top_ft:
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | <b>{p['prob']}%</b>\n\n"

        if top_dc:
            title = "Top Pick" if len(top_dc) == 1 else f"Top {len(top_dc)}"
            message += f"💎 <b>{title} Double Chance (Safest)</b> 💎\n"
            for p in top_dc:
                market_type = p['market'].replace("Double Chance ", "")
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | <b>{market_type} @ {p['prob']}%</b>\n\n"

        message += (
            "🔥 <b>Ready to place your bets?</b>\n"
            "Join 1win using our exclusive code to get the best odds!\n\n"
            "👉 <a href='https://1win.ng/?p=q70f'>Click here to Register on 1win</a>\n"
            "🎁 <b>Promo Code: <code>IGIOWO</code></b>\n"
        )
        
        print("📤 Sending picks to Telegram...")
        if send_telegram_message(message):
            history.extend(top_ht); history.extend(top_ft); history.extend(top_dc)
            with open(HISTORY_FILE, "w") as f: json.dump(history, f, indent=4)
            print("💾 History saved.")
            
    else:
        # --- FIX 3: Silencing the confusing "No Matches" message ---
        # If the bot runs a 2nd time and finds nothing new, it stays quiet.
        # It only sends a message if the API completely failed or it's a genuine rest day.
        if api_failed:
            print("❌ Skipping Telegram. API failed.")
        else:
            print("⚠️ No NEW matches met the criteria. (Staying quiet to avoid spamming Telegram).")
            # Uncomment the block below ONLY if you want a message sent when there are NO picks at all today
            """
            rest_message = (
                "🧠 <b>DAILY AI UPDATE</b> 🧠\n\n"
                "⚠️ <b>No High-Value Picks Found.</b>\n\n"
                "Our AI scanned the leagues and found no matches meeting our strict criteria today.\n\n"
                "💎 <b>Protecting your bankroll:</b> We never force bad bets.\n"
            )
            send_telegram_message(rest_message)
            """

if __name__ == "__main__":
    generate_picks()
