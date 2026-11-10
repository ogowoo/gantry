// models.js — 本地模型管理：列出 / 删除 / 下载 data/models/*.gguf
import { api, esc } from './apistate.js';

function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

export async function openModels() {
  const mask = document.getElementById('modelsMask');
  if (!mask) return;
  mask.classList.add('open');
  await renderModels();
}

export function closeModels() {
  const mask = document.getElementById('modelsMask');
  if (mask) mask.classList.remove('open');
}

async function fetchLocalStatus() {
  try {
    const r = await api('/v1/models/local/status');
    return (r && r.providers) || [];
  } catch (e) {
    return [];
  }
}

function statusBadge(p) {
  if (!p) return '';
  if (p.error) return `<span class="model-status bad" title="${esc(p.error)}">${esc(tr('错误'))}</span>`;
  if (p.loaded) {
    const perf = p.tok_per_second > 0 ? ` · ${p.tok_per_second} tok/s` : '';
    const info = `${esc(tr('已加载'))} · ${p.load_seconds}s${perf} · ${p.calls} ${esc(tr('次调用'))}`;
    return `<span class="model-status on" title="${info}">${esc(tr('已加载'))}</span>`;
  }
  return `<span class="model-status" title="${esc(tr('点击预热加载到内存'))}">${esc(tr('未加载'))}</span>`;
}

export async function renderModels() {
  const body = document.getElementById('modelsBody');
  if (!body) return;
  body.innerHTML = '<div class="meta">' + esc(tr('加载中…')) + '</div>';
  try {
    const [r, statusList] = await Promise.all([api('/v1/models/local'), fetchLocalStatus()]);
    const list = (r && r.models) || [];
    if (!list.length) {
      body.innerHTML = '<div class="meta">' + esc(tr('暂无本地模型。请把 .gguf 放到 data/models/，或用下方下载。')) + '</div>';
      return;
    }
    // 按文件名匹配 provider 状态（一个 provider 可能对应多个文件，取第一个）
    const statusMap = {};
    for (const sp of statusList) {
      const fn = sp.model_file || (sp.model_path ? sp.model_path.split(/[\\/]/).pop() : '');
      if (fn && !statusMap[fn]) statusMap[fn] = sp;
    }
    body.innerHTML = list.map((m) => {
      const path = esc(m.path || m.name);
      const isEmbed = /embed/i.test(m.name);
      const st = statusMap[m.name] || null;
      const icon = isEmbed
        ? '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 11.2 17 6.8M7 12.8l10 4.4"/></svg>'
        : '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H9l-5 4z"/><path d="M8 8h8M8 11h5"/></svg>';
      const badge = st ? statusBadge(st) : '';
      const warmBtn = (st && !st.loaded && !st.error)
        ? `<button type="button" class="btn ghost" data-act="modelWarm" data-arg="${esc(st.provider)}">${esc(tr('预热'))}</button>` : '';
      return `<div class="model-card">
        <span class="model-icon${isEmbed ? ' embed' : ''}" title="${isEmbed ? 'Embedding' : 'LLM'}">${icon}</span>
        <div class="model-card-main">
          <div class="model-name" title="${esc(m.name)}">${esc(m.name)} ${badge}</div>
          <div class="model-meta">${fmtSize(m.size)} · ${fmtTime(m.mtime)}</div>
          <div class="model-path" title="${path}">${path}</div>
        </div>
        <div class="model-card-actions">
          ${warmBtn}
          <button type="button" class="btn ghost" data-act="modelCopyPath" data-arg="${path}">${esc(tr('复制路径'))}</button>
          <button type="button" class="btn ghost danger" data-act="modelDelete" data-arg="${esc(m.name)}">${esc(tr('删除'))}</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    body.innerHTML = '<div class="meta">' + esc(tr('读取失败: ') + ((e && (e.text || e.detail || e.message)) || String(e))) + '</div>';
  }
}

export async function modelWarm(el) {
  const provider = el && el.dataset && el.dataset.arg;
  if (!provider) return;
  try {
    await api('/v1/models/local/warm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    toast(tr('预热完成'), 'ok');
    await renderModels();
  } catch (e) {
    toast(tr('预热失败: ') + ((e && (e.text || e.detail || e.message)) || String(e)), 'err');
  }
}

export async function modelDelete(el) {
  const name = el && el.dataset && el.dataset.arg;
  if (!name) return;
  if (!confirm(tr('确定删除模型 {name}？文件不可恢复。').replace('{name}', name))) return;
  try {
    await api('/v1/models/local/' + encodeURIComponent(name), { method: 'DELETE' });
    toast(tr('已删除 {name}').replace('{name}', name), 'ok');
    await renderModels();
  } catch (e) {
    toast(tr('删除失败: ') + ((e && (e.text || e.detail || e.message)) || String(e)), 'err');
  }
}

export async function modelCopyPath(el) {
  const path = el && el.dataset && el.dataset.arg;
  if (!path) return;
  try {
    await navigator.clipboard.writeText(path);
    toast(tr('已复制路径'), 'ok');
  } catch (e) {
    toast(tr('复制失败'), 'err');
  }
}

export function modelPreset(el) {
  const url = el && el.dataset && el.dataset.url;
  if (!url) return;
  const urlEl = document.getElementById('modelDownloadUrl');
  const nameEl = document.getElementById('modelDownloadName');
  if (urlEl) urlEl.value = url;
  if (nameEl) nameEl.value = (el.dataset.name || '');
  const hint = document.getElementById('modelDownloadHint');
  if (hint) hint.textContent = tr('已填入推荐下载地址，点「下载」开始');
}

export async function modelDownload() {
  const urlEl = document.getElementById('modelDownloadUrl');
  const nameEl = document.getElementById('modelDownloadName');
  const hint = document.getElementById('modelDownloadHint');
  const btn = document.getElementById('modelDownloadBtn');
  const url = (urlEl && urlEl.value.trim()) || '';
  const filename = (nameEl && nameEl.value.trim()) || '';
  if (!url) { toast(tr('请输入下载地址'), 'warn'); return; }
  if (btn) btn.disabled = true;
  if (hint) hint.textContent = tr('下载中…');
  try {
    const r = await api('/v1/models/local/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, filename })
    });
    toast(tr('下载完成: {name}').replace('{name}', r.name), 'ok');
    if (urlEl) urlEl.value = '';
    if (nameEl) nameEl.value = '';
    if (hint) hint.textContent = '';
    await renderModels();
  } catch (e) {
    const msg = (e && (e.text || e.detail || e.message)) || String(e);
    if (hint) hint.textContent = tr('下载失败: ') + msg;
    toast(tr('下载失败: ') + msg, 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
}
