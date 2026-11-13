/* ============================================================
   Motion — 轻量动效编排（无依赖，Web Animations + CSS 变量）
   原则：只在内容出现/操作反馈时动，不监听滚动，不做视差。
   ============================================================ */

const Motion = (() => {
  const reduceQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const reduce = () => reduceQuery.matches;
  const EASE = 'cubic-bezier(.16, 1, .3, 1)';

  function safeAnimate(el, frames, options) {
    if (!el || reduce() || typeof el.animate !== 'function') return null;
    try {
      return el.animate(frames, Object.assign({ easing: EASE, fill: 'both' }, options));
    } catch (e) {
      return null;
    }
  }

  /** 简报卡：入场 + 分区错峰由 CSS 负责，这里只标记新卡并清理状态 */
  function briefIn(card) {
    if (!card) return;
    card.classList.add('is-new');
    // 动画结束后移除 is-new，避免 hover/重排时重复触发
    window.setTimeout(() => card.classList.remove('is-new'), 1400);
  }

  /** 列表错峰：只给前 N 个元素加延迟，避免长列表拖沓 */
  function stagger(nodes, opts = {}) {
    const list = Array.prototype.slice.call(nodes || []);
    const step = opts.step == null ? 45 : opts.step;
    const max = opts.max == null ? 8 : opts.max;
    list.slice(0, max).forEach((node, i) => {
      node.classList.add('m-stagger');
      node.style.setProperty('--m-delay', (i * step) + 'ms');
      window.setTimeout(() => node.classList.remove('m-stagger'), 700 + i * step);
    });
  }

  /** 数字滚动：用于统计页，尊重减弱动态 */
  function countUp(node, value, opts = {}) {
    if (!node) return;
    const target = Number(value) || 0;
    if (reduce() || typeof requestAnimationFrame !== 'function') {
      node.textContent = String(target);
      return;
    }
    const duration = opts.duration == null ? 700 : opts.duration;
    const start = performance.now();
    const from = 0;
    function tick(now) {
      if (!node.isConnected) return;
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      node.textContent = String(Math.round(from + (target - from) * eased));
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /** 模式印章：选中时给一个短促的回弹 */
  function sealPop(card) {
    if (!card) return;
    const seal = card.querySelector('.mode-seal');
    safeAnimate(seal, [
      { transform: 'scale(1) rotate(0deg)' },
      { transform: 'scale(1.14) rotate(-3deg)', offset: 0.55 },
      { transform: 'scale(1) rotate(0deg)' },
    ], { duration: 380 });
  }

  /** 发送按钮加载态 */
  function setLoading(button, on) {
    if (!button) return;
    button.classList.toggle('is-loading', !!on);
    button.setAttribute('aria-busy', on ? 'true' : 'false');
  }

  /** 统计图表：进入时让条形从基线长出 */
  function chartsIn(root) {
    if (!root) return;
    root.querySelectorAll('.chart').forEach(chart => chart.classList.add('motion-in'));
  }

  return { briefIn, stagger, countUp, sealPop, setLoading, chartsIn, reduced: reduce };
})();

// 暴露到 window，供其他脚本做能力检测
window.Motion = Motion;
