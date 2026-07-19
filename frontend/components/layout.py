"""共享 Streamlit 布局组件 — 视觉原语统一封装

page_header / section_card / status_bar / ticker_bar / empty_state / metric_row
页面不再手写 HTML 片段，全部走这里（样式类定义在 frontend/assets/style.css）。
"""
from __future__ import annotations

from contextlib import contextmanager

import streamlit as st
import streamlit.components.v1 as _comps

__all__ = [
    "inject_mask_hider_js",
    "page_header",
    "section_card",
    "status_bar",
    "ticker_bar",
    "empty_state",
    "metric_row",
]


# ─────────────────────────────────────────────
# 视觉原语（样式类见 style.css）
# ─────────────────────────────────────────────

def page_header(title: str, desc: str = "", badge: str = "", badge_type: str = "green"):
    """页面横幅：标题 + 一句话说明（这页是干什么的）+ 可选状态徽章。

    badge_type: green / red / amber / blue / gray
    """
    badge_html = (
        f'<span class="badge badge--{badge_type}">{badge}</span>' if badge else ""
    )
    st.markdown(f"""
    <div class="page-header">
        <div class="ph-text">
            <h1>{title}</h1>
            <p>{desc}</p>
        </div>
        <div class="ph-badge">{badge_html}</div>
    </div>
    """, unsafe_allow_html=True)


@contextmanager
def section_card(title: str = "", icon: str = ""):
    """卡片容器（真实 DOM 嵌套，基于 st.container(border=True)）。

    用法:
        with section_card("控制面板", "⚙"):
            st.button(...)
    """
    with st.container(border=True):
        if title:
            st.markdown(
                f'<div class="section-title">{icon} {title}</div>',
                unsafe_allow_html=True,
            )
        yield


def status_bar(status_text: str, items: list[tuple[str, str]] | None = None,
               state: str = "ok"):
    """状态条：脉冲点 + 状态文本 + 键值对横排。

    state: ok（绿）/ warn（黄）/ off（灰）
    items: [(label, value), ...]
    """
    pairs = "".join(
        f'<span class="status-item">'
        f'<span class="sb-label">{k}</span>'
        f'<span class="sb-value">{v}</span></span>'
        for k, v in (items or [])
    )
    st.markdown(f"""
    <div class="status-bar status-bar--{state}">
        <span class="status-item"><span class="status-dot"></span><strong>{status_text}</strong></span>
        {pairs}
    </div>
    """, unsafe_allow_html=True)


def ticker_bar(items: list[dict], badge: str = "✅ 实时数据", badge_type: str = "green"):
    """深色行情条。

    items: [{"label": "ETH-USDT", "value": "$1,843 +1.6%", "color": "green"}, ...]
           color 可选 green / red / ""（默认白）
    """
    parts = []
    for it in items:
        color = it.get("color", "")
        parts.append(
            f'<div class="ticker-item">'
            f'<span class="ticker-label">{it["label"]}</span>'
            f'<span class="ticker-value {color}">{it["value"]}</span></div>'
        )
    badge_html = (
        f'<div style="margin-left:auto; position:relative; z-index:1;">'
        f'<span class="badge badge--{badge_type}">{badge}</span></div>'
        if badge else ""
    )
    st.markdown(
        f'<div class="ticker-bar">{"".join(parts)}{badge_html}</div>',
        unsafe_allow_html=True,
    )


def empty_state(icon: str, title: str, hint: str):
    """空状态引导：图标 + 说明 + 下一步指引。"""
    st.markdown(f"""
    <div class="empty-state">
        <div class="empty-icon">{icon}</div>
        <div class="empty-title">{title}</div>
        <div class="empty-hint">{hint}</div>
    </div>
    """, unsafe_allow_html=True)


def metric_row(items: list[dict]):
    """KPI 卡横排（左色条卡片，风格同 metrics_display）。

    items: [{"label": "总权益", "value": "$10,070", "sub": "+0.7%", "color": "green"}, ...]
           color: green / red / blue / amber / purple / gray（左色条 + 数字色）
    """
    cols = st.columns(len(items))
    for col, m in zip(cols, items):
        with col:
            color = m.get("color", "gray")
            value_cls = color if color in ("green", "red", "blue", "amber") else ""
            sub = f'<div class="sub">{m["sub"]}</div>' if m.get("sub") else ""
            st.markdown(f"""
            <div class="metric-card metric-card--{color}">
                <div class="label">{m["label"]}</div>
                <div class="value {value_cls}">{m["value"]}</div>
                {sub}
            </div>
            """, unsafe_allow_html=True)


def inject_mask_hider_js():
    """隐藏 st.rerun / st.fragment 时的加载蒙版。

    CSS 基础防御 + MutationObserver 动态拦截，双层保障。
    在页面顶部调用一次即可。
    """
    _comps.html("""<script>
(function() {
    'use strict';
    var doc;
    try { doc = parent.document; } catch(e) { doc = document; }
    if (!doc) return;
    var style = doc.createElement('style');
    style.setAttribute('data-mask-killer', '');
    style.textContent = [
        '[data-testid*="Status"], [data-testid*="status"],',
        '[data-testid*="Loading" i], [data-testid*="loading" i],',
        '[data-testid*="Spinner"], [data-testid*="spinner"],',
        '[data-testid*="Blocking"], [data-testid*="blocking"],',
        '[data-testid*="stStatusWidget"],',
        'div[class*="stAppLoading"],',
        'div[class*="stBlock"],',
        'div[class*="stStatus"],',
        'div[class*="stSpinner"],',
        'div[class*="stLoading"],',
        'div[class*="StyledThumb"],',
        'aside[data-testid*="stStatus"],',
        'aside[class*="stStatus"],',
        'iframe[title*="stStatus"],',
        'iframe[title*="loading" i],',
        'div[class*="stAppViewBlocking"],',
        'div[data-testid*="stFragment"] > div[class*="loading"]',
    ].join('') + ' {' +
        'display: none !important;' +
        'visibility: hidden !important;' +
        'opacity: 0 !important;' +
        'pointer-events: none !important;' +
        'z-index: -9999 !important;' +
        'width: 0 !important;' +
        'height: 0 !important;' +
        'overflow: hidden !important;' +
        'position: fixed !important;' +
    '}';
    doc.head.appendChild(style);
    var TARGETS = [
        '[data-testid*="Status"]', '[data-testid*="status"]',
        '[data-testid*="Loading" i]', '[data-testid*="loading" i]',
        '[data-testid*="Spinner"]', '[data-testid*="spinner"]',
        '[data-testid*="Blocking"]', '[data-testid*="blocking"]',
    ];
    var combined = TARGETS.join(',');
    function kill() {
        var els = doc.querySelectorAll(combined);
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (el.style.display !== 'none') {
                el.style.setProperty('display', 'none', 'important');
                el.style.setProperty('z-index', '-9999', 'important');
            }
        }
    }
    var observer = new MutationObserver(function(muts) {
        for (var m = 0; m < muts.length; m++) {
            if (muts[m].type === 'attributes' ||
                (muts[m].addedNodes && muts[m].addedNodes.length > 0)) {
                kill(); break;
            }
        }
    });
    var target = doc.body || doc.documentElement;
    if (target) {
        observer.observe(target, {
            childList: true, subtree: true, attributes: true,
            attributeFilter: ['style', 'class', 'data-testid'],
        });
    }
    setInterval(kill, 300);
    kill();
})();
</script>""", height=0)
