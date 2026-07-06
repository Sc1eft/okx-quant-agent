"""共享 Streamlit 布局组件

从 9_EthereumLive.py 和 11_AI_Trading.py 提取的重复 JS/CSS 代码。
"""
from __future__ import annotations

import streamlit.components.v1 as _comps

__all__ = ["inject_mask_hider_js"]


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
