# 真實盤口資料

這個資料夾只放真實盤口或成交賠率，不放推估賠率。

每日 moneyline 檔名：

```text
data/odds/mlb_moneyline_YYYY-MM-DD.csv
```

必要欄位：

- `date`：比賽日期，格式 `YYYY-MM-DD`。
- `game_pk`：MLB Stats API 的 gamePk。若沒有 gamePk，腳本會退而用中文對戰隊名比對。
- `sportsbook`：盤口來源或實際下注平台。
- `captured_at_tw`：盤口擷取時間，台灣時間。
- `away_zh`：客隊中文名。
- `home_zh`：主隊中文名。
- `away_moneyline`：客隊美式賠率，例如 `+125` 或 `-135`。
- `home_moneyline`：主隊美式賠率，例如 `+110` 或 `-120`。

ROI 腳本會拒絕缺少必要欄位或無法轉成美式賠率的資料。沒有真實盤口時，不計算投注 ROI。
