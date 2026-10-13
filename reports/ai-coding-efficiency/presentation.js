"use strict";
(() => {
  const slides = [...document.querySelectorAll('.slide')];
  const stage = document.querySelector('#stage');
  const canvas = document.querySelector('#canvas');
  const toc = document.querySelector('#toc-dialog');
  const viewButton = document.querySelector('#view');
  const params = new URLSearchParams(location.search);
  const pageFromHash = () => Math.max(0, Math.min(slides.length - 1, (Number(location.hash.match(/^#s-(\d+)$/)?.[1]) || 1) - 1));
  let current = pageFromHash();
  let reading = params.get('view') === 'read';
  let toastTimeout;
  const dots = [];

  slides.forEach((slide, i) => {
    const dot = document.createElement('button');
    dot.type = 'button';
    dot.setAttribute('aria-label', `第 ${i + 1} 屏：${slide.dataset.title}`);
    dot.addEventListener('click', () => show(i, true));
    document.querySelector('#page-dots').append(dot);
    dots.push(dot);
    const li = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    const number = document.createElement('span');
    number.textContent = String(i + 1).padStart(2, '0');
    button.append(number, document.createTextNode(slide.dataset.title));
    button.addEventListener('click', () => { toc.close(); show(i, true); });
    li.append(button);
    document.querySelector('#toc-list').append(li);
  });

  function fit() {
    if (reading) return;
    const scale = Math.min(stage.clientWidth / 1600, stage.clientHeight / 900);
    canvas.style.setProperty('--scale', Math.max(.01, scale));
  }
  function updateUrl() {
    const url = new URL(location.href);
    reading ? url.searchParams.set('view', 'read') : url.searchParams.delete('view');
    url.hash = slides[current].id;
    history.replaceState(null, '', url);
  }
  function show(index, scroll = false, persist = true) {
    current = Math.max(0, Math.min(slides.length - 1, index));
    slides.forEach((slide, i) => {
      slide.classList.toggle('active', i === current);
      slide.inert = !reading && i !== current;
      slide.setAttribute('aria-hidden', String(!reading && i !== current));
    });
    dots.forEach((dot, i) => dot.setAttribute('aria-current', String(i === current)));
    document.querySelector('#position').textContent = `${String(current + 1).padStart(2, '0')} / ${slides.length}`;
    document.querySelector('#section-label').textContent = current < 2 ? '全篇主线' : current < 6 ? '任务内提效' : current === 6 ? '转折' : '人的成长';
    document.querySelector('#progress').style.width = `${(current + 1) / slides.length * 100}%`;
    document.querySelector('#prev').disabled = current === 0;
    document.querySelector('#next').disabled = current === slides.length - 1;
    if (persist) updateUrl();
    if (reading && scroll) slides[current].scrollIntoView({block: 'start', behavior: 'instant'});
  }
  function setReading(value, scroll = true) {
    reading = value;
    document.body.classList.toggle('reading', reading);
    viewButton.textContent = reading ? '分页演示' : '阅读模式';
    viewButton.setAttribute('aria-pressed', String(reading));
    show(current, false);
    fit();
    if (reading && scroll) slides[current].scrollIntoView({block: 'start', behavior: 'instant'});
    else if (!reading) window.scrollTo(0, 0);
  }
  document.querySelector('#prev').addEventListener('click', () => show(current - 1, true));
  document.querySelector('#next').addEventListener('click', () => show(current + 1, true));
  viewButton.addEventListener('click', () => setReading(!reading));
  document.querySelector('#contents').addEventListener('click', () => toc.showModal());
  document.querySelector('#close-contents').addEventListener('click', () => toc.close());
  toc.addEventListener('click', event => {
    const rect = toc.getBoundingClientRect();
    if (event.target === toc && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) toc.close();
  });
  document.addEventListener('keydown', event => {
    if (toc.open || event.altKey || event.ctrlKey || event.metaKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
    // Space retains its native button action when a control is focused.
    if (event.key === ' ' && /BUTTON|A/.test(event.target.tagName)) return;
    if (event.key === 'ArrowRight' || event.key === 'PageDown' || (event.key === ' ' && !reading)) {
      event.preventDefault(); show(current + 1, true);
    } else if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
      event.preventDefault(); show(current - 1, true);
    } else if (event.key === 'Home' && !reading) {
      event.preventDefault(); show(0);
    } else if (event.key === 'End' && !reading) {
      event.preventDefault(); show(slides.length - 1);
    }
  });
  function notify(message) {
    const toast = document.querySelector('#toast');
    clearTimeout(toastTimeout);
    toast.textContent = message;
    toast.hidden = false;
    toastTimeout = setTimeout(() => { toast.hidden = true; }, 6000);
  }
  document.querySelector('#fullscreen').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      notify('嵌入页可能限制全屏，请从报告详情页“新窗口打开”进入演示。');
    }
  });
  document.addEventListener('fullscreenchange', () => {
    document.querySelector('#fullscreen').textContent = document.fullscreenElement ? '退出全屏' : '全屏演示';
    fit();
  });
  new ResizeObserver(fit).observe(stage);
  window.addEventListener('hashchange', () => show(pageFromHash(), true, false));
  let startTouch;
  stage.addEventListener('touchstart', event => {
    if (reading || event.target.closest('button,a')) return;
    startTouch = {x: event.changedTouches[0].clientX, y: event.changedTouches[0].clientY};
  }, {passive: true});
  stage.addEventListener('touchend', event => {
    if (!startTouch || reading) return;
    const dx = event.changedTouches[0].clientX - startTouch.x;
    const dy = event.changedTouches[0].clientY - startTouch.y;
    if (Math.abs(dx) > 70 && Math.abs(dy) < 60) show(current + (dx < 0 ? 1 : -1));
    startTouch = null;
  }, {passive: true});
  let scrollFrame = false;
  window.addEventListener('scroll', () => {
    if (!reading || scrollFrame) return;
    scrollFrame = true;
    requestAnimationFrame(() => {
      scrollFrame = false;
      let visible = 0;
      slides.forEach((slide, i) => { if (slide.getBoundingClientRect().top < 180) visible = i; });
      if (visible !== current) show(visible);
    });
  }, {passive: true});

  // The context-length evidence now uses the full source figure as a thumbnail.

  function selectable(selector, texts, target) {
    const buttons = [...document.querySelectorAll(selector)];
    buttons.forEach((button, selected) => button.addEventListener('click', () => {
      buttons.forEach((b, i) => { b.classList.toggle('selected', i === selected); b.setAttribute('aria-pressed', String(i === selected)); });
      document.querySelector(target).textContent = texts[selected];
    }));
  }
  selectable('[data-status]', [
    '先核对改动范围：目标问题是否解决，是否引入了无关修改？',
    '检查是否覆盖了本次风险；实现和测试共享同一个错误假设，也可能得到绿勾。',
    '核对构建产物、部署状态、实际运行镜像与浏览器资源，确认版本真的更新了。',
    '走实际页面，给出操作序列、预期与实际；用户体验符合要求，才完成这次验收。'
  ], '#status-detail');
  setReading(reading, false);
  fit();
  if (reading) requestAnimationFrame(() => slides[current].scrollIntoView({block: 'start'}));
})();
