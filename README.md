# MLB 實戰投注計畫

這個專案把 MLB 多模型回測結果整理成可執行的投注計畫儀表板。核心用途不是保證獲利，而是把模型選擇、下注單位、停損、降注與每日紀錄流程固定下來，避免實戰時憑感覺加碼或追單。

## 專案內容

- `data/mlb_backtest_summary.csv`：五個模型的回測摘要。
- `data/mlb_backtest_results.raw.txt`：原始貼上 JSON，尾端可能含有額外說明文字。
- `data/mlb_backtest_results.json`：由產生器清理後的正式 JSON。
- `scripts/mlb_multi_model_backtest_source.py`：原始 MLB 多模型回測程式。
- `scripts/fetch_real_mlb_data.py`：從 MLB Stats API 抓取真實 MLB 賽程與完賽比分。
- `scripts/name_localization.py`：將 MLB 球隊與球員顯示名稱轉成繁體中文。
- `scripts/run_real_mlb_backtest.py`：使用 `data/real_mlb_games.csv` 的真實比分重新回測。
- `scripts/run_real_mlb_prediction_accuracy.py`：只統計真實勝方預測準確率，不使用盤口、不計 ROI。
- `scripts/settle_daily_predictions.py`：賽後抓真實比分並結算每日預測正確率。
- `scripts/prepare_odds_template.py`：從每日預測產生指定日期的真實盤口填寫檔。
- `scripts/fetch_espn_moneyline_odds.py`：從 ESPN scoreboard 公開資料填入可取得的 DraftKings moneyline。
- `scripts/fetch_taiwan_sportslottery_odds.py`：從台灣運彩官方 sportsbook JSON 填入棒球不讓分小數賠率。
- `scripts/validate_odds_file.py`：檢查真實盤口檔是否欄位完整、moneyline 格式正確。
- `scripts/settle_betting_roi.py`：讀取真實 moneyline 盤口並結算投注 PnL/ROI。
- `scripts/generate_betting_ticket.py`：從 ROI 候選產生今日投注單。
- `scripts/run_totals_v1.py`：產生台灣運彩全場大小分 v1 預測與候選。
- `scripts/run_advanced_factors_model.py`：產生打擊率/壘包/雙殺/三振/四壞/投手型態/牛棚/連勝連敗/對戰/場地代理的進階因子勝方模型。
- `scripts/backtest_train_2024_2025_test_2026.py`：用 2024-2025 訓練、2026 已完賽資料測試獨贏準確率與大小分總分模型品質。
- `scripts/generate_plan.py`：讀取回測資料並產生實戰計畫。
- `scripts/generate_daily_plan.py`：抓指定日期 MLB 賽程並產生中文每日勝方預測。
- `data/odds/mlb_moneyline_template.csv`：真實盤口匯入模板。
- `docs/index.html`：可直接開啟的中文儀表板。
- `docs/prediction_accuracy.html`：真實比分下的模型預測準確率。
- `docs/daily_predictions.html`：可直接開啟的中文每日勝方預測。
- `docs/betting_ticket.html`：今日投注單，只列出真實盤口且 edge 通過的場次。
- `docs/totals_predictions.html`：台灣運彩全場大小分 v1 預測。
- `docs/advanced_factors.html`：進階因子勝方模型 v1。
- `docs/backtest_2026_report.html`：2024-2025 訓練、2026 測試回測報告。
- `docs/prediction_log.html`：每日實戰預測的賽後結算紀錄。
- `docs/betting_roi.html`：真實盤口匯入後的投注 ROI 紀錄。
- `docs/status.html`：專案完成度、缺盤口、待結算與下一步指令。
- `docs/plan.json`：儀表板使用的計畫摘要與風控規則。
- `docs/assets/dashboard_concept.png`：本次儀表板的設計概念圖。

## 使用方式

重新產生資料與儀表板：

```powershell
python scripts\fetch_real_mlb_data.py --start-date 2025-03-27 --end-date 2026-06-25
python scripts\run_real_mlb_prediction_accuracy.py
python scripts\run_real_mlb_backtest.py
python scripts\generate_plan.py
python scripts\generate_daily_plan.py --date 2026-06-26
python scripts\settle_daily_predictions.py --date 2026-06-25
python scripts\prepare_odds_template.py --date 2026-06-25
```

每日實戰更新可直接跑整合管線：

```powershell
python scripts\run_daily_workflow.py --date YYYY-MM-DD --all-predictions
```

如果只要更新每日預測、盤口與 ROI，不重跑歷史準確率和固定賠率參考：

```powershell
python scripts\run_daily_workflow.py --date YYYY-MM-DD --all-predictions --skip-backtest-refresh
```

Windows 也可以直接執行：

```powershell
.\run_daily_workflow.cmd YYYY-MM-DD
.\run_full_refresh.cmd YYYY-MM-DD
```

自動更新有兩種方式：

```powershell
.\start_auto_runner.cmd
```

這會開一個常駐視窗，每 60 分鐘自動更新今天的賽程、預測、真實盤口、投注單與 ROI，關掉視窗就停止。

如果要交給 Windows 自動排程，每小時背景跑一次：

```powershell
.\install_windows_auto_runner.cmd
```

自動更新狀態會寫到 `data/auto_runner_status.json`，詳細紀錄會寫到 `logs/auto_runner/`。
Windows 工作排程名稱是 `MLB_Betting_Auto_Update`。安裝檔會另外建立 `C:\tmp\mlb_betting_project` 資料夾捷徑與 `C:\tmp\mlb_betting_auto_update.cmd` 啟動器，避免排程器因中文路徑解析失敗。

其他 Windows 快捷檔：

```powershell
.\開啟網頁.cmd
.\立刻更新一次.cmd
.\開啟常駐自動更新.cmd
.\安裝每小時自動排程.cmd
.\移除每小時自動排程.cmd
.\open_dashboard.cmd
.\run_auto_once.cmd
.\uninstall_windows_auto_runner.cmd
```

- `開啟網頁.cmd`：直接開啟主控台。
- `立刻更新一次.cmd`：立刻更新今天一次。
- `開啟常駐自動更新.cmd`：開一個視窗，每 60 分鐘自動更新一次。
- `安裝每小時自動排程.cmd`：交給 Windows 每小時背景自動更新。
- `移除每小時自動排程.cmd`：移除每小時自動排程。
- `open_dashboard.cmd`：直接開啟主控台。
- `run_auto_once.cmd`：立刻更新今天一次。
- `uninstall_windows_auto_runner.cmd`：移除每小時自動排程。

開啟儀表板：

```powershell
Start-Process docs\index.html
```

## 目前實戰基準

第一層先看純勝方預測準確率，不使用盤口、不計 ROI。目前準確率最佳模型是 `A-畢氏勝率`：

- 真實比分場數：3641
- 實際覆蓋日期：2025-03-27 至 2026-06-25
- 預測場次：3562
- 正確：1932
- 錯誤：1630
- 準確率：54.24%

投注 ROI 需要真實盤口與成交賠率才能成立。固定 -110 的舊回測只作為暫時參考；等盤口資料補上後，再計算真實投注 ROI。

固定 -110 暫時參考下，`A-畢氏勝率` ROI 最高：

- 總注數：1106
- 勝敗：654 勝 / 452 敗
- 勝率：59.13%
- 總損益：14255.14
- ROI：12.89%

每日預測目前採 `A-畢氏勝率` 作為主模型、`E-對照組(Ensemble)` 作為方向確認模型；此處只產生勝方預測，不產生下注額與 ROI。

賽後可重跑 `scripts\settle_daily_predictions.py --date YYYY-MM-DD`。未完賽場次會標記為待結算，之後重跑會用最新比分更新正確率。

先用每日預測產生該日盤口填寫檔：

```powershell
python scripts\prepare_odds_template.py --date YYYY-MM-DD
```

如果 ESPN scoreboard 已提供 DraftKings moneyline，可先自動回填可取得的場次：

```powershell
python scripts\fetch_espn_moneyline_odds.py --date YYYY-MM-DD
```

正式流程會再用台灣運彩官方賠率覆蓋同場盤口：

```powershell
python scripts\fetch_taiwan_sportslottery_odds.py --date YYYY-MM-DD
```

台灣運彩賠率來源是官方 `sportslottery.com.tw` 前端使用的 `blob3rd.sportslottery.com.tw/apidata/Pre/34731.1-Games.zh.json`，棒球不讓分賠率會以小數賠率保存，例如 `1.80`、`2.10`。ESPN/DraftKings 只作為原始資料備援；正式投注推薦只接受台灣運彩盤口，沒有台灣運彩盤口就完全不推薦。

仍空白的場次代表可用來源未提供盤口，不能用推估值補上。確認 `data/odds/mlb_moneyline_YYYY-MM-DD.csv` 只包含真實盤口後，再執行：

```powershell
python scripts\validate_odds_file.py --date YYYY-MM-DD --allow-partial
python scripts\settle_betting_roi.py --date YYYY-MM-DD
```

沒有真實盤口檔時，ROI 腳本會停止，不會用固定 -110 或推估賠率假裝是真實 ROI。

## 風控原則

- 每注固定 100。
- 單日最多 5 注。
- 任一日達到 -2U 即停止追加下注。
- 連續三個活躍日虧損時，隔日降為 2 注上限。
- 準確率追蹤階段只標記高信心預測，不輸出下注金額。
- 實戰必須額外記錄成交賠率、盤口、下注時間與賽前資訊，不能只依賴回測輸出。

## 注意

目前回測已支援使用 MLB Stats API 的真實例行賽完賽比分。抓取長日期區間時會自動分段請求，這次保存 `2025-03-27` 至 `2026-06-25` 共 3641 場。賠率仍使用固定 -110 作為市場假設；若要做完整真實投注回測，下一步應加入每日真實盤口資料、下注紀錄 CSV，以及賽後結算腳本。

球隊名稱使用固定繁中對照。球員名稱會先套用常見球員/姓氏對照，未知姓名則用 deterministic 音譯規則產生中文顯示名；英文原名仍保留在資料欄位中供追溯。
