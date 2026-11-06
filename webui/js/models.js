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

export async function renderModels() {
  const body = document.getElementById('modelsBody');
  if (!body) return;
  body.innerHTML = '<div class="meta">' + esc(tr('加载中…')) + '</div>';
  try {
    const r = await api('/v1/models/local');
    const list = (r && r.models) || [];
    if (!list.length) {
      body.innerHTML = '<div class="meta">' + esc(tr('暂无本地模型。请把 .gguf 放到 data/models/，或用下方下载。')) + '</div>';
      return;
    }
    body.innerHTML = list.map((m) => {
      const path = esc(m.path || m.name);
      return `<div class="setrow" style="align-items:flex-start;gap:8px;padding:6px 0;border-bottom:1px solid var(--line)">
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(m.name)}">${esc(m.name)}</div>
          <div class="hint">${fmtSize(m.size)} · ${fmtTime(m.mtime)}</div>
          <div class="hint" style="font-family:var(--mono);font-size:11px">${path}</div>
        </div>
        <div style="display:flex;gap:4px;flex-direction:column">
          <button type="button" class="btn ghost" data-act="modelCopyPath" data-arg="${path}">${esc(tr('复制路径'))}</button>
          <button type="button" class="btn ghost danger" data-act="modelDelete" data-arg="${esc(m.name)}">${esc(tr('删除'))}</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    body.innerHTML = '<div class="meta">' + esc(tr('读取失败: ') + ((e && (e.text || e.detail || e.message)) || String(e))) + '</div>';
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
