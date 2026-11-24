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
  const st = String(p.state || '');
  if (st === 'loading') return `<span class="model-status" title="${esc(tr('正在把模型加载到内存'))}">${esc(tr('加载中'))}</span>`;
  if (st === 'generating') return `<span class="model-status on" title="${esc(tr('正在生成回答'))}">${esc(tr('生成中'))}</span>`;
  if (p.loaded) {
    const perf = p.tok_per_second > 0 ? ` · ${p.tok_per_second} tok/s` : '';
    const info = `${esc(tr('运行中'))} · ${p.load_seconds}s${perf} · ${p.calls} ${esc(tr('次调用'))}`;
    return `<span class="model-status on" title="${info}">${esc(tr('运行中'))}</span>`;
  }
  return `<span class="model-status" title="${esc(tr('点击启动加载到内存'))}">${esc(tr('未加载'))}</span>`;
}

export async function renderModels() {
  const body = document.getElementById('modelsBody');
  if (!body) return;
  body.innerHTML = '<div class="meta">' + esc(tr('加载中…')) + '</div>';
  try {
    const [r, statusList] = await Promise.all([api('/v1/models/local'), fetchLocalStatus()]);
    const list = (r && r.models) || [];
    // 按文件名匹配 provider 状态（一个 provider 可能对应多个文件，取第一个）
    const statusMap = {};
    for (const sp of statusList) {
      const fn = sp.model_file || (sp.model_path ? sp.model_path.split(/[\\/]/).pop() : '');
      if (fn && !statusMap[fn]) statusMap[fn] = sp;
    }
    // 运行状态区：即使模型文件不在 data/models/ 里，也能看到并启动/停止
    const providerHtml = statusList.length ? `<div class="model-providers">${
      statusList.map((sp) => {
        const name = esc(sp.model_file || sp.provider || 'local');
        const detail = sp.loaded
          ? `${sp.calls || 0} ${esc(tr('次调用'))}${sp.tok_per_second > 0 ? ` · ${sp.tok_per_second} tok/s` : ''}`
          : esc(tr('点击启动加载到内存'));
        const isBusy = sp.loaded || sp.state === 'loading' || sp.state === 'generating';
        // 两个分支写成字面量 data-act：invariants 会静态核对按钮与 ACTIONS 表
        const ctrl = isBusy
          ? `<button type="button" class="btn ghost" data-act="modelStop" data-arg="${esc(sp.provider)}">${esc(tr('停止'))}</button>`
          : `<button type="button" class="btn ghost" data-act="modelStart" data-arg="${esc(sp.provider)}">${esc(tr('启动'))}</button>`;
        return `<div class="model-provider-row">
          <div class="model-provider-main">
            <div class="model-provider-name">${name} ${statusBadge(sp)}</div>
            <div class="model-provider-detail">${detail}</div>
          </div>
          ${ctrl}
        </div>`;
      }).join('')
    }</div>` : '';
    const kindIcon = (k) => {
      if (k === 'embedding') return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 11.2 17 6.8M7 12.8l10 4.4"/></svg>';
      if (k === 'mmproj') return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="12" height="12" rx="2"/><path d="M15 9l5-3v12l-5-3z"/></svg>';
      if (k === 'rerank') return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6h9M4 12h13M4 18h7"/><path d="M18 15l2 2 3-4"/></svg>';
      return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H9l-5 4z"/><path d="M8 8h8M8 11h5"/></svg>';
    };
    // 本地对话 provider：把库里的文件设为当前模型（缺 provider 时用 local）
    const localProvider = (statusList[0] && statusList[0].provider) || 'local';
    const org = (r && (r.needs_organize || 0)) || 0;
    const broken = (r && (r.broken || 0)) || 0;
    const banner = (org || broken) ? `<div class="model-banner">
      <span>${org ? esc(tr('{n} 个模型不在规范目录（建议整理）').replace('{n}', org)) : ''}
      ${org && broken ? ' · ' : ''}
      ${broken ? esc(tr('{n} 个文件有问题').replace('{n}', broken)) : ''}</span>
      <button type="button" class="btn ghost" data-act="modelOrganize">${esc(tr('整理模型库'))}</button>
    </div>` : '';
    body.innerHTML = providerHtml + banner + (list.length ? list.map((m) => {
      const path = esc(m.path || m.name);
      const kind = m.kind || 'chat';
      const kindLabel = m.kind_label || (kind === 'embedding' ? 'Embedding' : 'LLM');
      const st = statusMap[m.name] || null;
      const badge = st ? statusBadge(st) : '';
      const warn = (m.valid === false && (m.issues || []).length)
        ? `<span class="model-status bad" title="${esc((m.issues || []).join('；'))}">${esc(tr('文件异常'))}</span>` : '';
      const moveHint = m.needs_move
        ? `<div class="model-meta warnline">${esc(tr('应放在 {p}').replace('{p}', m.canonical || ''))}</div>` : '';
      const useBtn = (kind === 'chat')
        ? `<button type="button" class="btn ghost" data-act="modelUse" data-arg="${esc(m.path)}" data-provider="${esc(localProvider)}">${esc(tr('设为当前'))}</button>` : '';
      return `<div class="model-card">
        <span class="model-icon${kind === 'embedding' ? ' embed' : ''}" title="${esc(kindLabel)} · ${esc(m.kind_reason || '')}">${kindIcon(kind)}</span>
        <div class="model-card-main">
          <div class="model-name" title="${esc(m.name)}">${esc(m.name)} ${badge} ${warn}
            <span class="model-kind">${esc(kindLabel)}</span></div>
          <div class="model-meta">${fmtSize(m.size)} · ${fmtTime(m.mtime)}</div>
          <div class="model-path" title="${path}">${path}</div>
          ${moveHint}
        </div>
        <div class="model-card-actions">
          ${useBtn}
          <button type="button" class="btn ghost" data-act="modelCopyPath" data-arg="${esc(m.full || path)}">${esc(tr('复制路径'))}</button>
          <button type="button" class="btn ghost danger" data-act="modelDelete" data-arg="${esc(m.path || m.name)}">${esc(tr('删除'))}</button>
        </div>
      </div>`;
    }).join('') : '<div class="meta">' + esc(tr('暂无本地模型。请把 .gguf 放到 data/models/，或用下方下载。')) + '</div>');
  } catch (e) {
    body.innerHTML = '<div class="meta">' + esc(tr('读取失败: ') + ((e && (e.text || e.detail || e.message)) || String(e))) + '</div>';
  }
}

async function controlModel(el, action) {
  const provider = el && el.dataset && el.dataset.arg;
  if (!provider) return;
  const url = '/v1/models/local/' + encodeURIComponent(provider) + '/' + action;
  const sendAction = (body) => api(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body
  });
  try {
    let r = await sendAction(action === 'stop' ? JSON.stringify({ force: false, timeout: 5 }) : '{}');
    // 模型正忙（加载中 / 生成中）时普通停止会超时：给一次"强行停止"的机会
    if (action === 'stop' && r && r.ok === false) {
      const st = (r.stats && r.stats.state) || '';
      const busy = st === 'loading' || st === 'generating' || /超时/.test(r.error || '');
      if (busy && confirm(tr('模型正忙（{s}），要强行停止吗？加载中的模型会在就绪后立刻释放内存。')
        .replace('{s}', st || tr('占用中')))) {
        r = await sendAction(JSON.stringify({ force: true, timeout: 5 }));
      }
    }
    if (r && r.ok === false) {
      toast(tr(action === 'start' ? '启动失败: ' : '停止失败: ') + (r.error || ''), 'err');
    } else if (action === 'stop') {
      const st = (r && r.stopped) || {};
      toast(st.loading ? tr('已中止加载：模型不会留在内存里')
        : tr('已停止 {name}').replace('{name}', provider), 'ok');
    } else {
      toast(tr('已启动 {name}').replace('{name}', provider), 'ok');
    }
    await renderModels();
  } catch (e) {
    toast(tr(action === 'start' ? '启动失败: ' : '停止失败: ')
      + ((e && (e.text || e.detail || e.message)) || String(e)), 'err');
  }
}

export async function modelStopAll() {
  if (!confirm(tr('强行停止所有本地模型并释放内存？正在生成的请求会立即结束。'))) return;
  try {
    const r = await api('/v1/models/local/stop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: true, timeout: 5 })
    });
    const failed = (r.providers || []).filter((x) => !x.ok);
    toast(failed.length
      ? tr('已停止 {n} 个，{e} 个失败').replace('{n}', r.stopped || 0).replace('{e}', failed.length)
      : tr('已强行停止 {n} 个本地模型').replace('{n}', r.stopped || 0), failed.length ? 'err' : 'ok');
    await renderModels();
  } catch (e) {
    toast(tr('停止失败: ') + ((e && (e.text || e.detail || e.message)) || String(e)), 'err');
  }
}

export async function modelStart(el) {
  return controlModel(el, 'start');
}

export async function modelStop(el) {
  return controlModel(el, 'stop');
}

export async function modelUse(el) {
  const rel = el && el.dataset && el.dataset.arg;
  const provider = (el && el.dataset && el.dataset.provider) || 'local';
  if (!rel) return;
  try {
    const r = await api('/v1/models/local/' + encodeURIComponent(provider) + '/use', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: rel })
    });
    toast(tr('已把 {name} 设为 {p} 的当前模型').replace('{name}', r.name || rel)
      .replace('{p}', provider), 'ok');
    await renderModels();
  } catch (e) {
    toast(tr('切换失败: ') + ((e && (e.text || e.detail || e.message)) || String(e)), 'err');
  }
}

export async function modelOrganize() {
  try {
    const plan = await api('/v1/models/local/scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apply: false })
    });
    const moves = (plan.actions || []).filter((a) => a.action === 'move');
    if (!moves.length) { toast(tr('模型库已经是规范目录，无需整理'), 'ok'); return; }
    const preview = moves.slice(0, 6)
      .map((a) => `${a.from} → ${a.to}`).join('\n');
    const more = moves.length > 6 ? `\n… ${tr('还有 {n} 个').replace('{n}', moves.length - 6)}` : '';
    if (!confirm(tr('将按类别移动 {n} 个文件：\n{p}{more}\n\n移动后 provider 的旧路径会失效（启动时会自动按文件名找回）。继续？')
      .replace('{n}', moves.length).replace('{p}', preview).replace('{more}', more))) return;
    const done = await api('/v1/models/local/scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apply: true })
    });
    const failed = (done.actions || []).filter((a) => a.action === 'error');
    if (failed.length) {
      toast(tr('已移动 {n} 个，{e} 个失败: ').replace('{n}', done.moved || 0)
        .replace('{e}', failed.length) + (failed[0].error || ''), 'err');
    } else {
      toast(tr('已按类别整理 {n} 个模型').replace('{n}', done.moved || 0), 'ok');
    }
    await renderModels();
  } catch (e) {
    toast(tr('整理失败: ') + ((e && (e.text || e.detail || e.message)) || String(e)), 'err');
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
    toast(tr('下载完成: {name}（{kind}）').replace('{name}', r.name)
      .replace('{kind}', r.kind_label || r.kind || ''), 'ok');
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
