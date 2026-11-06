// kb.js — 知识库管理：导入 Word/PDF/Excel/PPT/HTML/文本/URL，列出/删除文档，检索测试。
//
// 后端全部走 kb 插件的接口（/v1/plugins/kb/api/*），与 CLI、teams-bot 用的是同一份索引。
// 大文件走 Base64 POST（浏览器拿不到本地真实路径，文件选择器的 File 对象只能读内容）。

// api / esc / toast / tr 是 globals.js（经典脚本）挂在 window 上的全局，
// 不能 import：globals.js 不是 ES 模块，import 它会让整个模块图加载失败。
// 项目里没有 jQuery 风格的 $，元素统一用 document.getElementById。

const $id = (id) => document.getElementById(String(id || '').replace(/^#/, ''));

const BASE = '/v1/plugins/kb/api';

function fmtBytes(n) {
  const b = Number(n) || 0;
  if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB';
  if (b >= 1024) return (b / 1024).toFixed(0) + ' KB';
  return b + ' B';
}

async function kbCall(path, body, method) {
  return api(BASE + path, {
    method: method || (body ? 'POST' : 'GET'),
    body: body ? JSON.stringify(body) : undefined,
  });
}

function fileToB64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => {
      const s = String(fr.result || '');
      resolve(s.includes(',') ? s.slice(s.indexOf(',') + 1) : s);
    };
    fr.onerror = () => reject(new Error('读取文件失败'));
    fr.readAsDataURL(file);
  });
}

async function importOne(file) {
  const b64 = await fileToB64(file);
  const res = await kbCall('/import', { name: file.name, data_b64: b64 });
  if (res && res.ok === false) throw new Error(res.error || '导入失败');
  return res;
}

export async function kbRefresh() {
  const stats = $id('#kbStats');
  try {
    const st = await kbCall('/stats');
    if (stats) {
      const docs = st.documents ?? 0;
      const chunks = st.chunks ?? 0;
      const chars = st.chars ?? 0;
      stats.textContent =
        `${tr('目录')}: ${st.dir || '—'} · ${tr('文档')} ${docs} · ${tr('分块')} ${chunks} · ${chars} ${tr('字符')}`
        + (st.fts ? ' · FTS5' : ' · ' + tr('无全文索引'));
    }
  } catch (e) {
    if (stats) stats.textContent = tr('读取统计失败: ') + (e.text || e);
  }
  const box = $id('#kbDocs');
  if (box) box.innerHTML = `<div class="meta">${esc(tr('加载中…'))}</div>`;
  try {
    const d = await kbCall('/docs');
    const docs = d.documents || [];
    const cnt = $id('#kbDocCount');
    if (cnt) cnt.textContent = `(${docs.length})`;
    if (!box) return;
    if (!docs.length) {
      box.innerHTML = `<div class="meta">${esc(tr('还没有文档：拖文件进来，或用上面的 URL / 粘贴文本'))}</div>`;
      return;
    }
    box.innerHTML = docs.map((x) => `
      <div class="kbrow" style="display:flex;gap:8px;align-items:center;padding:5px 2px;border-bottom:1px solid var(--line)">
        <code style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.path)}</code>
        <span class="hint">${x.chunks} ${esc(tr('块'))} · ${fmtBytes(x.chars)}</span>
        <button type="button" class="btn ghost" data-act="kbDelete" data-arg="${esc(x.path)}" title="${esc(tr('删除'))}">✕</button>
      </div>`).join('');
  } catch (e) {
    if (box) box.innerHTML = `<div class="meta">${esc(tr('读取文档列表失败: ') + (e.text || e))}</div>`;
  }
}

export async function kbPick() {
  const input = $id('#kbFile');
  if (!input) return;
  input.value = '';
  input.click();
}

async function importFiles(files) {
  const list = Array.from(files || []).filter(Boolean);
  if (!list.length) return;
  let ok = 0;
  const failed = [];
  for (const f of list) {
    try {
      await importOne(f);
      ok += 1;
    } catch (e) {
      failed.push(`${f.name}: ${e.text || e.message || e}`);
    }
  }
  if (ok) toast(tr('已导入 ') + ok + tr(' 个文件'), 'ok');
  if (failed.length) toast(tr('导入失败：') + failed.join('；'), 'err', 8000);
  await kbRefresh();
}

export async function kbImportUrl() {
  const input = $id('#kbUrl');
  const url = (input && input.value || '').trim();
  if (!url) { toast(tr('先填 URL'), 'err'); return; }
  try {
    const res = await kbCall('/import', { url });
    if (res && res.ok === false) throw new Error(res.error || '导入失败');
    toast(tr('已导入：') + (res.title || url), 'ok');
    if (input) input.value = '';
  } catch (e) {
    toast(tr('导入 URL 失败：') + (e.text || e.message || e), 'err', 8000);
  }
  await kbRefresh();
}

export async function kbImportText() {
  const body = ($id('#kbTextBody') && $id('#kbTextBody').value || '').trim();
  const title = ($id('#kbTitle') && $id('#kbTitle').value || '').trim();
  if (!body) { toast(tr('先粘贴文本'), 'err'); return; }
  try {
    const res = await kbCall('/import', { text: body, title: title || undefined });
    if (res && res.ok === false) throw new Error(res.error || '导入失败');
    toast(tr('已导入文本：') + (res.title || ''), 'ok');
    if ($id('#kbTextBody')) $id('#kbTextBody').value = '';
    if ($id('#kbTitle')) $id('#kbTitle').value = '';
  } catch (e) {
    toast(tr('导入文本失败：') + (e.text || e.message || e), 'err', 8000);
  }
  await kbRefresh();
}

export async function kbInbox() {
  try {
    const d = await kbCall('/inbox');
    if (!d.count) {
      toast(tr('收件箱是空的：把文件放进 ') + (d.dir || 'data/kb/inbox/'), 'ok', 6000);
      return;
    }
    const res = await kbCall('/inbox/scan', {});
    const bad = (res.items || []).filter((x) => !x.ok);
    toast(tr('收件箱导入 ') + (res.imported || 0) + tr(' 个') +
      (bad.length ? '；失败 ' + bad.map((x) => x.name).join('、') : ''), bad.length ? 'err' : 'ok', 8000);
  } catch (e) {
    toast(tr('收件箱处理失败：') + (e.text || e.message || e), 'err', 8000);
  }
  await kbRefresh();
}

export async function kbReindex() {
  try {
    const res = await kbCall('/reindex', { force: false });
    toast(tr('索引已重建：') + JSON.stringify(res && res.stats || res), 'ok', 6000);
  } catch (e) {
    toast(tr('重建失败：') + (e.text || e.message || e), 'err', 8000);
  }
  await kbRefresh();
}

export async function kbDelete(path) {
  if (!path) return;
  try {
    const res = await kbCall('/delete', { path });
    if (res && res.ok === false) throw new Error(res.error || '删除失败');
    toast(tr('已删除：') + path, 'ok');
  } catch (e) {
    toast(tr('删除失败：') + (e.text || e.message || e), 'err', 8000);
  }
  await kbRefresh();
}

export async function kbSearch() {
  const q = ($id('#kbQ') && $id('#kbQ').value || '').trim();
  const box = $id('#kbHits');
  if (!q) { toast(tr('先输入问题'), 'err'); return; }
  if (box) box.innerHTML = `<div class="meta">${esc(tr('检索中…'))}</div>`;
  try {
    const d = await kbCall('/search?q=' + encodeURIComponent(q) + '&n=5');
    const hits = d.hits || [];
    if (!box) return;
    if (!hits.length) {
      box.innerHTML = `<div class="meta">${esc(tr('没有命中（后端: ') + (d.backend || '?') + '）')}</div>`;
      return;
    }
    box.innerHTML = `<div class="meta">${esc(tr('后端: '))}${esc(d.backend || '')} · ${hits.length} ${esc(tr('条'))}</div>`
      + hits.map((h) => `
        <div style="padding:6px 2px;border-bottom:1px solid var(--line)">
          <div><b>${esc(h.cite || h.title || '')}</b>
            <span class="hint">${(Number(h.score) || 0).toFixed(2)}${h.via ? ' · ' + esc(h.via) : ''}</span></div>
          <div class="meta" style="white-space:pre-wrap;max-height:88px;overflow:hidden">${esc(String(h.text || '').slice(0, 260))}</div>
        </div>`).join('');
  } catch (e) {
    if (box) box.innerHTML = `<div class="meta">${esc(tr('检索失败: ') + (e.text || e.message || e))}</div>`;
  }
}

export function kbClear() {
  const q = $id('#kbQ');
  const box = $id('#kbHits');
  if (q) q.value = '';
  if (box) box.innerHTML = '';
}

function wireOnce() {
  const input = $id('#kbFile');
  if (input && !input.dataset.kbWired) {
    input.dataset.kbWired = '1';
    input.addEventListener('change', () => importFiles(input.files));
  }
  const drop = $id('#kbDrop');
  if (drop && !drop.dataset.kbWired) {
    drop.dataset.kbWired = '1';
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.style.borderColor = 'var(--accent, #38bdf8)';
    }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => {
      drop.style.borderColor = 'var(--line)';
    }));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files) importFiles(e.dataTransfer.files);
    });
  }
  const q = $id('#kbQ');
  if (q && !q.dataset.kbWired) {
    q.dataset.kbWired = '1';
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); kbSearch(); } });
  }
  const saveBtn = $id('#kbSettingsSave');
  if (saveBtn && !saveBtn.dataset.kbWired) {
    saveBtn.dataset.kbWired = '1';
    saveBtn.addEventListener('click', kbSaveSettings);
  }
  const clearBtn = $id('#kbClear');
  if (clearBtn && !clearBtn.dataset.kbWired) {
    clearBtn.dataset.kbWired = '1';
    clearBtn.addEventListener('click', kbClear);
  }
}

function fmtErr(e) {
  if (!e) return String(e);
  if (typeof e.text === 'string') return e.text;
  if (typeof e.detail === 'string') return e.detail;
  if (typeof e.message === 'string') return e.message;
  try { return JSON.stringify(e); } catch (_) { return String(e); }
}

async function kbLoadSettings() {
  const backend = $id('#kbEmbBackend');
  const model = $id('#kbEmbModel');
  const ocr = $id('#kbOcrEngine');
  const hint = $id('#kbSettingsHint');
  try {
    const d = await api('/v1/config');
    const vals = (d && d.values) || {};
    if (backend) backend.value = String(vals['kb.embedding.backend'] || 'fastembed');
    if (model) model.value = String(vals['kb.embedding.model'] || '');
    if (ocr) ocr.value = String(vals['kb.ocr.engine'] || 'auto');
    if (hint) {
      const eff = await api('/v1/plugins/kb/api/ocr');
      hint.textContent = (tr('Embedding') + ': ' + (vals['kb.embedding.backend'] || 'fastembed')
        + (vals['kb.embedding.model'] ? ' / ' + vals['kb.embedding.model'] : '')
        + ' · OCR: ' + ((eff && eff.effective) || vals['kb.ocr.engine'] || 'auto')
        + ' · ' + tr('保存后 Embedding 改动需重建索引/重启生效'));
    }
  } catch (e) {
    if (hint) hint.textContent = tr('读取设置失败: ') + fmtErr(e);
  }
}

async function kbSaveSettings() {
  const backend = $id('#kbEmbBackend');
  const model = $id('#kbEmbModel');
  const ocr = $id('#kbOcrEngine');
  const hint = $id('#kbSettingsHint');
  const payload = {
    values: {
      'kb.embedding.backend': (backend && backend.value) || 'fastembed',
      'kb.embedding.model': (model && model.value) || '',
      'kb.ocr.engine': (ocr && ocr.value) || 'auto',
    },
    persist: true,
  };
  try {
    await api('/v1/config', { method: 'POST', body: JSON.stringify(payload) });
    toast(tr('知识库设置已保存'), 'ok');
  } catch (e) {
    const msg = fmtErr(e);
    toast(tr('保存失败: ') + msg, 'err', 8000);
    if (hint) hint.textContent = tr('保存失败: ') + msg;
    return;
  }
  // OCR 切换单独尝试，失败不影响 config 已保存的事实
  try {
    await api('/v1/plugins/kb/api/ocr', {
      method: 'POST',
      body: JSON.stringify({ engine: payload.values['kb.ocr.engine'] }),
    });
  } catch (e) {
    toast(tr('OCR 切换失败: ') + fmtErr(e), 'warn', 6000);
  }
  await kbLoadSettings();
}

export async function openKb() {
  const mask = $id('#kbMask');
  if (!mask) return;
  mask.classList.add('open');
  wireOnce();
  await kbRefresh();
  await kbLoadSettings();
}

export function closeKb() {
  const mask = $id('#kbMask');
  if (mask) mask.classList.remove('open');
}
