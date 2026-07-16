#!/usr/bin/env python3
"""Add a consistent side navigation to generated docs pages."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"

NAV_ITEMS = [
    ("index.html", "總覽"),
    ("daily_predictions.html", "今日預測"),
    ("betting_ticket.html", "投注單"),
    ("totals_predictions.html", "大小分"),
    ("advanced_factors.html", "進階因子"),
    ("quant_model.html", "Q-量化模型"),
    ("game_simulator.html", "逐打席模擬"),
    ("monte_carlo.html", "蒙地卡羅"),
    ("market_research.html", "盤口研究"),
    ("prediction_log.html", "結算紀錄"),
    ("postgame_review.html", "賽後檢討"),
    ("betting_roi.html", "投注 ROI"),
    ("lineup_fix_comparison.html", "修正前後"),
    ("winner_model_search.html", "模型搜尋"),
    ("prediction_accuracy.html", "準確率"),
    ("status.html", "狀態"),
    ("api/index.html", "資料 API"),
]

SKIP_FILES = {"index.html"}
MARKER = "data-site-nav=\"true\""
CSS_MARKER = "/* site navigation */"


def nav_html(current: str) -> str:
    links = []
    for href, label in NAV_ITEMS:
        active = " active" if href == current else ""
        links.append(f'        <a class="site-nav-link{active}" href="{href}">{label}</a>')
    return "\n".join(
        [
            f'  <div class="site-shell" {MARKER}>',
            '    <aside class="site-sidebar">',
            '      <div class="site-brand">MLB 投注計畫</div>',
            '      <nav aria-label="主要頁面">',
            *links,
            "      </nav>",
            "    </aside>",
        ]
    )


def refresh_nav_links(text: str, current: str) -> str:
    aside_start = text.find('<aside class="site-sidebar">')
    nav_start = text.find("<nav", aside_start)
    nav_open_end = text.find(">", nav_start)
    nav_end = text.find("</nav>", nav_open_end)
    if min(aside_start, nav_start, nav_open_end, nav_end) < 0:
        return text
    links = []
    for href, label in NAV_ITEMS:
        active = " active" if href == current else ""
        links.append(f'        <a class="site-nav-link{active}" href="{href}">{label}</a>')
    return text[: nav_open_end + 1] + "\n" + "\n".join(links) + "\n      " + text[nav_end:]


def site_css() -> str:
    return f"""
    {CSS_MARKER}
    .site-shell {{ min-height: 100vh; display: grid; grid-template-columns: 220px minmax(0, 1fr); background: inherit; }}
    .site-sidebar {{ position: sticky; top: 0; align-self: start; height: 100vh; box-sizing: border-box; overflow-y: auto; border-right: 1px solid #dfe5df; background: #ffffff; padding: 22px 14px; z-index: 10; }}
    .site-brand {{ color: #163b34; font-size: 15px; font-weight: 900; letter-spacing: 0; margin: 0 0 14px; }}
    .site-sidebar nav {{ display: grid; gap: 6px; }}
    .site-nav-link {{ display: block; color: #40514b; text-decoration: none; border-radius: 8px; padding: 9px 10px; font-size: 14px; font-weight: 750; line-height: 1.25; }}
    .site-nav-link:hover {{ background: #eef4f1; color: #123d35; }}
    .site-nav-link.active {{ background: #165f56; color: #ffffff; }}
    .site-shell > header {{ grid-column: 2; min-width: 0; }}
    .site-shell > main {{ grid-column: 2; width: min(1180px, calc(100% - 56px)); margin-left: auto; margin-right: auto; min-width: 0; }}
    @media (max-width: 900px) {{
      .site-shell {{ display: block; }}
      .site-sidebar {{ position: static; height: auto; border-right: 0; border-bottom: 1px solid #dfe5df; }}
      .site-sidebar nav {{ grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); }}
      .site-shell > header {{ grid-column: auto; }}
      .site-shell > main {{ width: auto; }}
    }}
"""


def strip_existing(text: str) -> str:
    if MARKER not in text:
        return text
    start = text.find(f'  <div class="site-shell" {MARKER}>')
    aside_end = text.find("</aside>", start)
    if start >= 0 and aside_end > start:
        text = text[:start] + text[aside_end + len("</aside>") :]
    end_marker = "\n  </div>\n</body>"
    if end_marker in text:
        text = text.replace(end_marker, "\n</body>", 1)
    return text


def inject_css(text: str) -> str:
    if CSS_MARKER in text:
        start = text.find(CSS_MARKER)
        end = text.find("</style>", start)
        if end >= 0:
            return text[:start] + site_css().strip() + "\n  " + text[end:]
        return text
    css = site_css()
    if "</style>" in text:
        return text.replace("</style>", css + "  </style>", 1)
    return text.replace("</head>", f"<style>{css}</style>\n</head>", 1)


def apply_nav(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return False
    text = path.read_text(encoding="utf-8-sig")
    original = text
    if MARKER in text:
        text = inject_css(text)
        text = refresh_nav_links(text, path.name)
        if text != original:
            path.write_text(text, encoding="utf-8")
            return True
        return False
    text = strip_existing(text)
    text = inject_css(text)
    text = text.replace("<body>", f"<body>\n{nav_html(path.name)}", 1)
    text = text.replace("</body>", "  </div>\n</body>", 1)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = []
    for path in sorted(DOCS_DIR.glob("*.html")):
        if apply_nav(path):
            changed.append(path.name)
    print(f"site_navigation_updated={len(changed)}")
    for name in changed:
        print(name)


if __name__ == "__main__":
    main()
