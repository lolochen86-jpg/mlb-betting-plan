
#!/usr/bin/env python3
"""
MLB 多模型自動下注回測系統 v1.0
模型：
  A - 畢氏勝率模型 (Pythagorean Expectation)
  B - 近期狀態模型 (Last-10 Rolling Form)
  C - ELO 評分模型 (ELO Rating System)
  D - 先發投手模型 (Starting Pitcher ERA)
  E - 對照組 (Dynamic Ensemble Calibrator) ← 根據其他模型校準

每日選出信心值最高的 4~5 場下注，-110 賠率（標準美式賠率）
"""

import json, os, math, time, random
from datetime import date, timedelta
from collections import defaultdict

try:
    import statsapi
except ImportError:
    os.system("pip install MLB-StatsAPI -q")
    import statsapi

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
SEASON_START   = date(2025, 3, 27)   # 2025 MLB 開幕日
TODAY          = date.today()
UNIT           = 100                  # 每注 $100
ODDS           = -110                 # 標準讓分賠率
BET_PER_DAY    = 5                    # 每天最多下注場數
MIN_CONFIDENCE = 0.55                 # 最低信心門檻
MIN_EDGE       = 0.03                 # 相對賠率最低優勢

OUTPUT_DIR = os.path.join(os.getcwd(), "files")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────
def payout(unit=UNIT, odds=ODDS):
    """計算美式賠率淨利"""
    if odds > 0:
        return unit * odds / 100
    else:
        return unit * 100 / abs(odds)

def implied_prob(odds=ODDS):
    """美式賠率轉隱含概率"""
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)

WIN_PAYOUT  = payout()
IMP_PROB    = implied_prob()         # ~0.5238 for -110

# ─────────────────────────────────────────────
# 資料層：從 MLB StatsAPI 取得比賽結果
# ─────────────────────────────────────────────
_game_cache = {}

def get_games_on_date(d: date):
    key = str(d)
    if key in _game_cache:
        return _game_cache[key]
    try:
        sched = statsapi.schedule(date=d.strftime("%Y-%m-%d"))
        _game_cache[key] = sched
        return sched
    except Exception:
        _game_cache[key] = []
        return []

def build_season_results(start: date, end: date):
    """
    建立整季比賽結果字典
    回傳: {date: [{"home": str, "away": str, "home_score": int, "away_score": int, ...}]}
    """
    print(f"📥 載入賽季資料 {start} → {end} ...")
    results = {}
    d = start
    while d <= end:
        games = get_games_on_date(d)
        day_results = []
        for g in games:
            if g.get("status") not in ("Final", "Game Over", "Completed Early"):
                d += timedelta(days=1)
                continue
            day_results.append({
                "game_id":    g.get("game_id"),
                "home":       g.get("home_name", ""),
                "away":       g.get("away_name", ""),
                "home_score": g.get("home_score", 0),
                "away_score": g.get("away_score", 0),
                "home_win":   g.get("home_score", 0) > g.get("away_score", 0),
            })
        if day_results:
            results[d] = day_results
        d += timedelta(days=1)
        time.sleep(0.05)   # rate limit
    print(f"✅ 共載入 {sum(len(v) for v in results.values())} 場比賽")
    return results

# ─────────────────────────────────────────────
# 滾動統計追蹤器
# ─────────────────────────────────────────────
class TeamStats:
    def __init__(self):
        self.season_rs = defaultdict(list)   # runs scored
        self.season_ra = defaultdict(list)   # runs allowed
        self.rolling10 = defaultdict(list)   # (rs, ra) last 10 games
        self.elo       = defaultdict(lambda: 1500.0)

    def update(self, home, away, home_score, away_score):
        self.season_rs[home].append(home_score)
        self.season_ra[home].append(away_score)
        self.season_rs[away].append(away_score)
        self.season_ra[away].append(home_score)

        self.rolling10[home].append((home_score, away_score))
        self.rolling10[away].append((away_score, home_score))
        if len(self.rolling10[home]) > 10:
            self.rolling10[home].pop(0)
        if len(self.rolling10[away]) > 10:
            self.rolling10[away].pop(0)

        # ELO update
        k = 20
        ea = 1 / (1 + 10 ** ((self.elo[away] - self.elo[home]) / 400))
        sa = 1 if home_score > away_score else 0
        self.elo[home] += k * (sa - ea)
        self.elo[away] += k * ((1 - sa) - (1 - ea))

    # ── Model A: Pythagorean ──
    def pythagorean(self, team, exp=1.83):
        rs = sum(self.season_rs[team]) or 1
        ra = sum(self.season_ra[team]) or 1
        return rs**exp / (rs**exp + ra**exp)

    # ── Model B: Last-10 Form ──
    def form_pct(self, team):
        games = self.rolling10[team]
        if not games:
            return 0.5
        wins = sum(1 for rs, ra in games if rs > ra)
        return wins / len(games)

    # ── Model C: ELO ──
    def elo_win_prob(self, home, away):
        return 1 / (1 + 10 ** ((self.elo[away] - self.elo[home]) / 400))

    def has_data(self, team):
        return len(self.season_rs[team]) >= 5

# ─────────────────────────────────────────────
# 預測模型
# ─────────────────────────────────────────────
class ModelA_Pythagorean:
    """畢氏勝率模型"""
    name = "A-畢氏勝率"
    history = []   # (predicted_correct: bool)

    def predict(self, home, away, stats: TeamStats):
        if not (stats.has_data(home) and stats.has_data(away)):
            return None
        ph = stats.pythagorean(home)
        pa = stats.pythagorean(away)
        prob = ph / (ph + pa)
        prob = max(0.40, min(0.70, prob + 0.02))   # home advantage
        return prob   # P(home wins)

class ModelB_RecentForm:
    """近期狀態模型（近10場）"""
    name = "B-近期狀態"
    history = []

    def predict(self, home, away, stats: TeamStats):
        fh = stats.form_pct(home)
        fa = stats.form_pct(away)
        total = fh + fa or 1
        prob = fh / total
        prob = max(0.38, min(0.72, prob + 0.025))
        return prob

class ModelC_ELO:
    """ELO 評分模型"""
    name = "C-ELO評分"
    history = []

    def predict(self, home, away, stats: TeamStats):
        prob = stats.elo_win_prob(home, away)
        prob = max(0.35, min(0.75, prob + 0.015))
        return prob

class ModelD_PitcherERA:
    """
    先發投手模型（簡化版：用當日失分率代理 ERA 差）
    實際使用時可接入 rotations API
    """
    name = "D-先發投手"
    history = []

    def predict(self, home, away, stats: TeamStats):
        if not (stats.has_data(home) and stats.has_data(away)):
            return None
        # 用最近5場平均失分當 ERA 代理值
        def avg_ra_5(team):
            games = stats.rolling10[team][-5:]
            if not games:
                return 4.5
            return sum(ra for _, ra in games) / len(games)
        era_h = avg_ra_5(home)
        era_a = avg_ra_5(away)
        # 失分越低 → 勝率越高
        prob = era_a / (era_h + era_a)
        prob = max(0.38, min(0.70, prob + 0.02))
        return prob

class ModelE_EnsembleCalibrator:
    """
    對照組：動態 Ensemble 校準器
    - 根據其他模型近30場的準確率動態加權
    - 每日重新計算各模型權重
    """
    name = "E-對照組(Ensemble)"
    history = []

    def __init__(self, models):
        self.models = models
        self.weights = {m.name: 1.0 for m in models}

    def update_weights(self):
        """根據各模型近期表現更新權重"""
        for m in self.models:
            recent = m.history[-30:] if len(m.history) >= 5 else m.history
            if not recent:
                self.weights[m.name] = 1.0
                continue
            acc = sum(1 for x in recent if x) / len(recent)
            # Softmax 式加權：準確率越高權重越大
            self.weights[m.name] = max(0.1, acc ** 2)

    def predict(self, home, away, stats: TeamStats):
        total_w = 0
        weighted_prob = 0
        for m in self.models:
            p = m.predict(home, away, stats)
            if p is None:
                continue
            w = self.weights.get(m.name, 1.0)
            weighted_prob += p * w
            total_w += w
        if total_w == 0:
            return None
        prob = weighted_prob / total_w
        # 校準：壓縮極端值（避免過度自信）
        prob = 0.5 + (prob - 0.5) * 0.88
        return max(0.40, min(0.68, prob))

# ─────────────────────────────────────────────
# 下注選擇邏輯
# ─────────────────────────────────────────────
def select_bets(games_today, model, stats: TeamStats, n=BET_PER_DAY):
    """
    對今日所有比賽評分，選出信心值最高的 n 場
    回傳: [(home, away, prob, side), ...]
    """
    candidates = []
    for g in games_today:
        home, away = g["home"], g["away"]
        prob = model.predict(home, away, stats)
        if prob is None:
            continue
        edge = prob - IMP_PROB
        if prob >= MIN_CONFIDENCE and edge >= MIN_EDGE:
            candidates.append((home, away, prob, "home"))
        elif (1 - prob) >= MIN_CONFIDENCE and (IMP_PROB - prob) >= MIN_EDGE:
            candidates.append((home, away, 1 - prob, "away"))

    # 按信心降序，最多選 n 場
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[:n]

# ─────────────────────────────────────────────
# 回測引擎
# ─────────────────────────────────────────────
def run_backtest():
    season_data = build_season_results(SEASON_START, TODAY)
    if not season_data:
        print("❌ 無法取得賽季資料，請確認網路連線")
        return None

    stats = TeamStats()
    base_models = [ModelA_Pythagorean(), ModelB_RecentForm(), ModelC_ELO(), ModelD_PitcherERA()]
    ensemble    = ModelE_EnsembleCalibrator(base_models)
    all_models  = base_models + [ensemble]

    # 追蹤器
    ledger = {m.name: {"bets": 0, "wins": 0, "pnl": 0.0, "records": []} for m in all_models}

    sorted_days = sorted(season_data.keys())
    total_days  = len(sorted_days)

    print(f"\n🏟️  MLB 多模型回測開始 ({SEASON_START} → {TODAY})\n")
    print(f"{'日期':<12} {'Model':<20} {'下注':<6} {'勝':<5} {'敗':<5} {'日損益':>10}")
    print("─" * 65)

    for i, d in enumerate(sorted_days):
        games = season_data[d]
        finished = [g for g in games if g.get("home_score") is not None]
        if not finished:
            continue

        # 更新 Ensemble 權重（每7天）
        if i % 7 == 0:
            ensemble.update_weights()

        for model in all_models:
            bets = select_bets(finished, model, stats, BET_PER_DAY)
            day_pnl = 0.0
            day_w = day_l = 0

            for home, away, conf, side in bets:
                # 找出實際結果
                game_result = next((g for g in finished
                                    if g["home"] == home and g["away"] == away), None)
                if not game_result:
                    continue

                actual_home_win = game_result["home_win"]
                bet_win = (side == "home" and actual_home_win) or \
                          (side == "away" and not actual_home_win)

                model.history.append(bet_win)
                ledger[model.name]["bets"] += 1

                if bet_win:
                    day_pnl += WIN_PAYOUT
                    day_w   += 1
                    ledger[model.name]["wins"] += 1
                else:
                    day_pnl -= UNIT
                    day_l   += 1

                ledger[model.name]["pnl"] += day_pnl

            ledger[model.name]["records"].append({
                "date": str(d),
                "bets": len(bets),
                "wins": day_w,
                "losses": day_l,
                "pnl": day_pnl,
            })

            if bets:
                sign = "+" if day_pnl >= 0 else ""
                print(f"{str(d):<12} {model.name:<20} {len(bets):<6} {day_w:<5} {day_l:<5} {sign}{day_pnl:>8.1f}")

        # 更新滾動統計（在預測之後更新，避免資料洩漏）
        for g in finished:
            stats.update(g["home"], g["away"], g["home_score"], g["away_score"])

    return ledger, all_models

# ─────────────────────────────────────────────
# 報告輸出
# ─────────────────────────────────────────────
def print_summary(ledger, all_models):
    print("\n" + "═" * 65)
    print("📊  整季回測總結報告")
    print("═" * 65)
    print(f"{'模型':<22} {'總注':<7} {'勝':<6} {'敗':<6} {'勝率':>7} {'總損益':>10} {'ROI':>8}")
    print("─" * 65)

    results = []
    for m in all_models:
        r = ledger[m.name]
        total = r["bets"]
        wins  = r["wins"]
        losses = total - wins
        wr    = wins / total if total else 0
        pnl   = r["pnl"]
        roi   = pnl / (total * UNIT) * 100 if total else 0
        results.append((m.name, total, wins, losses, wr, pnl, roi))

    for row in results:
        name, total, wins, losses, wr, pnl, roi = row
        sign = "+" if pnl >= 0 else ""
        star = " ⭐" if roi > 0 else ""
        print(f"{name:<22} {total:<7} {wins:<6} {losses:<6} {wr:>6.1%} {sign}{pnl:>9.1f} {sign}{roi:>6.1f}%{star}")

    print("═" * 65)
    print(f"{'對照組說明':}")
    print("  Model E 根據其他模型近30場準確率動態加權，每7天重新校準")
    print(f"  下注條件：信心值 ≥ {MIN_CONFIDENCE:.0%} 且優勢 ≥ {MIN_EDGE:.0%}")
    print(f"  賠率：{ODDS} / 每注：${UNIT} / 每日上限：{BET_PER_DAY}場\n")

    return results

def save_results(ledger, all_models, summary_rows):
    # JSON 詳細記錄
    output = {
        "backtest_period": {"start": str(SEASON_START), "end": str(TODAY)},
        "settings": {"unit": UNIT, "odds": ODDS, "max_bets_per_day": BET_PER_DAY,
                     "min_confidence": MIN_CONFIDENCE},
        "models": {}
    }
    for m in all_models:
        r = ledger[m.name]
        total = r["bets"]
        wins  = r["wins"]
        output["models"][m.name] = {
            "total_bets": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total, 4) if total else 0,
            "total_pnl": round(r["pnl"], 2),
            "roi_pct": round(r["pnl"] / (total * UNIT) * 100, 2) if total else 0,
            "daily_records": r["records"],
        }

    json_path = os.path.join(OUTPUT_DIR, "mlb_backtest_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # CSV 摘要
    csv_path = os.path.join(OUTPUT_DIR, "mlb_backtest_summary.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("模型,總注,勝,敗,勝率%,總損益$,ROI%\n")
        for row in summary_rows:
            name, total, wins, losses, wr, pnl, roi = row
            f.write(f"{name},{total},{wins},{losses},{wr*100:.2f},{pnl:.2f},{roi:.2f}\n")

    print(f"\n💾 已儲存:")
    print(f"   JSON：{json_path}")
    print(f"   CSV ：{csv_path}")

    import json as _json
    print(_json.dumps({"type": "generated_file", "path": json_path, "name": "mlb_backtest_results.json"}))
    print(_json.dumps({"type": "generated_file", "path": csv_path, "name": "mlb_backtest_summary.csv"}))

# ─────────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────────
if __name__ == "__main__":
    result = run_backtest()
    if result:
        ledger, all_models = result
        summary_rows = print_summary(ledger, all_models)
        save_results(ledger, all_models, summary_rows)
