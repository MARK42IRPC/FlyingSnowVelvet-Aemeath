#!/usr/bin/env python3
"""Generate a sci-fi styled HTML portal that showcases doc + contribution files."""

from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.version_info import APP_VERSION

DOC_DIR = ROOT / "doc"
CONTRIB_DIR = DOC_DIR / "贡献名单和主播的狗盆"
PORTAL_PATH = ROOT / "AA使用必读.html"
IMAGE_PATH = (
    CONTRIB_DIR / "如果想给作者买鸡腿饭的话" / "喵-感谢支持喵-欢迎工单喵.jpg"
)


def _relative_href(path: Path) -> str:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


def _render_cards(paths: list[Path]) -> str:
    cards: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        safe = html.escape(text)
        mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        cards.append(
            f"""        <article class="card">
            <header>
                <div class="eyebrow">{mtime}</div>
                <h3>{html.escape(path.stem)}</h3>
                <a class="download" href="{_relative_href(path)}" download>下载原文件</a>
            </header>
            <pre>{safe}</pre>
        </article>"""
        )
    return "\n".join(cards)


def _generate_portal() -> str:
    dev_contrib_files = sorted(CONTRIB_DIR.glob("开发贡献*.txt"))
    sponsor_files = sorted(CONTRIB_DIR.glob("感谢*.txt"))
    excluded_names = {path.name for path in [*dev_contrib_files, *sponsor_files]}
    doc_files = sorted(path for path in DOC_DIR.glob("*.txt") if path.name not in excluded_names)

    doc_cards = _render_cards(doc_files)
    contrib_cards = _render_cards(dev_contrib_files)
    sponsor_cards = _render_cards(sponsor_files)
    particles_spans = "\n".join(
        f'            <span style="--i:{idx};"></span>' for idx in range(1, 25)
    )
    particle_css = "\n".join(
        (
            ".particles span:nth-child({i}) {{"
            " background:{color};"
            " animation-duration:{duration}s;"
            " animation-delay:-{delay:.2f}s;"
            " }}"
        ).format(
            i=i,
            color=("#FFB6C1", "#ADD8E6", "#d1f2ff", "#f8cde1")[i % 4],
            duration=4 + (i % 5),
            delay=i * 0.35,
        )
        for i in range(1, 25)
    )

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    image_rel = _relative_href(IMAGE_PATH)
    hero_subtitle = f"贡献 + 赞助 + 文档 · {APP_VERSION}"
    harmony_font = "resc/FRONTS/HarmonyOS_Sans_SC_Bold.ttf"
    lahairoi_font = "resc/FRONTS/WuWa%20Lahai-Roi%20Regular.ttf"

    template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>飞行雪绒资料舱 · {version}</title>
    <style>
        @font-face {{
            font-family: 'HarmonyOS Sans';
            src: url('{harmony_font}') format('truetype');
            font-display: swap;
        }}
        @font-face {{
            font-family: 'Lahairoi';
            src: url('{lahairoi_font}') format('truetype');
            font-display: swap;
        }}
        :root {{
            --pink: #FFB6C1;
            --cyan: #ADD8E6;
            --deep-blue: #234C80;
            --bg-dark: #04070c;
            --card-bg: rgba(5, 9, 18, 0.85);
            --border: rgba(173, 216, 230, 0.6);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            font-family: 'HarmonyOS Sans', 'Segoe UI', 'Microsoft YaHei', Arial, sans-serif;
            background: radial-gradient(circle at 20% 20%, rgba(255,182,193,0.3), transparent 65%),
                        radial-gradient(circle at 80% 0%, rgba(173,216,230,0.35), transparent 55%),
                        var(--bg-dark);
            color: #f8fbff;
            overflow-x: hidden;
        }}
        main {{
            position: relative;
            padding: 4rem clamp(1rem, 5vw, 4rem) 5rem;
            z-index: 1;
        }}
        .hero {{
            text-align: center;
            margin-bottom: 3rem;
        }}
        .hero h1 {{
            font-size: clamp(2.6rem, 4vw, 3.6rem);
            margin: 0;
            letter-spacing: 0.1em;
            color: var(--pink);
            text-shadow: 0 0 18px rgba(255,182,193,0.7);
        }}
        .hero p {{
            margin: 0.8rem auto 0;
            font-size: 1.1rem;
            color: var(--cyan);
            max-width: 720px;
            line-height: 1.6;
        }}
        section {{
            margin-bottom: 3rem;
        }}
        section > h2 {{
            font-size: 1.8rem;
            color: var(--cyan);
            margin-bottom: 1rem;
            border-left: 4px solid var(--pink);
            padding-left: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.2em;
        }}
        .cards {{
            display: grid;
            gap: 1.5rem;
            grid-template-columns: repeat(3, minmax(0, 1fr));
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 1.5rem;
            box-shadow: 0 15px 45px rgba(0, 0, 0, 0.45);
            backdrop-filter: blur(6px);
            position: relative;
            overflow: hidden;
        }}
        .card::before {{
            content: '';
            position: absolute;
            inset: 8px;
            border: 1px solid rgba(255,182,193,0.15);
            border-radius: 12px;
            pointer-events: none;
        }}
        .card header {{
            display: flex;
            flex-wrap: wrap;
            align-items: baseline;
            gap: 0.8rem;
            margin-bottom: 1rem;
        }}
        .card h3 {{
            margin: 0;
            font-size: 1.3rem;
            color: var(--pink);
        }}
        .card .eyebrow {{
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.2em;
            color: rgba(255,255,255,0.7);
        }}
        .card .download {{
            margin-left: auto;
            text-decoration: none;
            color: var(--cyan);
            font-size: 0.9rem;
            border-bottom: 1px solid transparent;
            transition: border 0.2s;
        }}
        .card .download:hover {{ border-color: var(--cyan); }}
        pre {{
            margin: 0;
            font-size: 0.9rem;
            line-height: 1.5;
            white-space: pre-wrap;
            color: #e7ebff;
        }}
        figure {{
            margin: 1rem auto 0;
            max-width: 360px;
            text-align: center;
        }}
        figure img {{
            width: 100%;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.25);
            box-shadow: 0 12px 30px rgba(0,0,0,0.5);
        }}
        figure figcaption {{
            margin-top: 0.6rem;
            font-size: 0.85rem;
            color: rgba(255,255,255,0.8);
        }}
        footer {{
            text-align: center;
            padding: 2rem 1rem 4rem;
            font-size: 0.85rem;
            color: rgba(255,255,255,0.65);
        }}
        .section-desc {{
            color: rgba(255,255,255,0.85);
            max-width: 720px;
            margin-bottom: 1rem;
            line-height: 1.5;
        }}
        .top-panels {{
            display: flex;
            flex-wrap: wrap;
            gap: 1.5rem;
        }}
        .top-panel {{
            flex: 1 1 320px;
            min-width: 0;
        }}
        .trail-container {{
            position: fixed;
            inset: 0;
            pointer-events: none;
            z-index: 3;
        }}
        .trail-letter {{
            position: absolute;
            font-family: 'Lahairoi', 'HarmonyOS Sans', sans-serif;
            font-weight: 600;
            font-size: 1.2rem;
            color: var(--pink);
            opacity: 0.9;
            animation: trailBurst 0.9s ease-out forwards;
            text-shadow: 0 0 12px rgba(255,182,193,0.8), 0 0 24px rgba(173,216,230,0.6);
        }}
        @keyframes trailBurst {{
            0% {{ transform: translate3d(0,0,0) scale(1); opacity: 0.95; }}
            100% {{ transform: translate3d(var(--dx, 0px), var(--dy, -60px), 0) scale(0.3); opacity: 0; }}
        }}
        .particles {{
            position: fixed;
            inset: 0;
            overflow: hidden;
            pointer-events: none;
            z-index: 0;
        }}
        .particles span {{
            position: absolute;
            width: 6px;
            height: 6px;
            border-radius: 50%;
            opacity: 0.8;
            animation: float 8s linear infinite;
            left: calc(4% * var(--i));
            top: calc(3% * var(--i));
            box-shadow: 0 0 12px currentColor;
        }}
{particle_css}
        @keyframes float {{
            0% {{ transform: translate3d(0, 0, 0); opacity: 0; }}
            20% {{ opacity: 0.85; }}
            100% {{ transform: translate3d(40px, -120px, 0); opacity: 0; }}
        }}
        @media (min-width: 768px) {{
            .cards {{ grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
        }}
    </style>
</head>
<body>
    <div class="particles">
{particles}
    </div>
    <div class="trail-container" id="trail-root"></div>
    <main>
        <div class="hero">
            <h1>飞行雪绒资料舱</h1>
            <p>{hero_subtitle}<br>基于项目默认配色 + 粒子灵感打造，方便在浏览器里查阅 doc 与贡献记录。生成时间：{timestamp}</p>
        </div>
        <section id="top-panels">
            <div class="top-panels">
                <div class="top-panel">
                    <h2>贡献列表</h2>
                    <div class="cards">
{contrib_cards}
                    </div>
                </div>
                <div class="top-panel">
                    <h2>赞助列表</h2>
                    <div class="cards">
{sponsor_cards}
                    </div>
                </div>
            </div>
        </section>
        <section id="dog-bowl">
            <h2>狗盆墙</h2>
            <figure>
                <img src="{image_rel}" alt="感谢支持喵">
                <figcaption>如果想给作者买鸡腿饭的话 · 原图</figcaption>
            </figure>
        </section>
        <section id="documents">
            <h2>DOC</h2>
            <div class="cards">
{doc_cards}
            </div>
        </section>
    </main>
    <footer>由飞行雪绒 {version} 代码生成 · 粉粉青青也是科技感 (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧</footer>
    <script>
    (() => {{
        const letters = "FLYINGSNOWVELVET";
        const trailRoot = document.getElementById("trail-root");
        if (!trailRoot) return;
        document.addEventListener("mousemove", (event) => {{
            const span = document.createElement("span");
            span.className = "trail-letter";
            span.textContent = letters[Math.floor(Math.random() * letters.length)];
            span.style.left = `${{event.clientX}}px`;
            span.style.top = `${{event.clientY}}px`;
            const dx = (Math.random() * 80) - 40;
            const dy = -30 - Math.random() * 80;
            span.style.setProperty("--dx", `${{dx}}px`);
            span.style.setProperty("--dy", `${{dy}}px`);
            trailRoot.appendChild(span);
            setTimeout(() => span.remove(), 900);
        }});
    }})();
    </script>
</body>
</html>
"""

    return template.format(
        particle_css=particle_css,
        particles=particles_spans,
        hero_subtitle=hero_subtitle,
        timestamp=timestamp,
        doc_cards=doc_cards,
        contrib_cards=contrib_cards,
        image_rel=image_rel,
        sponsor_cards=sponsor_cards,
        harmony_font=harmony_font,
        lahairoi_font=lahairoi_font,
        version=APP_VERSION,
    )


def main() -> None:
    html_text = _generate_portal()
    PORTAL_PATH.write_text(html_text, encoding="utf-8")
    print(f"Wrote {PORTAL_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
