import {request, ready} from './js/bridge.js';

// ─────────────────────────────────────────────────────────────────
// 公開版ではAI対話（チャット）を無効にしている。
//
// 静的サイトに置いたファイルは誰でもダウンロードできるので、APIキーを
// 埋め込むと第三者にあなたのキーで課金されてしまう。そのため公開URLでは
// 対話だけを止め、分析・設問生成・レポートはそのまま使えるようにしている。
//
// 有効にする手順（中継サーバーを立てて鍵をサーバー側に置く）は
//   py/llm_adapter.py の冒頭コメント
// に全部書いてある。有効化するときは下を true にして、あわせて
// llm_adapter.py の PROXY_ENDPOINT と complete() のコメントを外すこと。
// ─────────────────────────────────────────────────────────────────
const CHAT_ENABLED = false;
const CHAT_DISABLED_MESSAGE = '公開版ではAI対話を停止しています。分析・設問生成はこのまま使えます。';

const app = document.querySelector('#app');
const toastEl = document.querySelector('#toast');

const icons = {
  back: '<svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>',
  arrow: '<svg viewBox="0 0 24 24"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4m-4 4 4-4 4 4"/><path d="M4 15v4h16v-4"/></svg>',
  chart: '<svg viewBox="0 0 24 24"><path d="M4 19V9m5 10V5m5 14v-7m5 7V3"/><path d="M2 19h20"/></svg>',
  form: '<svg viewBox="0 0 24 24"><path d="M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg>',
  alert: '<svg viewBox="0 0 24 24"><path d="M12 3 2 21h20L12 3Z"/><path d="M12 9v5m0 3h.01"/></svg>',
  wand: '<svg viewBox="0 0 24 24"><path d="m15 4 5 5L9 20l-5-5L15 4Z"/><path d="m14 5 5 5M6 4v3M4.5 5.5h3M19 16v4m-2-2h4"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16m-10 4v6m4-6v6M9 7V4h6v3m-9 0 1 14h10l1-14"/></svg>',
};

const state = {
  view: 'home',
  busy: false,
  goalSending: false,
  dataset: null,
  analysisQuestion: '',
  plan: null,
  result: null,
  questionnaire: null,
  questionnaireForm: null,
  hypothesisSuggestion: null,
  selectedHypothesis: null,
  qualityDirty: false,
  projects: [],
  projectsLoaded: false,
  project: null,
  currentProjectId: null,
  selectedCandidateIds: [],
  researchPlan: null,
  atamiDr3: null,
  goalDialogue: null,
  researchBrief: null,
  conversations: [],
  conversationsLoaded: false,
  currentConversationId: null,
  currentMaterialId: null,
  conversationDataReady: true,
  conversationLoadToken: 0,
  conversationLoading: false,
  atamiComparisonQuestion: 'barriers',
  atamiComparisonView: 'grouped',
  atamiComparisonFilters: {
    A: {university:['shibaura'], academic_status:['undergraduate'], gender:[], international:[]},
    B: {university:['other'], academic_status:['undergraduate'], gender:[], international:[]},
  },
  workspace: null,
  workspaceLoaded: false,
  projectEditing: false,
  sessionId: null,
  session: null,
  sessionLoading: false,
  sessionBusy: false,
  uiStateSaveTimer: null,
  uiStateSaveStatus: 'idle',
  questionnaireImport: null,
  questionnaireImportBusy: false,
};

function esc(value = '') {
  return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

function normalizeMessageText(value = '') {
  return String(value)
    .replace(/[¥￥\\]\s*r\s*[¥￥\\]\s*n/g, '\n')
    .replace(/[¥￥\\]\s*n/g, '\n')
    .replace(/\\r\\n/g, '\n')
    .replace(/\\n/g, '\n')
    .replace(/\r\n/g, '\n')
    .replace(/\s*(?:[#＃]?\s*n\s*)?■\s*/g, '\n- ')
    .replace(/\n\s*[-・*]\s*/g, '\n- ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function renderMessageBody(value = '') {
  const text = normalizeMessageText(value);
  if (!text) return '<div class="message-body"><p></p></div>';
  const blocks = text.split(/\n{2,}/).map(block => block.trim()).filter(Boolean);
  const html = blocks.map(block => {
    const lines = block.split('\n').map(line => line.trim()).filter(Boolean);
    const isBullet = line => /^[-*・]\s+/.test(line);
    const isNumbered = line => /^\d+[.)]\s+/.test(line);
    const renderList = (items, ordered = false) => {
      const tag = ordered ? 'ol' : 'ul';
      const marker = ordered ? /^\d+[.)]\s+/ : /^[-*・]\s+/;
      return `<${tag}>${items.map(line => `<li>${esc(line.replace(marker, ''))}</li>`).join('')}</${tag}>`;
    };
    if (lines.every(isBullet)) return renderList(lines);
    if (lines.every(isNumbered)) return renderList(lines, true);
    if (lines.length > 1 && /[:：]$/.test(lines[0])) {
      const items = lines.slice(1);
      if (items.every(isBullet)) return `<p class="message-section-title">${esc(lines[0])}</p>${renderList(items)}`;
      if (items.every(isNumbered)) return `<p class="message-section-title">${esc(lines[0])}</p>${renderList(items, true)}`;
    }
    return lines.map((line, index) => `<p${index === 0 && /[:：]$/.test(line) ? ' class="message-section-title"' : ''}>${esc(line)}</p>`).join('');
  }).join('');
  return `<div class="message-body">${html}</div>`;
}

function renderChatMessage(message, textKey = 'content') {
  if (message.role === 'system_event') {
    return `<div class="message system-event"><small>${esc(message[textKey] || message.content || '')}</small></div>`;
  }
  const isAssistant = message.role === 'assistant';
  const usedFallback = message.structured?.model?.usedFallback;
  const label = isAssistant ? (usedFallback ? 'RULE' : 'GUIDE') : 'YOU';
  const fallbackNote = usedFallback ? '<small class="message-note">Claudeを利用できなかったため、ルールベースで整理しました。</small>' : '';
  return `<div class="message ${message.role}${usedFallback ? ' fallback' : ''}"><span>${label}</span>${renderMessageBody(message[textKey] || '')}${fallbackNote}</div>`;
}

async function api(path, body, method) {
  // 公開版はサーバーが無いので、fetch の代わりにブラウザ内の Python を呼ぶ。
  // 返す形（data / error / status）はローカル版のサーバーと同じ。
  const httpMethod = method || (body ? 'POST' : 'GET');
  const {status, data} = await request(httpMethod, path, body);
  if (status >= 400) {
    const error = new Error(data.error || '処理に失敗しました');
    error.data = data;
    error.status = status;
    throw error;
  }
  return data;
}

function toast(message, error = false) {
  toastEl.textContent = message;
  toastEl.className = `toast${error ? ' error' : ''}`;
  toastEl.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { toastEl.hidden = true; }, 3600);
}

function setView(view) {
  state.view = view;
  render();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function render() {
  if (state.view === 'session') renderSessionShell();
  else if (state.view === 'analysis') renderAnalysis();
  else if (state.view === 'questionnaire') renderQuestionnaire();
  else if (state.view === 'questionnaire-import') renderQuestionnaireImport();
  else if (state.view === 'project') renderProject();
  else if (state.view === 'atami-dr3') renderAtamiDr3();
  else if (state.view === 'goal-chat') renderGoalChat();
  else if (state.view === 'home-legacy') renderLegacyHome();
  else renderWorkspaceHome();
}

// --- v3: Survey Insight Workspace（一導線ホーム＋分析セッション） -------------

function statusLabel(status) {
  return {consulting: '相談中', question_confirmed: '問い確定', analyzed: '分析済み', needs_validation: '次の確認あり'}[status] || status;
}

function llmStatusLabel(llm = {}) {
  if (llm.connected) return `AI接続中：${llm.model || llm.provider}`;
  if (llm.reason === 'session_limit') return 'Claude一時利用上限';
  if (llm.reason === 'auth_required') return 'Claudeログイン必要';
  if (llm.reason === 'cli_not_found') return 'Claude CLI未検出';
  if (llm.reason === 'insufficient_quota') return 'OpenAI利用枠不足';
  if (llm.reason === 'missing_key') return 'AIキー未設定';
  if (llm.reason === 'unsupported_provider') return 'AI設定エラー';
  if (llm.configured) return `${llm.provider === 'openai' ? 'OpenAI' : llm.provider === 'claude_cli' ? 'Claude' : 'AI'}接続エラー`;
  return 'AI未接続';
}

function renderLlmNotice(llm = {}) {
  if (llm.connected) return '';
  // 公開版は「未接続」ではなく「意図的に停止」なので、文言を差し替える
  if (!CHAT_ENABLED || llm.reason === 'public_build') {
    return `<div class="required-data context-warning"><strong>この公開版ではAI対話を停止しています</strong><span>${esc(llm.message || CHAT_DISABLED_MESSAGE)}有効化の手順はリポジトリの <code>py/llm_adapter.py</code> 冒頭に書いてあります（中継サーバーを立てて、APIキーはそちら側に置きます）。</span></div>`;
  }
  let title = 'AIが接続されていません';
  let message = 'APIキーを設定すると、会話内容に応じて問いを整理できます。現在はルールベースで続行します。';
  if (llm.reason === 'session_limit') {
    title = 'Claudeのセッション上限に達しています';
    message = 'Claude CLIとの連携は設定済みです。利用枠のリセット後、次の送信から自動で再試行します。';
  } else if (llm.reason === 'auth_required') {
    title = 'Claude CLIへのログインが必要です';
    message = 'ターミナルでclaudeを開いてログインすると、この画面から利用できるようになります。';
  } else if (llm.reason === 'cli_not_found') {
    title = 'Claude CLIが見つかりません';
    message = 'Claude Codeをインストールするか、SURVEY_CLAUDE_CLI_PATHで実行ファイルを指定してください。';
  } else if (llm.reason === 'insufficient_quota') {
    title = 'OpenAI APIの利用枠がありません';
    message = 'キーは認識されています。OpenAI APIの請求設定・利用上限を確認してください。設定後は次の送信で自動再試行します。';
  } else if (llm.reason === 'connection_error') {
    title = `${llm.provider === 'claude_cli' ? 'Claude CLI' : 'AI'}へ接続できませんでした`;
    message = '通信状態を確認してください。入力内容は保存され、次の送信で再試行できます。';
  } else if (llm.reason === 'unsupported_provider') {
    title = '対応していないAI設定です';
    message = 'SURVEY_LLM_PROVIDERにはclaude_cli、openai、anthropicのいずれかを指定してください。';
  }
  return `<div class="required-data context-warning llm-warning"><strong>${esc(title)}</strong><span>${esc(message)}</span></div>`;
}

async function loadWorkspace() {
  state.workspaceLoaded = false;
  if (state.view === 'home') render();
  try {
    state.workspace = await api('/api/workspace');
  } catch (error) {
    state.workspace = {project: {id: '', title: '', partner: '', background: '', materials: []}, recentSessions: [], llm: {connected: false, provider: 'disabled', model: ''}};
    toast(error.message, true);
  } finally {
    state.workspaceLoaded = true;
    if (state.view === 'home') render();
  }
}

function goHome() {
  state.sessionId = null;
  state.session = null;
  state.projectEditing = false;
  setView('home');
  loadWorkspace();
}

function renderWorkspaceHome() {
  if (!state.workspaceLoaded) {
    app.innerHTML = `<section class="panel skeleton-panel"><div class="skeleton-line w40"></div><div class="skeleton-line w80"></div><div class="skeleton-line w60"></div></section>`;
    return;
  }
  const ws = state.workspace;
  const project = ws.project;
  const questionnaires = (project.materials || []).filter(material => material.kind === 'アンケート設問票');
  app.innerHTML = `
    <section class="workspace-hero">
      <div class="workspace-hero-head">
        ${state.projectEditing ? `
          <div class="field-edit">
            <p class="eyebrow">PROJECT</p>
            <input class="text-input" id="editTitle" value="${esc(project.title)}" placeholder="プロジェクト名">
            <input class="text-input" id="editPartner" value="${esc(project.partner)}" placeholder="連携先　例：熱海DMO">
            <textarea class="text-area short" id="editBackground" placeholder="背景・上位目的">${esc(project.background)}</textarea>
            <div class="button-row"><button class="button button-secondary" id="cancelProjectMeta" type="button">キャンセル</button><button class="button button-primary" id="saveProjectMeta" type="button">保存</button></div>
          </div>
        ` : `
          <div><p class="eyebrow">PROJECT</p><h1>${esc(project.title || '熱海DMO × 学生PBL')}</h1><p class="workspace-partner">${project.partner ? `連携先：${esc(project.partner)}` : '連携先は未設定です'}</p><p class="workspace-background">${esc(project.background) || '背景・上位目的は未設定です'}</p><button class="link-button" id="editProjectMeta" type="button">プロジェクト概要を編集</button></div>
        `}
        <span class="privacy-pill ${ws.llm.connected ? 'ok' : 'warn'}">${esc(llmStatusLabel(ws.llm))}</span>
      </div>
      <button class="button button-primary button-large" id="startConsult" type="button" ${state.sessionBusy ? 'disabled' : ''}>何を明らかにしたいか相談する ${icons.arrow}</button>
      ${!CHAT_ENABLED ? `<button class="button button-secondary button-large" id="startGeneralAnalysis" type="button">${icons.chart} どのアンケートでも分析する（汎用）</button>
      <p class="hint-inline">上の「相談する」は熱海DMOの実データ専用の固定分析です。手元のアンケートを試すなら汎用のほうを使ってください。</p>` : ''}
    </section>
    <section class="questionnaire-materials">
      <div class="questionnaire-materials-head">
        <div><p class="eyebrow">QUESTIONNAIRE</p><h2>アンケート設問票</h2><p>実施した設問票を添付し、読み取った内容を原本と照合して保存します。</p></div>
        <label class="button button-secondary" for="questionnaireFile">${icons.upload} 設問票を添付
          <input id="questionnaireFile" type="file" accept=".xlsx,.xlsm,.csv,.docx,.pdf,.txt" hidden>
        </label>
      </div>
      <div class="questionnaire-material-list">
        ${questionnaires.length ? questionnaires.map(material => `
          <div class="questionnaire-material-row">
            <span class="questionnaire-file-icon">${icons.form}</span>
            <span><strong>${esc(material.title)}</strong><small>${material.summary?.questionCount || 0}問・原本照合済み</small></span>
            <span class="verified-badge">${icons.check} 確認済み</span>
          </div>`).join('') : '<div class="questionnaire-empty"><strong>設問票はまだ登録されていません</strong><span>Excel、CSV、Word、PDF、TXTに対応しています。</span></div>'}
      </div>
    </section>
    <section class="recent-sessions">
      <div class="recent-sessions-head"><p class="eyebrow">RECENT SESSIONS</p><h2>最近の分析セッション</h2></div>
      <div class="session-list">${ws.recentSessions.length ? ws.recentSessions.map(s => `
        <button class="project-row" data-session-id="${esc(s.id)}" type="button">
          <span><strong>${esc(s.title)}</strong><small>${statusLabel(s.status)}・${formatChatTime(s.updatedAt)}</small></span>
          <span class="session-row-action">${s.status === 'analyzed' ? '分析を見る' : '続きから'} ${icons.arrow}</span>
        </button>`).join('') : '<div class="empty">まだセッションはありません</div>'}</div>
    </section>`;
  document.querySelector('#startConsult').onclick = createSessionAndOpen;
  // 汎用分析（LLM不要・どのアンケートCSVでも動く）。公開版の主導線。
  const generalAnalysis = document.querySelector('#startGeneralAnalysis');
  if (generalAnalysis) generalAnalysis.onclick = () => { state.dataset = null; state.plan = null; state.result = null; setView('analysis'); };
  document.querySelector('#questionnaireFile')?.addEventListener('change', event => startQuestionnaireImport(event.target.files[0]));
  document.querySelectorAll('[data-session-id]').forEach(button => button.onclick = () => openSession(button.dataset.sessionId));
  document.querySelector('#editProjectMeta')?.addEventListener('click', () => { state.projectEditing = true; render(); });
  document.querySelector('#cancelProjectMeta')?.addEventListener('click', () => { state.projectEditing = false; render(); });
  document.querySelector('#saveProjectMeta')?.addEventListener('click', saveProjectMeta);
}

async function startQuestionnaireImport(file) {
  if (!file || state.questionnaireImportBusy) return;
  state.questionnaireImportBusy = true;
  try {
    const rawData = await fileToBase64(file);
    const data = await api(`/api/projects/${state.workspace.project.id}/questionnaires/parse`, {
      name: file.name,
      data: rawData,
    });
    state.questionnaireImport = {...data.draft, rawData, verified: false};
    state.questionnaireImportBusy = false;
    setView('questionnaire-import');
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.questionnaireImportBusy = false;
    if (state.view === 'questionnaire-import') renderQuestionnaireImport();
  }
}

function collectQuestionnaireDraft() {
  const draft = state.questionnaireImport;
  if (!draft) return;
  const rows = [...document.querySelectorAll('[data-question-row]')];
  if (!rows.length) return;
  draft.questions = rows.map((row, index) => ({
    id: row.querySelector('[data-field="id"]').value.trim() || `Q${index + 1}`,
    text: row.querySelector('[data-field="text"]').value.trim(),
    type: row.querySelector('[data-field="type"]').value,
    options: row.querySelector('[data-field="options"]').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
    source: draft.questions[index]?.source || '',
  }));
  draft.verified = Boolean(document.querySelector('#questionnaireVerified')?.checked);
}

function renderQuestionnaireImport() {
  const draft = state.questionnaireImport;
  if (!draft) {
    goHome();
    return;
  }
  const warningItems = draft.warnings || [];
  app.innerHTML = `
    <div class="page-head questionnaire-review-head">
      <div><p class="eyebrow">READING CHECK</p><h1>設問票の読み取り確認</h1><p>${esc(draft.filename)}・読み取った内容を原本と照合してください。</p></div>
      <button class="back-button" id="cancelQuestionnaireImport" type="button">${icons.back} キャンセル</button>
    </div>
    <section class="questionnaire-review-summary">
      <div><small>検出した設問</small><strong>${draft.questions.length}問</strong></div>
      <div><small>形式未判定</small><strong class="${draft.questions.some(q => q.type === '未判定') ? 'review-alert' : ''}">${draft.questions.filter(q => q.type === '未判定').length}件</strong></div>
      <div><small>読み取り警告</small><strong class="${warningItems.length ? 'review-alert' : ''}">${warningItems.length}件</strong></div>
      <div><small>保存状態</small><strong>未確定</strong></div>
    </section>
    ${warningItems.length ? `<div class="review-warning">${icons.alert}<div><strong>確認が必要な箇所があります</strong>${warningItems.map(item => `<span>${esc(item)}</span>`).join('')}</div></div>` : ''}
    <div class="questionnaire-review-layout">
      <section class="source-preview">
        <div class="review-section-head"><div><p class="panel-kicker">SOURCE</p><h2>抽出した原文</h2></div><span>${draft.sourceTruncated ? '先頭60,000字' : '全文'}</span></div>
        <pre>${esc(draft.sourceText)}</pre>
      </section>
      <section class="question-review">
        <div class="review-section-head"><div><p class="panel-kicker">PARSED QUESTIONS</p><h2>読み取った設問</h2></div><button class="button button-secondary" id="addQuestionRow" type="button">設問を追加</button></div>
        <div class="question-review-list">
          ${draft.questions.map((question, index) => renderQuestionReviewRow(question, index)).join('')}
        </div>
      </section>
    </div>
    <section class="questionnaire-confirm-bar">
      <label class="verification-check"><input id="questionnaireVerified" type="checkbox" ${draft.verified ? 'checked' : ''}><span><strong>原本と照合しました</strong><small>設問番号・設問文・回答形式・選択肢が原本と一致していることを確認</small></span></label>
      <button class="button button-primary button-large" id="saveQuestionnaire" type="button" ${state.questionnaireImportBusy ? 'disabled' : ''}>${state.questionnaireImportBusy ? '保存しています…' : `${icons.check} 確認して保存`}</button>
    </section>`;

  document.querySelector('#cancelQuestionnaireImport').onclick = () => {
    state.questionnaireImport = null;
    goHome();
  };
  document.querySelector('#questionnaireVerified').addEventListener('change', event => {
    draft.verified = event.target.checked;
  });
  document.querySelector('#addQuestionRow').onclick = () => {
    collectQuestionnaireDraft();
    draft.questions.push({id: `Q${draft.questions.length + 1}`, text: '', type: '未判定', options: [], source: '手動追加'});
    renderQuestionnaireImport();
  };
  document.querySelectorAll('[data-remove-question]').forEach(button => button.onclick = () => {
    collectQuestionnaireDraft();
    draft.questions.splice(Number(button.dataset.removeQuestion), 1);
    renderQuestionnaireImport();
  });
  document.querySelector('#saveQuestionnaire').onclick = saveQuestionnaire;
}

function renderQuestionReviewRow(question, index) {
  const types = ['単一選択', '複数選択', '尺度', '数値', '自由記述', '未判定'];
  return `<article class="question-review-row" data-question-row>
    <div class="question-row-number">${index + 1}</div>
    <div class="question-review-fields">
      <div class="question-id-type">
        <div class="field"><label>設問番号</label><input class="text-input" data-field="id" value="${esc(question.id)}"></div>
        <div class="field"><label>回答形式</label><select class="select-input ${question.type === '未判定' ? 'needs-review' : ''}" data-field="type">${types.map(type => `<option ${type === question.type ? 'selected' : ''}>${type}</option>`).join('')}</select></div>
      </div>
      <div class="field"><label>設問文</label><textarea class="text-area compact" data-field="text">${esc(question.text)}</textarea></div>
      <div class="field"><label>選択肢（1行に1つ）</label><textarea class="text-area options" data-field="options" placeholder="自由記述の場合は空欄">${esc((question.options || []).join('\n'))}</textarea></div>
      <small class="source-reference">${esc(question.source || '')}</small>
    </div>
    <button class="icon-button danger" data-remove-question="${index}" type="button" aria-label="${esc(question.id)}を削除" title="設問を削除">${icons.trash}</button>
  </article>`;
}

async function saveQuestionnaire() {
  collectQuestionnaireDraft();
  const draft = state.questionnaireImport;
  if (!draft.verified) {
    toast('原本と照合したことを確認してください', true);
    document.querySelector('#questionnaireVerified')?.focus();
    return;
  }
  if (draft.questions.some(question => !question.text || question.type === '未判定')) {
    toast('設問文と回答形式をすべて確認してください', true);
    return;
  }
  state.questionnaireImportBusy = true;
  renderQuestionnaireImport();
  try {
    await api(`/api/projects/${state.workspace.project.id}/materials/questionnaire`, {
      name: draft.filename,
      data: draft.rawData,
      questions: draft.questions,
      verified: true,
      verifiedAt: new Date().toISOString(),
      parserWarnings: draft.warnings || [],
    });
    state.questionnaireImport = null;
    await loadWorkspace();
    goHome();
    toast('設問票を確認済み資料として保存しました');
  } catch (error) {
    toast(error.message, true);
    renderQuestionnaireImport();
  } finally {
    state.questionnaireImportBusy = false;
  }
}

async function saveProjectMeta() {
  const patch = {
    title: document.querySelector('#editTitle').value.trim(),
    partner: document.querySelector('#editPartner').value.trim(),
    background: document.querySelector('#editBackground').value.trim(),
  };
  try {
    const data = await api(`/api/projects/${state.workspace.project.id}`, patch, 'PATCH');
    state.workspace.project = {...state.workspace.project, ...data.project};
    state.projectEditing = false;
    render();
    toast('プロジェクト情報を更新しました');
  } catch (error) { toast(error.message, true); }
}

async function createSessionAndOpen() {
  if (state.sessionBusy) return;
  state.sessionBusy = true; render();
  try {
    const datasets = (state.workspace.project.materials || []).filter(material => material.kind === 'アンケートデータ');
    const demoDataset = datasets.find(material => material.title.includes('大学生観光実態アンケート')) || datasets[0];
    const data = await api(`/api/projects/${state.workspace.project.id}/sessions`, {
      datasetMaterialId: demoDataset?.id || '',
    });
    state.sessionId = data.session.id;
    state.session = data.session;
    initUiStateFromSession(data.session);
    state.sessionBusy = false;
    setView('session');
    setTimeout(() => document.querySelector('#sessionMessage')?.focus(), 30);
    return;
  } catch (error) { toast(error.message, true); }
  state.sessionBusy = false; render();
}

async function openSession(sessionId) {
  state.sessionId = sessionId;
  state.session = null;
  state.mappingNeeded = null;
  state.sessionLoading = true;
  setView('session');
  try {
    const data = await api(`/api/sessions/${sessionId}`);
    if (state.sessionId !== sessionId) return;
    state.session = data.session;
    initUiStateFromSession(data.session);
  } catch (error) { toast(error.message, true); }
  finally { state.sessionLoading = false; if (state.view === 'session') renderSessionShell(); }
}

function initUiStateFromSession(session) {
  const ui = session.uiState || {};
  state.atamiComparisonQuestion = ui.questionId || 'barriers';
  state.atamiComparisonView = ui.comparisonView || 'grouped';
  state.atamiComparisonFilters = (ui.filters && ui.filters.A && ui.filters.B) ? ui.filters : {
    A: {university:['shibaura'], academic_status:['undergraduate'], gender:[], international:[]},
    B: {university:['other'], academic_status:['undergraduate'], gender:[], international:[]},
  };
}

async function refreshWorkspaceSessions() {
  try { state.workspace = await api('/api/workspace'); } catch (error) { /* 履歴更新の失敗は操作を止めない */ }
}

function renderSessionShell() {
  if (state.sessionLoading || !state.session) {
    app.innerHTML = `
      <div class="page-head"><div><p class="eyebrow">SESSION</p><h1>読み込んでいます…</h1></div><button class="back-button" id="backWorkspace">${icons.back} ホーム</button></div>
      <section class="panel skeleton-panel"><div class="skeleton-line w60"></div><div class="skeleton-line w80"></div><div class="skeleton-line w40"></div></section>`;
    document.querySelector('#backWorkspace').onclick = goHome;
    return;
  }
  const session = state.session;
  const ws = state.workspace;
  app.innerHTML = `
    <div class="page-head"><div><p class="eyebrow">SESSION</p><h1>${esc(session.title)}</h1><p>${statusLabel(session.status)}${state.uiStateSaveStatus === 'saving' ? '・画面状態を保存しています' : state.uiStateSaveStatus === 'error' ? '・画面状態の保存に失敗しました（操作は続けられます）' : ''}</p></div><button class="back-button" id="backWorkspace">${icons.back} ホーム</button></div>
    <div class="goal-layout session-layout">
      <section class="session-main">
        ${renderLlmNotice(ws.llm)}
        ${session.dataset === null ? renderDatasetPicker(session) : ''}
        ${session.surveyPlanId ? '' : renderSessionChat(session)}
        ${state.mappingNeeded ? renderMappingUI(state.mappingNeeded) : ''}
        ${(session.status === 'question_confirmed' || session.status === 'needs_validation') && !session.analysis && !state.mappingNeeded ? renderAnalyzeCta(session) : ''}
        ${session.analysis && (session.status === 'analyzed' || session.status === 'needs_validation') ? renderSessionAnalysis(session) : ''}
      </section>
      <aside class="goal-sidebar">
        <section class="conversation-history">
          <div class="history-head"><div><p class="panel-kicker">セッション履歴</p><strong>選択中のセッション</strong></div></div>
          <div class="conversation-list">${ws.recentSessions.map(s => `<button class="conversation-item ${s.id === session.id ? 'active' : ''}" data-session-id="${esc(s.id)}" type="button"><span><strong>${esc(s.title)}</strong><small>${statusLabel(s.status)}・${formatChatTime(s.updatedAt)}</small></span></button>`).join('')}</div>
        </section>
      </aside>
    </div>`;
  document.querySelector('#backWorkspace').onclick = goHome;
  document.querySelectorAll('[data-session-id]').forEach(button => button.onclick = () => { if (button.dataset.sessionId !== session.id) openSession(button.dataset.sessionId); });
  document.querySelector('#pickDataset')?.addEventListener('change', event => pickSessionDataset(event.target.value));
  document.querySelector('#newDatasetFile')?.addEventListener('change', event => uploadNewSessionDataset(event.target.files[0]));
  bindSessionChat();
  document.querySelector('#runAnalyze')?.addEventListener('click', runAnalyze);
  bindSessionAnalysisControls(session);
}

function renderDatasetPicker(session) {
  const options = (state.workspace.project.materials || []).filter(m => m.kind === 'アンケートデータ');
  const isRecovery = Boolean(session?.surveyPlanId);
  const uploadForm = `
      <div class="field"><label for="newDatasetPopulation">調査対象</label><input class="text-input" id="newDatasetPopulation" placeholder="例：熱海駅周辺の来訪者"></div>
      <div class="field"><label for="newDatasetRecruitment">募集方法</label><input class="text-input" id="newDatasetRecruitment" placeholder="例：学内配布・SNS告知"></div>
      <div class="field"><label for="newDatasetPeriod">調査時期</label><input class="text-input" id="newDatasetPeriod" placeholder="例：2026年6月"></div>
      <label class="button button-secondary" for="newDatasetFile">${icons.upload} Excel/CSVを選択<input id="newDatasetFile" type="file" accept=".xlsx,.xlsm,.csv" hidden></label>`;
  if (!options.length) {
    return `<div class="required-data context-warning"><strong>${isRecovery ? '回収した回答データを追加してください' : '元データが必要です'}</strong><span>ExcelまたはCSVを追加してください。</span>${uploadForm}</div>`;
  }
  return `<div class="required-data context-warning"><strong>${isRecovery ? '回収した回答データを選ぶか、新しく追加してください' : '使用するアンケートデータを選んでください'}</strong>
    <select class="select-input" id="pickDataset"><option value="">選択してください</option>${options.map(m => `<option value="${esc(m.id)}">${esc(m.title)}</option>`).join('')}</select>
    ${isRecovery ? uploadForm : ''}
  </div>`;
}

async function uploadNewSessionDataset(file) {
  if (!file) return;
  try {
    const base64 = await fileToBase64(file);
    const data = await api(`/api/projects/${state.workspace.project.id}/materials/dataset`, {
      name: file.name, data: base64,
      population: document.querySelector('#newDatasetPopulation')?.value.trim() || '',
      recruitment: document.querySelector('#newDatasetRecruitment')?.value.trim() || '',
      period: document.querySelector('#newDatasetPeriod')?.value.trim() || '',
    });
    await refreshWorkspaceSessions();
    await pickSessionDataset(data.material.id);
  } catch (error) { toast(error.message, true); }
}

async function pickSessionDataset(materialId) {
  if (!materialId) return;
  try {
    const data = await api(`/api/sessions/${state.session.id}/dataset`, {datasetMaterialId: materialId}, 'PUT');
    state.session = data.session;
    render();
    toast('使用するデータを設定しました');
  } catch (error) { toast(error.message, true); }
}

function renderSessionChat(session) {
  const lastAssistant = [...session.messages].reverse().find(m => m.role === 'assistant');
  const proposal = lastAssistant?.structured;
  const showAnalysisConfirm = session.status === 'consulting' && proposal?.readyToConfirm;
  const analysisComplete = Boolean(session.analysis);
  return `
    <section class="chat-shell">
      <div class="chat-status"><span class="assistant-dot"></span><strong>目的整理アシスタント</strong><small>${state.sessionBusy ? '処理しています…' : analysisComplete ? '分析結果を表示しています' : '自動保存されています'}</small></div>
      <div class="chat-thread" id="sessionChatThread">
        ${session.messages.map(m => renderChatMessage(m, 'content')).join('') || `<div class="message assistant"><span>GUIDE</span>${renderMessageBody('いま、どんなことが気になっていますか？\nうまく整理できていなくても、そのまま書いてください。')}</div>`}
        ${showAnalysisConfirm ? renderSessionAnalysisConfirm() : ''}
      </div>
      ${analysisComplete
        ? '<div class="chat-complete">分析は完了しています。下の結果を確認してください。</div>'
        : !CHAT_ENABLED
          ? renderManualBriefForm(session)
          : `<form class="chat-composer" id="sessionForm"><textarea id="sessionMessage" rows="2" placeholder="例：学生が熱海を候補にしたのに、なぜ来なかったのか知りたい" ${state.sessionBusy ? 'disabled' : ''}></textarea><button class="send-button" type="submit" aria-label="送信" ${state.sessionBusy ? 'disabled' : ''}>${icons.arrow}</button></form>`}
    </section>`;
}

// 対話を止めている公開版では、対話の代わりに問いを直接入力して分析へ進む。
// confirm-question も analyze もLLMを使わないので、この経路は公開版でも完動する。
const MANUAL_BRIEF_PRESETS = {
  atami_conversion: {
    label: '訪問しなかった障壁を調べる',
    objective: '熱海に行こうと考えたのに訪問しなかった障壁を明らかにする',
    target: '熱海を検討したが未訪問の大学生',
    decision: '次に検証する施策候補を絞る',
  },
  atami_policy_test: {
    label: '施策仮説を比較する',
    objective: '熱海で検証したい施策の候補を比較する',
    target: '熱海を検討したが未訪問の大学生',
    decision: '次に検証する施策を1つ選ぶ',
  },
};

function renderManualBriefForm(session) {
  const route = state.manualBriefRoute || 'atami_conversion';
  const preset = MANUAL_BRIEF_PRESETS[route];
  const disabled = state.sessionBusy ? 'disabled' : '';
  return `<div class="brief-proposal manual-brief">
    <div class="brief-proposal-head"><span>${icons.wand}</span><div><strong>問いを入力して分析する</strong><small>公開版はAI対話を止めているので、明らかにしたいことを直接書いて進めます。<br>※このセッション分析は熱海DMOの実データ（特定の列構成）専用です。手元のアンケートを試すときはホームの「どのアンケートでも分析する（汎用）」を使ってください。</small></div></div>
    <label>分析ルート<select class="select-input" id="manualBriefRoute" ${disabled}>${Object.entries(MANUAL_BRIEF_PRESETS).map(([value, item]) => `<option value="${value}" ${value === route ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select></label>
    <label>明らかにしたいこと<textarea id="manualBriefObjective" rows="2" ${disabled}>${esc(preset.objective)}</textarea></label>
    <div class="brief-grid">
      <label>対象<textarea id="manualBriefTarget" rows="2" ${disabled}>${esc(preset.target)}</textarea></label>
      <label>支援する判断<textarea id="manualBriefDecision" rows="2" ${disabled}>${esc(preset.decision)}</textarea></label>
    </div>
    <button class="button button-primary" id="submitManualBrief" type="button" ${disabled}>${icons.chart} この問いで分析する</button>
  </div>`;
}

async function submitManualBrief() {
  if (state.sessionBusy) return;
  const brief = {
    route: document.querySelector('#manualBriefRoute').value,
    objective: document.querySelector('#manualBriefObjective').value.trim(),
    target: document.querySelector('#manualBriefTarget').value.trim(),
    decision: document.querySelector('#manualBriefDecision').value.trim(),
  };
  if (!brief.objective || !brief.target || !brief.decision) return toast('明らかにしたいこと・対象・判断をすべて入力してください', true);
  state.sessionBusy = true;
  state.mappingNeeded = null;
  render();
  try {
    state.session = (await api(`/api/sessions/${state.session.id}/confirm-question`, {brief})).session;
    // 第3引数を省くと body 無し＝GET になるため、明示的に POST を指定する
    state.session = (await api(`/api/sessions/${state.session.id}/analyze`, {}, 'POST')).session;
    initUiStateFromSession(state.session);
    await refreshWorkspaceSessions();
    toast('分析が完了しました');
    setTimeout(() => document.querySelector('#analysisResults')?.scrollIntoView({behavior: 'smooth', block: 'start'}), 80);
  } catch (error) {
    if (error.status === 422 && error.data?.mappingNeeded) {
      state.mappingNeeded = error.data.mappingNeeded;
      try { state.session = (await api(`/api/sessions/${state.session.id}`)).session; } catch (_) { /* 対応づけ画面は出す */ }
      toast('列の対応づけを確認してください', true);
    } else {
      toast(error.message, true);
    }
  } finally { state.sessionBusy = false; render(); }
}

function renderSessionAnalysisConfirm() {
  return `<div class="analysis-confirm">
    <button class="button button-primary" id="runAnalysisFromProposal" type="button" ${state.sessionBusy ? 'disabled' : ''}>${icons.chart} 分析を実行する</button>
  </div>`;
}

function isAnalysisExecutionRequest(message, session) {
  if (!session || !['consulting', 'question_confirmed', 'needs_validation'].includes(session.status)) return false;
  const lastAssistant = [...(session.messages || [])].reverse().find(item => item.role === 'assistant');
  const hasConfirmedProposal = session.status !== 'consulting' || Boolean(lastAssistant?.structured?.readyToConfirm);
  if (!hasConfirmedProposal) return false;

  const text = String(message || '').trim().toLowerCase().replace(/[！!。．、,\s]+/g, '');
  if (!text || /(まだ|待って|やめ|しない|しません|修正|変更|違う)/.test(text)) return false;
  return (
    /(分析|集計).*(して|お願い|進め|実行|開始)/.test(text)
    || /(この内容|これ|それ).*(で|を).*(進め|お願い|分析|実行)/.test(text)
    || /^(はい|うん|お願い|おねがい|進めて|すすめて|そうして|それで)$/.test(text)
  );
}

function renderSessionBriefEditor(brief) {
  return `<div class="brief-proposal">
    <div class="brief-proposal-head"><span>${icons.wand}</span><div><strong>私の理解</strong><small>違っていたら、そのまま書き換えてください</small></div></div>
    <label>明らかにしたいこと<textarea id="sessionBriefObjective">${esc(brief.objective)}</textarea></label>
    <div class="brief-grid"><label>対象<textarea id="sessionBriefTarget">${esc(brief.target)}</textarea></label><label>支援する判断<textarea id="sessionBriefDecision">${esc(brief.decision)}</textarea></label></div>
    <button class="button button-primary" id="confirmSessionBrief" type="button" ${state.sessionBusy ? 'disabled' : ''}>そう、それです ${icons.check}</button>
  </div>`;
}

function bindSessionChat() {
  document.querySelector('#sessionForm')?.addEventListener('submit', event => { event.preventDefault(); sendSessionMessage(document.querySelector('#sessionMessage').value); });
  document.querySelector('#confirmSessionBrief')?.addEventListener('click', confirmSessionBrief);
  document.querySelector('#runAnalysisFromProposal')?.addEventListener('click', runAnalysisFromProposal);
  // 公開版の「問いを入力して分析する」
  document.querySelector('#submitManualBrief')?.addEventListener('click', submitManualBrief);
  document.querySelector('#manualBriefRoute')?.addEventListener('change', event => {
    state.manualBriefRoute = event.target.value; render();
  });
  setTimeout(() => {
    const action = document.querySelector('#runAnalysisFromProposal');
    if (action) action.scrollIntoView({behavior: 'smooth', block: 'center'});
    else document.querySelector('#sessionChatThread')?.scrollTo({top: 9999, behavior: 'smooth'});
  }, 40);
}

async function runAnalysisFromProposal() {
  if (state.sessionBusy) return;
  state.sessionBusy = true;
  state.mappingNeeded = null;
  render();
  try {
    const data = await api(`/api/sessions/${state.session.id}/analyze-from-message`, {message: '分析を実行する'});
    state.session = data.session;
    initUiStateFromSession(data.session);
    await refreshWorkspaceSessions();
    if (data.exchange?.autoAction === 'select_dataset') {
      toast('分析に使うデータを選択してください', true);
    } else {
      toast('分析が完了しました');
      setTimeout(() => document.querySelector('#analysisResults')?.scrollIntoView({behavior: 'smooth', block: 'start'}), 80);
    }
  } catch (error) {
    if (error.status === 422 && error.data?.mappingNeeded) {
      state.mappingNeeded = error.data.mappingNeeded;
      try {
        state.session = (await api(`/api/sessions/${state.session.id}`)).session;
      } catch (_) { /* 対応づけ画面は表示する */ }
      toast('列の対応づけを確認してください', true);
    } else {
      toast(error.message, true);
    }
  } finally {
    state.sessionBusy = false;
    render();
  }
}

async function sendSessionMessage(message) {
  const text = String(message || '').trim();
  if (!text) return toast('考えていることを入力してください', true);
  if (state.sessionBusy) return;
  const runAnalysis = isAnalysisExecutionRequest(text, state.session);
  // 分析の実行はLLMを使わないので公開版でも動かす。純粋な対話だけ止める。
  if (!CHAT_ENABLED && !runAnalysis) return toast(CHAT_DISABLED_MESSAGE, true);
  state.sessionBusy = true; render();
  try {
    const endpoint = runAnalysis ? 'analyze-from-message' : 'messages';
    const data = await api(`/api/sessions/${state.session.id}/${endpoint}`, {message: text});
    state.session = data.session;
    if (runAnalysis) {
      initUiStateFromSession(data.session);
      await refreshWorkspaceSessions();
      toast(data.exchange?.autoAction === 'select_dataset' ? '分析に使うデータを選択してください' : '分析が完了しました');
      if (data.exchange?.autoAction === 'analysis_completed') {
        setTimeout(() => document.querySelector('#analysisResults')?.scrollIntoView({behavior: 'smooth', block: 'start'}), 80);
      }
    } else if (data.exchange?.model) {
      if (data.exchange.model.usedFallback) {
        try { state.workspace.llm = (await api('/api/llm/status')).llm; } catch (_) { /* 応答自体は保存済み */ }
      } else {
        state.workspace.llm = {connected:true, configured:true, provider:data.exchange.model.provider, model:data.exchange.model.model, reason:''};
      }
    }
  } catch (error) {
    if (runAnalysis && error.status === 422 && error.data?.mappingNeeded) {
      state.mappingNeeded = error.data.mappingNeeded;
      try {
        state.session = (await api(`/api/sessions/${state.session.id}`)).session;
      } catch (_) { /* 確定済み状態の再取得失敗は対応づけ画面を止めない */ }
      toast('列の対応づけを確認してください', true);
    } else {
      toast(error.message, true);
      setTimeout(() => { const el = document.querySelector('#sessionMessage'); if (el) el.value = text; }, 20);
    }
  } finally { state.sessionBusy = false; render(); }
}

async function confirmSessionBrief() {
  if (state.sessionBusy) return;
  const lastAssistant = [...state.session.messages].reverse().find(m => m.role === 'assistant');
  const source = lastAssistant?.structured?.briefCandidate || {};
  const brief = {
    ...source,
    objective: document.querySelector('#sessionBriefObjective')?.value.trim() || source.objective || '',
    target: document.querySelector('#sessionBriefTarget')?.value.trim() || source.target || '',
    decision: document.querySelector('#sessionBriefDecision')?.value.trim() || source.decision || '',
  };
  state.sessionBusy = true; render();
  try {
    const data = await api(`/api/sessions/${state.session.id}/confirm-question`, brief);
    state.session = data.session;
    await refreshWorkspaceSessions();
    toast('問いを確定しました');
  } catch (error) { toast(error.message, true); }
  finally { state.sessionBusy = false; render(); }
}

function renderAnalyzeCta(session) {
  if (!session.dataset) return '';
  return `<section class="panel next-route"><p class="panel-kicker">READY</p><h2>問いが確定しました</h2><p>使用データ：${esc(session.dataset.filename)}</p><button class="button button-primary" id="runAnalyze" type="button" ${state.sessionBusy ? 'disabled' : ''}>${state.sessionBusy ? '分析しています…' : '分析を実行する'} ${icons.arrow}</button></section>`;
}

async function runAnalyze() {
  if (state.sessionBusy) return;
  state.sessionBusy = true; state.mappingNeeded = null; render();
  try {
    const data = await api(`/api/sessions/${state.session.id}/analyze`, {});
    state.session = data.session;
    initUiStateFromSession(data.session);
    await refreshWorkspaceSessions();
    toast('分析が完了しました');
    setTimeout(() => document.querySelector('#analysisResults')?.scrollIntoView({behavior: 'smooth', block: 'start'}), 80);
  } catch (error) {
    if (error.status === 422 && error.data?.mappingNeeded) {
      state.mappingNeeded = error.data.mappingNeeded;
      toast('列の対応づけを確認してください', true);
    } else {
      toast(error.message, true);
    }
  }
  finally { state.sessionBusy = false; render(); }
}

function renderMappingUI(items) {
  return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">COLUMN MAPPING</p><h2>設問と列の対応を確認してください</h2><p>回収データの列名から自動で特定できなかった設問があります。対応する列を選んでください。</p></div></div>
    ${items.map(item => item.type === 'matrix5' ? `
      <div class="mapping-row"><strong>${esc(item.questionId)}：${esc(item.text)}</strong>
        ${item.items.map((sub, i) => `<label class="mapping-item">${esc(sub.item)}
          <select class="select-input" data-mapping-q="${esc(item.questionId)}" data-mapping-item="${esc(sub.item)}">
            <option value="">${sub.candidates.length ? '選択してください' : '該当する列が見つかりません'}</option>
            ${sub.candidates.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('')}
          </select></label>`).join('')}
      </div>` : `
      <div class="mapping-row"><strong>${esc(item.questionId)}：${esc(item.text)}</strong>
        <select class="select-input" data-mapping-q="${esc(item.questionId)}">
          <option value="">${item.candidates.length ? '選択してください' : '該当する列が見つかりません'}</option>
          ${item.candidates.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('')}
        </select>
      </div>`).join('')}
    <div class="button-row"><button class="button button-primary" id="confirmMapping" type="button">この対応で分析する ${icons.check}</button></div>
  </section>`;
}

async function confirmMapping() {
  const mapping = {};
  let incomplete = false;
  document.querySelectorAll('[data-mapping-q]').forEach(select => {
    const qid = select.dataset.mappingQ;
    if (!select.value) { incomplete = true; return; }
    if (select.dataset.mappingItem) {
      mapping[qid] = mapping[qid] || {};
      mapping[qid][select.dataset.mappingItem] = select.value;
    } else {
      mapping[qid] = select.value;
    }
  });
  if (incomplete) return toast('すべての設問に列を選んでください', true);
  try {
    await api(`/api/sessions/${state.session.id}/column-mapping`, {mapping});
    state.mappingNeeded = null;
    await runAnalyze();
  } catch (error) { toast(error.message, true); }
}

async function publishSurveyPlan() {
  if (state.sessionBusy) return;
  state.sessionBusy = true; render();
  try {
    const data = await api(`/api/sessions/${state.session.id}/survey-plans`, {});
    const refreshed = await api(`/api/sessions/${state.session.id}`);
    state.session = refreshed.session;
    await refreshWorkspaceSessions();
    toast(`比較調査を発行しました（${data.surveyPlan.id}）`);
  } catch (error) { toast(error.message, true); }
  finally { state.sessionBusy = false; render(); }
}

function findPublishedPlanId(session) {
  for (let i = session.messages.length - 1; i >= 0; i--) {
    const planId = session.messages[i]?.structured?.surveyPlanId;
    if (planId) return planId;
  }
  return null;
}

async function createRecoverySession(planId) {
  if (state.sessionBusy || !planId) return;
  state.sessionBusy = true; render();
  try {
    const data = await api(`/api/projects/${state.workspace.project.id}/sessions`, {surveyPlanId: planId});
    state.sessionId = data.session.id;
    state.session = data.session;
    state.mappingNeeded = null;
    initUiStateFromSession(data.session);
    await refreshWorkspaceSessions();
    state.sessionBusy = false;
    setView('session');
    return;
  } catch (error) { toast(error.message, true); }
  state.sessionBusy = false; render();
}

function renderSurveyLoopCta(session) {
  const planId = findPublishedPlanId(session);
  if (session.status === 'needs_validation' && planId) {
    return `<section class="panel next-route"><p class="panel-kicker">NEXT SURVEY</p><h2>比較調査を発行済みです</h2><p>Googleフォーム等で調査を実施し、回収した回答データ（Excel/CSV）をこのアプリで分析できます。</p><div class="button-row compact"><button class="button button-primary" id="startRecovery" data-plan-id="${esc(planId)}" type="button">回収データを分析する ${icons.arrow}</button></div></section>`;
  }
  if (session.status === 'analyzed' && session.analysis?.next?.survey) {
    return `<section class="panel next-route"><p class="panel-kicker">NEXT SURVEY</p><h2>この比較調査を発行する</h2><p>発行すると分析契約が保存され、回収した回答データをこのアプリでそのまま分析できるようになります。</p><button class="button button-primary" id="publishSurvey" type="button" ${state.sessionBusy ? 'disabled' : ''}>調査を発行する ${icons.arrow}</button></section>`;
  }
  return '';
}

function renderSessionAnalysis(session) {
  const content = session.analysis.engine === 'declared_survey_v1'
    ? renderDeclaredResults(session.analysis)
    : renderAtamiResult(flattenAnalysis(session.analysis)) + renderSurveyLoopCta(session);
  return `<div id="analysisResults" class="analysis-results" tabindex="-1">${content}</div>`;
}

function renderDeclaredResults(analysis) {
  const q = analysis.quality;
  const banner = q.verdict === 'valid_new'
    ? `<section class="quality-banner new"><span class="quality-icon">${icons.check}</span><div><strong>基準外データ：構造チェック合格</strong><p>${esc(q.message)}</p></div><span class="verdict-badge">基準外データ</span></section>`
    : '';
  const blocks = analysis.results.map(result => {
    if (result.kind === 'distribution') {
      return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">${esc(result.question)} / DISTRIBUTION</p><h2>${esc(result.label)}</h2><p>回答 ${result.denominator}件${result.unknownN ? `・選択肢外 ${result.unknownN}件` : ''}・バーの長さ＝回答者に占める割合（0〜100%）</p></div></div>
        <div class="cmp-chart">${result.items.map(item => `<div class="cmp-row"><strong>${esc(item.value)}</strong><div class="cmp-line"><div class="cmp-track"><i class="a" style="width:${Math.min(item.share, 100)}%"></i></div><span class="cmp-val">${item.share}% <small>${item.n}/${result.denominator}件</small></span></div></div>`).join('')}</div>
      </section>`;
    }
    if (result.kind === 'likert_summary') {
      return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">${esc(result.question)} / 5段階評価</p><h2>${esc(result.label)}</h2><p>top2率＝4・5を選んだ割合</p></div></div>
        <div class="table-scroll"><table class="data-table"><thead><tr><th>案</th><th>回答数</th><th>平均</th><th>top2率</th></tr></thead><tbody>
        ${result.rows.map(row => `<tr><td>${esc(row.item)}</td><td class="number">${row.denominator}</td><td class="number">${row.mean ?? '—'}</td><td class="number"><strong>${row.top2Share}%</strong><br><small>${row.top2N}件</small></td></tr>`).join('')}
        </tbody></table></div>
      </section>`;
    }
    if (result.kind === 'crosstab') {
      const denominators = Object.fromEntries(result.colDenominators.map(d => [d.col, d.n]));
      return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">${esc(result.row)} × ${esc(result.col)} / CROSSTAB</p><h2>${esc(result.rowLabel)} × ${esc(result.colLabel)}</h2><p>割合の分母は各列の回答者数です。</p></div></div>
        <div class="table-scroll"><table class="data-table"><thead><tr><th>${esc(result.rowLabel)}</th>${result.colOptions.map(col => `<th><span class="group-head">${esc(col)}</span><small>回答者 ${denominators[col] ?? 0}人</small></th>`).join('')}</tr></thead><tbody>
        ${result.rowOptions.map(rowOpt => `<tr><td>${esc(rowOpt)}</td>${result.colOptions.map(colOpt => { const cell = result.cells.find(c => c.row === rowOpt && c.col === colOpt); return `<td class="number"><strong>${cell?.share ?? 0}%</strong><br><small>${cell?.n ?? 0}人</small></td>`; }).join('')}</tr>`).join('')}
        </tbody></table></div>
      </section>`;
    }
    return '';
  }).join('');
  const freeText = Object.entries(analysis.freeTextCount || {}).map(([qid, n]) => `${qid}：${n}件`).join('・');
  return `${banner}${blocks}
    <div class="claim-grid"><div class="claim can"><strong>このデータから言える</strong>${analysis.claims.canSay.map(x => `<p>${icons.check}${esc(x)}</p>`).join('') || '<p>件数が少なく、主張できる項目がありません</p>'}</div><div class="claim cannot"><strong>まだ言えない</strong>${analysis.claims.cannotSay.map(x => `<p>${icons.alert}${esc(x)}</p>`).join('')}</div></div>
    ${freeText ? `<p class="cmp-privacy">自由記述の回答（${freeText}）は集計せず、本文は表示・送信しません。</p>` : ''}`;
}

function flattenAnalysis(analysis) {
  return {
    quality: analysis.quality,
    funnel: analysis.step1.funnel,
    evidence: analysis.step1.evidence,
    denominatorContract: analysis.step1.denominatorContract,
    comparison: analysis.step3.comparison,
    claims: analysis.step2.claims,
    plans: analysis.next.plans && analysis.next.plans.length ? analysis.next.plans : null,
    survey: analysis.next.survey,
  };
}

function bindSessionAnalysisControls(session) {
  document.querySelector('#confirmMapping')?.addEventListener('click', confirmMapping);
  document.querySelector('#publishSurvey')?.addEventListener('click', publishSurveyPlan);
  document.querySelector('#startRecovery')?.addEventListener('click', event => createRecoverySession(event.currentTarget.dataset.planId));
  if (session.status !== 'analyzed' && session.status !== 'needs_validation') return;
  document.querySelector('#resetAtami')?.addEventListener('click', () => toast('分析済みのため、別データを使うには新しいセッションを作成してください', true));
  document.querySelector('#comparisonQuestion')?.addEventListener('change', event => { state.atamiComparisonQuestion = event.target.value; render(); scheduleUiStateSave(); });
  document.querySelectorAll('[data-comparison-view]').forEach(button => button.onclick = () => { state.atamiComparisonView = button.dataset.comparisonView; render(); scheduleUiStateSave(); });
  document.querySelectorAll('[data-filter-group]').forEach(button => button.onclick = () => {
    const {filterGroup, axis, value} = button.dataset;
    const selected = state.atamiComparisonFilters[filterGroup][axis];
    state.atamiComparisonFilters[filterGroup][axis] = selected.includes(value) ? selected.filter(item => item !== value) : [...selected, value];
    render(); scheduleUiStateSave();
  });
  document.querySelector('#downloadAtamiSurvey')?.addEventListener('click', () => downloadSurveyFromAnalysis(session.analysis));
}

function scheduleUiStateSave() {
  clearTimeout(state.uiStateSaveTimer);
  state.uiStateSaveTimer = setTimeout(saveUiState, 300);
}

async function saveUiState() {
  if (!state.session) return;
  state.uiStateSaveStatus = 'saving';
  const patch = {
    activeStep: state.session.status === 'analyzed' ? 3 : state.session.status === 'question_confirmed' ? 2 : 1,
    questionId: state.atamiComparisonQuestion,
    comparisonView: state.atamiComparisonView,
    filters: state.atamiComparisonFilters,
  };
  try {
    const data = await api(`/api/sessions/${state.session.id}/ui-state`, patch, 'PATCH');
    if (state.session) { state.session.uiState = data.session.uiState; }
    state.uiStateSaveStatus = 'idle';
  } catch (error) {
    state.uiStateSaveStatus = 'error';
    toast('画面状態の保存に失敗しました。操作は続けられます', true);
  }
}

function downloadSurveyFromAnalysis(analysis) {
  const survey = analysis.next.survey;
  if (!survey) return;
  const rows = [['ID', '設問', '形式', '選択肢', '目的'], ...survey.questions.map(q => [q.id, q.text, q.type, (q.options || []).join('／'), survey.purpose])];
  const csv = '﻿' + rows.map(row => row.map(v => `"${String(v).replaceAll('"', '""')}"`).join(',')).join('\r\n');
  const blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = 'session-comparison-survey.csv';
  link.click();
  URL.revokeObjectURL(link.href);
  toast('比較調査の設問案を保存しました');
}

// --- legacy（v2以前。旧ホームからの入口は非表示、コードは温存） -----------------

function renderLegacyHome() {
  app.innerHTML = `
    <section class="hero">
      <div>
        <p class="eyebrow">SURVEY DECISION SUPPORT</p>
        <h1>知りたいことを、<br><em>根拠のある分析</em>に変える。</h1>
        <div class="flow-line"><span>知りたいこと</span>${icons.arrow}<span>実データ分析</span>${icons.arrow}<span>次の設問</span></div>
      </div>
      <p class="hero-copy"><strong>アンケート分析に慣れていない学生向け。</strong><br>使用設問・分子・分母まで確認できるので、データ以上の結論を防ぎます。</p>
    </section>
    <section class="project-launch" aria-label="保存プロジェクト">
      <div class="project-launch-head"><div><p class="eyebrow">PROJECT WORKSPACE</p><h2>調査プロジェクト</h2><p>材料・分析・検証候補を保存し、後から続けられます。</p></div></div>
      <div class="project-create">
        <input class="text-input" id="projectTitle" placeholder="プロジェクト名　例：熱海・大学生の訪問転換">
        <input class="text-input" id="projectDecision" placeholder="何を判断したいか　例：学生の訪問を妨げる要因は何か">
        <input class="text-input" id="projectTarget" placeholder="主な対象　例：首都圏の大学生">
        <button class="button button-primary" id="createProject" type="button">新規作成</button>
      </div>
      <div class="project-list">${state.projects.length ? state.projects.map(project => `<button class="project-row" data-project-id="${esc(project.id)}" type="button"><span><strong>${esc(project.title)}</strong><small>${esc(project.decision_question)}</small></span><span class="project-counts">材料 ${project.material_count}・分析 ${project.analysis_count}${icons.arrow}</span></button>`).join('') : `<div class="empty">${state.projectsLoaded ? '保存済みプロジェクトはありません' : 'プロジェクトを読み込んでいます'}</div>`}</div>
    </section>
    <section class="atami-launch">
      <div><p class="eyebrow">RESEARCH QUESTION DIALOGUE</p><h2>まず、何を明らかにしたいか相談する</h2><p>まとまっていなくても大丈夫です。会話から目的・対象・判断を整理し、合意してから分析へ進みます。</p></div>
      <button class="button button-primary" id="startAtamiDr3" type="button">対話で整理する ${icons.arrow}</button>
    </section>
    <div class="section-divider"><span>または単発で使う</span></div>
    <section class="action-grid" aria-label="開始メニュー">
      <button class="action-card" id="startAnalysis" type="button">
        <span class="action-icon">${icons.chart}</span>
        <h2>アンケート結果を分析する</h2>
        <p>ExcelまたはCSVと「何を知りたいか」を入力。分析計画を確認して、根拠付きの結果を得ます。</p>
        <span class="action-link">分析を始める ${icons.arrow}</span>
      </button>
      <button class="action-card" id="startQuestionnaire" type="button">
        <span class="action-icon">${icons.form}</span>
        <h2>検証用の調査を設計する</h2>
        <p>調査目的から仮説候補を整理。学生が修正・採用した仮説に沿って、短く自然な設問を作ります。</p>
        <span class="action-link">設計を始める ${icons.arrow}</span>
      </button>
    </section>
    <section class="proof-strip" aria-label="システムの特徴">
      <div class="proof-item"><strong>目的に沿った分析計画</strong><span>何をどう比較するかを実行前に確認</span></div>
      <div class="proof-item"><strong>言えること／言えないこと</strong><span>標本・欠損・因果の限界を自動表示</span></div>
      <div class="proof-item"><strong>分析から次の調査へ</strong><span>不足情報を検証用の設問へ変換</span></div>
    </section>`;
  document.querySelector('#startAnalysis').onclick = () => { state.currentProjectId = null; state.currentMaterialId=null; state.conversationDataReady=true; state.dataset = null; state.plan = null; state.result = null; initGoalDialogue(); setView('goal-chat'); };
  document.querySelector('#startAtamiDr3').onclick = () => { state.currentProjectId=null; state.currentMaterialId=null; state.conversationDataReady=true; state.dataset=null; initGoalDialogue(); setView('goal-chat'); };
  document.querySelector('#startQuestionnaire').onclick = () => setView('questionnaire');
  document.querySelector('#createProject').onclick = createProject;
  document.querySelectorAll('[data-project-id]').forEach(button => button.onclick = () => openProject(button.dataset.projectId));
  if (!state.projectsLoaded) loadProjects();
}

function initGoalDialogue() {
  state.conversationLoadToken += 1;
  state.goalDialogue = {
    messages: [{role:'assistant', text:'いま、どんなことが気になっていますか？ うまく整理できていなくても、そのまま書いてください。'}],
    response: null,
  };
  state.researchBrief = null;
  state.currentConversationId = null;
  state.goalDialogue.messages = [{role:'assistant', text:'いま、どんなことが気になっていますか？\nうまく整理できていなくても、そのまま書いてください。'}];
}

function renderGoalChat() {
  if (!state.goalDialogue) initGoalDialogue();
  const dialogue = state.goalDialogue;
  const response = dialogue.response;
  const goalLocked=state.goalSending||state.conversationLoading;
  app.innerHTML = `
    <div class="page-head goal-head"><div><p class="eyebrow">STEP 1 / DEFINE THE QUESTION</p><h1>分析する前に、<br>問いを一緒に決める。</h1><p>この対話は答えを決めるものではありません。「誰について、何を、何の判断のために明らかにするか」を学生と確認します。</p></div><button class="back-button" id="backHome" ${goalLocked?'disabled':''}>${icons.back} ホーム</button></div>
    <div class="goal-layout">
      <section class="chat-shell">
        <div class="chat-status"><span class="assistant-dot"></span><strong>目的整理アシスタント</strong><small>${state.conversationLoading?'履歴を開いています…':state.goalSending?'保存しています…':state.currentConversationId ? 'この会話は自動保存されています' : '最初の送信から自動保存されます'}</small><button class="chat-new" id="newGoalChat" type="button" ${goalLocked?'disabled':''}>＋ 新しい会話</button></div>
        <div class="chat-thread" id="chatThread">
          ${!CHAT_ENABLED ? `<div class="required-data context-warning"><strong>公開版ではAI対話を停止しています</strong><span>静的サイトにAPIキーを置くと第三者に使われてしまうためです。分析・設問生成・レポートはこのまま使えます。有効化の手順はリポジトリの <code>py/llm_adapter.py</code> の冒頭に書いてあります。</span></div>` : ''}
          ${dialogue.messages.map(m=>renderChatMessage(m, 'text')).join('')}
          ${['needs_choice','unsupported'].includes(response?.status) ? `<div class="choice-stack">${response.choices.map(c=>`<button class="choice-button" data-goal-message="${esc(c.message)}" ${goalLocked?'disabled':''}><strong>${esc(c.label)}</strong>${icons.arrow}</button>`).join('')}</div>${response.status==='unsupported' ? `<div class="required-data"><strong>別地域で必要なもの</strong><span>${response.required.map(esc).join('・')}</span></div>` : ''}` : ''}
          ${state.researchBrief ? `<div class="message assistant confirmed-message"><span>GUIDE</span><p>はい、その目的で進めます。では、目的に関係するデータだけを使って分析します。</p></div><button class="button button-primary analyze-from-chat" id="analyzeFromChat" ${state.conversationDataReady&&!goalLocked?'':'disabled'}>${state.conversationDataReady?'この目的で実データを分析する':'元データを選び直してください'} ${icons.arrow}</button>` : ''}
          ${!state.conversationDataReady ? `<div class="required-data context-warning"><strong>元のアンケートを復元できませんでした</strong><span>プロジェクト画面から対象データを選び直してください。別のデータで分析は開始しません。</span></div>` : ''}
        </div>
        <form class="chat-composer" id="goalForm"><textarea id="goalMessage" rows="2" placeholder="${state.researchBrief ? '追加の条件や、修正したいことを書いてください' : '例：学生が熱海を候補にしたのに、なぜ来なかったのか知りたい'}" ${goalLocked?'disabled':''}>${esc(localStorage.getItem('survey-insight-goal-draft') || '')}</textarea><button class="send-button" type="submit" aria-label="送信" ${goalLocked?'disabled':''}>${icons.arrow}</button></form>
      </section>
      <aside class="goal-sidebar">
        <section class="conversation-history">
          <div class="history-head"><div><p class="panel-kicker">会話履歴</p><strong>すぐに続きから開く</strong></div><button id="newGoalChatAside" type="button" ${goalLocked?'disabled':''}>新規</button></div>
          <div class="conversation-list">${state.conversations.length ? state.conversations.map(item=>`<button class="conversation-item ${item.id===state.currentConversationId?'active':''}" data-conversation-id="${esc(item.id)}" type="button" ${goalLocked?'disabled':''}><span><strong>${esc(item.title)}</strong><small>${formatChatTime(item.updatedAt)}・${item.confirmed?'問い確定':'整理中'}</small></span><b>${item.messageCount}</b></button>`).join('') : `<div class="history-empty">${state.conversationsLoaded?'保存された会話はまだありません':'履歴を読み込んでいます'}</div>`}</div>
        </section>
        <section class="goal-guide">
          <p class="panel-kicker">この会話で決めること</p>
          <ol><li class="active"><b>1</b><span><strong>問い</strong><small>何を明らかにするか</small></span></li><li class="${response?.brief?'active':''}"><b>2</b><span><strong>対象と判断</strong><small>誰の、何のためか</small></span></li><li class="${state.researchBrief?'active':''}"><b>3</b><span><strong>分析開始</strong><small>目的に必要な計算だけ</small></span></li></ol>
          <div class="scope-note"><strong>対象範囲</strong><p>アンケートを使う地域連携PBLに特化しています。施策を自動決定する汎用AIではありません。</p></div>
        </section>
      </aside>
    </div>`;
  document.querySelector('#backHome').onclick = cancelGoalConversationLoad;
  document.querySelector('#goalForm')?.addEventListener('submit', event => { event.preventDefault(); sendGoalMessage(document.querySelector('#goalMessage').value); });
  document.querySelector('#goalMessage')?.addEventListener('input', event => localStorage.setItem('survey-insight-goal-draft', event.target.value));
  document.querySelectorAll('[data-goal-message]').forEach(button=>button.onclick=()=>sendGoalMessage(button.dataset.goalMessage));
  document.querySelectorAll('[data-conversation-id]').forEach(button=>button.onclick=()=>openConversation(button.dataset.conversationId));
  document.querySelector('#newGoalChat')?.addEventListener('click', newGoalConversation);
  document.querySelector('#newGoalChatAside')?.addEventListener('click', newGoalConversation);
  document.querySelector('#confirmBrief')?.addEventListener('click', confirmResearchBrief);
  document.querySelector('#analyzeFromChat')?.addEventListener('click', () => { if(!state.conversationDataReady) return toast('元のアンケートを選び直してください',true); state.atamiDr3=null; setView('atami-dr3'); analyzeConfirmedData(); });
  setTimeout(()=>document.querySelector('#chatThread')?.scrollTo({top:9999,behavior:'smooth'}),20);
  if (!state.conversationsLoaded) loadConversations();
}

function formatChatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.getMonth()+1}/${date.getDate()} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
}

function newGoalConversation() {
  if(state.goalSending||state.conversationLoading) return;
  localStorage.removeItem('survey-insight-goal-draft');
  initGoalDialogue();
  renderGoalChat();
  setTimeout(()=>document.querySelector('#goalMessage')?.focus(),20);
}

async function loadConversations() {
  state.conversationsLoaded = true;
  try {
    const data = await api('/api/conversations');
    state.conversations = data.conversations;
    if (state.view === 'goal-chat') renderGoalChat();
  } catch(error) { toast(error.message,true); }
}

async function openConversation(conversationId) {
  if(state.goalSending||state.conversationLoading) return;
  const loadToken=++state.conversationLoadToken;
  state.conversationLoading=true;
  renderGoalChat();
  try {
    const data = await api(`/api/conversations/${conversationId}`);
    const conversation = data.conversation;
    let restoredProject=null;
    let restoredDataset=null;
    let dataReady=true;
    if(conversation.projectId) {
      const projectData=await api(`/api/projects/${conversation.projectId}`);
      restoredProject=projectData.project;
      const material=restoredProject.materials.find(item=>item.id===conversation.materialId);
      restoredDataset=material?.dataset||null;
      dataReady=Boolean(restoredDataset);
    }
    if(loadToken!==state.conversationLoadToken) return;
    state.project=restoredProject;
    state.currentProjectId=conversation.projectId||null;
    state.currentMaterialId=conversation.materialId||null;
    state.dataset=restoredDataset;
    state.conversationDataReady=dataReady;
    state.currentConversationId = conversation.id;
    state.goalDialogue = {messages:conversation.messages,response:conversation.response};
    state.researchBrief = conversation.brief;
    localStorage.removeItem('survey-insight-goal-draft');
  } catch(error) { if(loadToken===state.conversationLoadToken) toast(error.message,true); }
  finally { if(loadToken===state.conversationLoadToken) { state.conversationLoading=false; if(state.view==='goal-chat') renderGoalChat(); } }
}

function cancelGoalConversationLoad() {
  if(state.goalSending) return;
  state.conversationLoadToken+=1;
  state.conversationLoading=false;
  state.projectsLoaded=false;
  setView('home');
}

function renderBriefEditor(brief) {
  return `<div class="brief-proposal">
    <div class="brief-proposal-head"><span>${icons.wand}</span><div><strong>私の理解</strong><small>違っていたら、そのまま書き換えてください</small></div></div>
    <label>明らかにしたいこと<textarea id="briefObjective">${esc(brief.objective)}</textarea></label>
    <div class="brief-grid"><label>対象<textarea id="briefTarget">${esc(brief.target)}</textarea></label><label>支援する判断<textarea id="briefDecision">${esc(brief.decision)}</textarea></label></div>
    <div class="analysis-reason"><strong>なぜ、この対象を見るのか</strong><p>${esc(brief.why)}</p><small>分析方法：${esc(brief.analysis)}</small></div>
    <button class="button button-primary" id="confirmBrief" type="button" ${state.goalSending||state.conversationLoading?'disabled':''}>そう、それです ${icons.check}</button>
  </div>`;
}

async function sendGoalMessage(message) {
  // 公開版では対話を停止（有効化手順は先頭の CHAT_ENABLED 付近を参照）
  if(!CHAT_ENABLED) return toast(CHAT_DISABLED_MESSAGE,true);
  if(state.goalSending||state.conversationLoading) return;
  const text=String(message||'').trim(); if(!text) return toast('考えていることを入力してください',true);
  const snapshot={messages:[...state.goalDialogue.messages],response:state.goalDialogue.response,brief:state.researchBrief,draft:localStorage.getItem('survey-insight-goal-draft')||text};
  state.goalSending=true;
  state.goalDialogue.messages.push({role:'user',text}); state.goalDialogue.response=null; state.researchBrief=null; renderGoalChat();
  try {
    const path=state.currentConversationId ? `/api/conversations/${state.currentConversationId}/messages` : '/api/conversations';
    const data=await api(path,{message:text,projectId:state.currentProjectId||'',materialId:state.currentMaterialId||''});
    const conversation=data.conversation;
    state.currentConversationId=conversation.id;
    state.goalDialogue={messages:conversation.messages,response:conversation.response};
    state.researchBrief=conversation.brief;
    localStorage.removeItem('survey-insight-goal-draft');
    state.conversationsLoaded=false;
    state.goalSending=false;
    if(state.view==='goal-chat') renderGoalChat();
  }
  catch(error){ state.goalDialogue.messages=snapshot.messages; state.goalDialogue.response=snapshot.response; state.researchBrief=snapshot.brief; localStorage.setItem('survey-insight-goal-draft',snapshot.draft); state.goalSending=false; if(state.view==='goal-chat') renderGoalChat(); toast(error.message,true); }
}

async function confirmResearchBrief() {
  if(state.goalSending||state.conversationLoading) return;
  const conversationId=state.currentConversationId;
  const snapshot={brief:state.researchBrief,response:state.goalDialogue.response};
  const source=state.researchBrief || state.goalDialogue.response.brief;
  const candidate={...source,objective:document.querySelector('#briefObjective').value.trim(),target:document.querySelector('#briefTarget').value.trim(),decision:document.querySelector('#briefDecision').value.trim()};
  state.goalSending=true;
  document.querySelectorAll('#confirmBrief,[data-conversation-id],#newGoalChat,#newGoalChatAside,#goalMessage,.send-button').forEach(element=>element.disabled=true);
  try {
    const validated=await api('/api/research-dialogue/validate',{brief:candidate});
    if(state.currentConversationId!==conversationId) throw new Error('会話が切り替わったため、確定を中止しました');
    let confirmedBrief=validated.brief;
    let confirmedResponse=state.goalDialogue.response;
    if(conversationId) {
      const saved=await api(`/api/conversations/${conversationId}/brief`,{brief:confirmedBrief});
      confirmedBrief=saved.conversation.brief;
      confirmedResponse=saved.conversation.response;
      state.conversationsLoaded=false;
    }
    state.researchBrief=confirmedBrief;
    state.goalDialogue.response=confirmedResponse;
  }
  catch(error){ state.researchBrief=snapshot.brief; state.goalDialogue.response=snapshot.response; toast(error.message,true); }
  finally { state.goalSending=false; if(state.view==='goal-chat') renderGoalChat(); }
}

function renderAtamiDr3() {
  const r = state.atamiDr3;
  app.innerHTML = `
    <div class="page-head"><div><p class="eyebrow">ATAMI DR3 / FIXED ANALYSIS</p><h1>「調べた」で終わらせず、<br>次に何を比べるかまで決める。</h1><p>大学生アンケートの分岐列を統合し、合意済みの対象条件でファネルを再現します。</p></div><button class="back-button" id="backHome">${icons.back} ホーム</button></div>
    ${state.researchBrief ? `<section class="research-question-banner"><span>確定した問い</span><div><strong>${esc(state.researchBrief.objective)}</strong><small>対象：${esc(state.researchBrief.target)} ／ 判断：${esc(state.researchBrief.decision)}</small></div><button class="back-button" id="backToGoal">問いを修正</button></section>` : ''}
    ${!r ? `<section class="panel atami-start"><div><p class="panel-kicker">START</p><h2>目的に必要なデータを読み込む</h2><p>デモでは保存済みの実データを使用できます。別のファイルでは基準値不一致時に処理を止めます。</p></div><div class="atami-start-actions"><button class="button button-primary" id="useSavedAtami">保存済み実データで分析</button><label class="button button-secondary">別ファイルを選ぶ<input id="atamiFile" type="file" accept=".xlsx,.xlsm,.csv" hidden></label></div></section>` : renderAtamiResult(r)}
  `;
  document.querySelector('#backHome').onclick = () => setView('home');
  document.querySelector('#backToGoal')?.addEventListener('click',()=>setView('goal-chat'));
  document.querySelector('#returnToGoal')?.addEventListener('click',()=>{ initGoalDialogue(); setView('goal-chat'); sendGoalMessage('熱海の若者向け施策を整理し、次にどれを検証するか決めたい'); });
  document.querySelector('#useSavedAtami')?.addEventListener('click', analyzeSavedAtami);
  document.querySelector('#atamiFile')?.addEventListener('change', event => analyzeAtamiFile(event.target.files[0]));
  document.querySelector('#resetAtami')?.addEventListener('click', () => { state.atamiDr3 = null; renderAtamiDr3(); });
  document.querySelector('#downloadAtamiSurvey')?.addEventListener('click', downloadAtamiSurvey);
  document.querySelector('#comparisonQuestion')?.addEventListener('change',event=>{ state.atamiComparisonQuestion=event.target.value; renderAtamiDr3(); });
  document.querySelectorAll('[data-comparison-view]').forEach(button=>button.onclick=()=>{ state.atamiComparisonView=button.dataset.comparisonView; renderAtamiDr3(); });
  document.querySelectorAll('[data-filter-group]').forEach(button=>button.onclick=()=>{
    const {filterGroup,axis,value}=button.dataset;
    const selected=state.atamiComparisonFilters[filterGroup][axis];
    state.atamiComparisonFilters[filterGroup][axis]=selected.includes(value)?selected.filter(item=>item!==value):[...selected,value];
    renderAtamiDr3();
  });
}

function renderAtamiResult(r) {
  const q = r.quality;
  if (!q.canonical) return `
    <section class="quality-banner stop"><span class="quality-icon">${icons.alert}</span><div><strong>再現チェック：停止</strong><p>${esc(q.message)}</p></div><button class="button button-secondary" id="resetAtami">別データで確認</button></section>
    <section class="panel"><div class="panel-head"><div><p class="panel-kicker">DATA QUALITY GATE</p><h2>不一致箇所を確認してください</h2><p>古い主張・施策候補・設問案は表示しません。</p></div></div><div class="check-grid">${q.checks.map(c=>`<div class="${c.ok?'ok':'ng'}"><strong>${esc(c.label)}</strong><span>実データ ${c.actual} ／ 基準 ${c.expected}</span></div>`).join('')}</div></section>`;
  const comparison=r.comparison;
  const question=comparison.questions.find(item=>item.id===state.atamiComparisonQuestion)||comparison.questions[0];
  state.atamiComparisonQuestion=question.id;
  const groupA=aggregateComparison(comparison,state.atamiComparisonFilters.A,question.id);
  const groupB=aggregateComparison(comparison,state.atamiComparisonFilters.B,question.id);
  const overlapN=comparison.cells.filter(cell=>matchesComparisonCell(cell,state.atamiComparisonFilters.A)&&matchesComparisonCell(cell,state.atamiComparisonFilters.B)).reduce((sum,cell)=>sum+cell.n,0);
  const max = r.funnel[0].n;
  return `
    <section class="quality-banner ${q.canonical ? 'ok' : 'stop'}"><span class="quality-icon">${q.canonical ? icons.check : icons.alert}</span><div><strong>${q.canonical ? '再現チェック：一致' : '再現チェック：停止'}</strong><p>${esc(q.message)}</p></div><button class="button button-secondary" id="resetAtami">別データで確認</button></section>
    <section class="panel"><div class="panel-head"><div><p class="panel-kicker">STEP 1 / FUNNEL</p><h2>離脱点を、同じ定義で再現</h2><p>最初に対象者を限定し、「未訪問」の中から検討経験と旅行意欲を追います。</p></div></div>
      <div class="funnel-list">${r.funnel.map((stage,i) => `<div class="funnel-row"><div class="funnel-label"><span>0${i+1}</span><strong>${esc(stage.label)}</strong><small>${esc(stage.note)}</small></div><div class="funnel-track"><i style="width:${Math.max(stage.n/max*100,12)}%"></i></div><b>${stage.n}<small>件</small></b></div>`).join('')}</div>
      <details class="evidence-detail"><summary>計算条件と分母を見る</summary><div class="trace-table">${r.evidence.map(e => `<div><strong>${esc(e.stage)}</strong><span>${esc(e.condition)}</span><span>${e.numerator} / ${e.denominator}</span><small>${e.columns.map(esc).join(' ＋ ')}</small></div>`).join('')}</div></details>
    </section>
    ${r.denominatorContract ? renderDenominatorContract(r.denominatorContract) : ''}
    <section class="panel"><div class="panel-head"><div><p class="panel-kicker">STEP 2 / HUMAN-IN-THE-LOOP COMPARISON</p><h2>比べたい学生像を、人が組み合わせる</h2><p>対象は「熱海を検討したが未訪問」の${comparison.populationN}件。年齢・学年・国籍は使わず、判断に必要な4軸だけに絞ります。</p></div><label class="dimension-select">比較する設問<select id="comparisonQuestion">${comparison.questions.map(item=>`<option value="${item.id}" ${item.id===question.id?'selected':''}>${esc(item.label)}</option>`).join('')}</select></label></div>
      <div class="comparison-builders">${renderComparisonBuilder('A',comparison.axes,state.atamiComparisonFilters.A,groupA)}${renderComparisonBuilder('B',comparison.axes,state.atamiComparisonFilters.B,groupB)}</div>
      <div class="comparison-toolbar"><div><strong>${esc(question.label)}</strong><small>複数回答のため、割合の合計は100%を超えることがあります。</small></div><div class="view-toggle"><button type="button" data-comparison-view="grouped" class="${state.atamiComparisonView==='grouped'?'active':''}">重ねて比較</button><button type="button" data-comparison-view="split" class="${state.atamiComparisonView==='split'?'active':''}">左右で比較</button></div></div>
      ${renderComparisonChart(groupA,groupB,state.atamiComparisonView)}
      ${(groupA.denominator<5||groupB.denominator<5)?`<div class="segment-limit">片方の有効回答が5件未満です。差の断定には使わず、追加で確認する候補を見つけるための参考値として見てください。</div>`:''}
      ${overlapN?`<div class="segment-limit">AとBに同じ回答者が${overlapN}件含まれます。独立した2群の比較ではありません。</div>`:''}
      <p class="cmp-privacy">${esc(comparison.privacy)}</p>
      <div class="claim-grid"><div class="claim can"><strong>このデータから言える</strong>${r.claims.canSay.map(x=>`<p>${icons.check}${esc(x)}</p>`).join('')}</div><div class="claim cannot"><strong>まだ言えない</strong>${r.claims.cannotSay.map(x=>`<p>${icons.alert}${esc(x)}</p>`).join('')}</div></div>
    </section>
    ${r.plans ? `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">STEP 3 / NEXT TEST</p><h2>3案を、1回の短い調査で比較する</h2><p>小さなアンケートを何度も行わず、同じ回答者・同じ尺度で候補を比較します。</p></div><button class="button button-primary" id="downloadAtamiSurvey">設問案CSVを保存</button></div>
      <div class="plan-grid">${r.plans.map(p=>`<article><span>PLAN ${p.id}</span><h3>${esc(p.name)}</h3><p>根拠：${esc(p.basis)}</p></article>`).join('')}</div>
      <div class="survey-preview"><div><strong>${esc(r.survey.title)}</strong><small>${esc(r.survey.purpose)}</small></div>${r.survey.questions.map(x=>`<div class="survey-row"><b>${x.id}</b><span><strong>${esc(x.text)}</strong><small>${esc(x.type)}${x.options?.length ? ` ／ ${x.options.map(esc).join('・')}` : ''}${x.scale?.length ? ` ／ 尺度：${x.scale.map(esc).join('・')}` : ''}</small></span></div>`).join('')}</div>
    </section>` : `<section class="panel next-route"><p class="panel-kicker">ANALYSIS COMPLETE</p><h2>今回は「来なかった障壁」までを回答しました</h2><p>施策候補の比較は別の問いです。問いを「次に試す施策を絞る」に変更すると、3案と次の比較調査を表示します。</p><button class="button button-secondary" id="returnToGoal" type="button">問いを変更する</button></section>`}`;
}

function renderDenominatorContract(contract) {
  return `<section class="panel denominator-contract">
    <div class="panel-head"><div><p class="panel-kicker">DENOMINATOR CONTRACT</p><h2>誰を対象に、何人を分母にしたか</h2><p>設問への実到達と、今回の分析対象内の回答を分けて集計しています。</p></div><span class="contract-status">${contract.verified ? icons.check : icons.alert} ${contract.verified ? '確認済み' : '要確認'}</span></div>
    <div class="contract-context">
      <div><span>分析母集団</span><strong>${esc(contract.population.label)}</strong><b>${contract.population.n}<small>人</small></b></div>
      <div><span>今回の対象</span><strong>${esc(contract.cohort.label)}</strong><b>${contract.cohort.n}<small>人</small></b></div>
    </div>
    <div class="contract-prerequisites"><strong>対象になる前提</strong><ol>${contract.cohort.prerequisites.map(item => `<li>${esc(item)}</li>`).join('')}</ol></div>
    <div class="contract-questions">${contract.questions.map(question => `
      <article>
        <div><span>${esc(question.label)}</span><small>${esc(question.reachCondition)}</small></div>
        <dl>
          <div><dt>設問到達</dt><dd>${question.reachedN}</dd></div>
          <div><dt>到達内回答</dt><dd>${question.answeredN}</dd></div>
          <div><dt>到達内未回答</dt><dd>${question.missingN}</dd></div>
          <div><dt>分岐対象外</dt><dd>${question.excludedByBranchN}</dd></div>
          <div><dt>今回の対象</dt><dd>${question.cohortN}</dd></div>
          <div><dt>対象内回答</dt><dd>${question.cohortAnsweredN}</dd></div>
          <div><dt>対象内未回答</dt><dd>${question.cohortMissingN}</dd></div>
        </dl>
      </article>`).join('')}</div>
    <p class="contract-source">構造：${esc(contract.snapshot.title)} ／ ${esc(contract.snapshot.revision)}</p>
  </section>`;
}

function matchesComparisonCell(cell, filters) {
  return Object.entries(filters).every(([axis,selected])=>!selected.length||selected.includes(cell.attributes[axis]));
}

function aggregateComparison(comparison, filters, questionId) {
  const cells=comparison.cells.filter(cell=>matchesComparisonCell(cell,filters));
  const counts=new Map();
  let denominator=0;
  cells.forEach(cell=>{
    const question=cell.questions[questionId];
    denominator+=question.denominator;
    question.items.forEach(item=>{
      const current=counts.get(item.rawLabel)||{label:item.label,rawLabel:item.rawLabel,n:0};
      current.n+=item.n;
      counts.set(item.rawLabel,current);
    });
  });
  const items=[...counts.values()].map(item=>({...item,pct:denominator?Math.round(item.n/denominator*1000)/10:0})).sort((a,b)=>b.n-a.n||a.label.localeCompare(b.label,'ja'));
  return {label:comparisonFilterLabel(comparison.axes,filters),n:cells.reduce((sum,cell)=>sum+cell.n,0),denominator,items};
}

function comparisonFilterLabel(axes,filters) {
  const parts=[];
  axes.forEach(axis=>{
    const selected=filters[axis.id]||[];
    if(selected.length&&selected.length<axis.values.length) parts.push(selected.map(id=>axis.values.find(value=>value.id===id)?.label||id).join('・'));
  });
  return parts.join(' × ')||'全体';
}

function renderComparisonBuilder(group,axes,filters,result) {
  return `<article class="comparison-builder group-${group.toLowerCase()}"><div class="comparison-builder-head"><span>GROUP ${group}</span><div><strong>${esc(result.label)}</strong><small>該当 ${result.n}件 ／ 有効回答 ${result.denominator}件</small></div></div>${axes.map(axis=>`<div class="filter-row"><b>${esc(axis.label)}</b><div>${axis.values.map(value=>`<button type="button" data-filter-group="${group}" data-axis="${axis.id}" data-value="${value.id}" class="${filters[axis.id].includes(value.id)?'active':''}">${esc(value.label)}</button>`).join('')}</div></div>`).join('')}<p>未選択の軸は「すべて」。同じ軸で複数選択するとOR、軸をまたぐとANDです。</p></article>`;
}

function renderComparisonChart(groupA,groupB,view) {
  const labels=[...new Set([...groupA.items.map(item=>item.rawLabel),...groupB.items.map(item=>item.rawLabel)])];
  const rows=labels.map(rawLabel=>{
    const a=groupA.items.find(item=>item.rawLabel===rawLabel)||{label:(groupB.items.find(item=>item.rawLabel===rawLabel)?.label||rawLabel),n:0,pct:0};
    const b=groupB.items.find(item=>item.rawLabel===rawLabel)||{label:a.label,n:0,pct:0};
    return {label:a.label,a,b,max:Math.max(a.pct,b.pct)};
  }).sort((left,right)=>right.max-left.max);
  if(!rows.length) return `<div class="empty-segment">この条件に該当する有効回答はありません</div>`;
  if(view==='split') return `<div class="comparison-split"><div class="split-head"><strong>A：${esc(groupA.label)}（n=${groupA.denominator}）</strong><span></span><strong>B：${esc(groupB.label)}（n=${groupB.denominator}）</strong></div>${rows.map(row=>`<div class="split-row"><div><span>${row.a.pct}%・${row.a.n}件</span><i><b style="width:${Math.min(row.a.pct,100)}%"></b></i></div><strong>${esc(row.label)}</strong><div><i><b style="width:${Math.min(row.b.pct,100)}%"></b></i><span>${row.b.pct}%・${row.b.n}件</span></div></div>`).join('')}<div class="cmp-scale-note">バーの長さ＝回答者に占める割合（0〜100%・目盛りは25%刻み）</div></div>`;
  return `<div class="cmp-chart">
    <div class="cmp-legend"><span class="a">A：${esc(groupA.label)}<b>n=${groupA.denominator}</b></span><span class="b">B：${esc(groupB.label)}<b>n=${groupB.denominator}</b></span><small>バーの長さ＝回答者に占める割合（0〜100%・目盛りは25%刻み）</small></div>
    ${rows.map(row=>`<div class="cmp-row"><strong>${esc(row.label)}</strong>
      <div class="cmp-line"><b class="cmp-tag a">A</b><div class="cmp-track"><i class="a" style="width:${Math.min(row.a.pct,100)}%"></i></div><span class="cmp-val">${row.a.pct}% <small>${row.a.n}/${groupA.denominator}件</small></span></div>
      <div class="cmp-line"><b class="cmp-tag b">B</b><div class="cmp-track"><i class="b" style="width:${Math.min(row.b.pct,100)}%"></i></div><span class="cmp-val">${row.b.pct}% <small>${row.b.n}/${groupB.denominator}件</small></span></div>
    </div>`).join('')}
  </div>`;
}

function renderRankBars(items, denominator, limit=6) {
  if(!items.length) return `<div class="empty-segment">該当する回答はありません</div>`;
  return `<div class="rank-bars">${items.slice(0,limit).map(item=>`<div><span><strong>${esc(item.label)}</strong><small>${item.n}/${denominator}件・${item.pct}%</small></span><i><b style="width:${Math.min(item.pct,100)}%"></b></i></div>`).join('')}</div>`;
}

function renderAttributeProfile(dimension,totalUnvisited) {
  return `<div class="attribute-profile"><div class="attribute-profile-title"><strong>未訪問 ${totalUnvisited}件の内訳</strong><small>使用設問：${esc(dimension.column)}</small></div><div class="attribute-groups">${dimension.groups.map(group=>`<div><span><strong>${esc(group.label)}</strong><small>検討未訪問 ${group.consideredN}件${group.smallSample?'・参考値':''}</small></span><i><b style="width:${group.unvisitedN/totalUnvisited*100}%"></b></i><em>${group.unvisitedN}<small>件</small></em></div>`).join('')}</div></div>`;
}

async function analyzeSavedAtami() {
  try { state.busy = true; toast('実データを検証しています'); const data = await api('/api/atami-dr3/saved', {brief:state.researchBrief}); state.atamiDr3 = data.result; renderAtamiDr3(); }
  catch (error) { toast(error.message, true); } finally { state.busy = false; }
}

async function analyzeConfirmedData() {
  if (!state.dataset?.id) return analyzeSavedAtami();
  try { state.busy=true; toast(`${state.dataset.filename} を目的に沿って分析しています`); const data=await api('/api/atami-dr3/dataset',{datasetId:state.dataset.id,brief:state.researchBrief}); state.atamiDr3=data.result; renderAtamiDr3(); }
  catch(error){ toast(error.message,true); }
  finally{ state.busy=false; }
}

async function analyzeAtamiFile(file) {
  if (!file) return;
  try { const base64 = await fileToBase64(file); const data = await api('/api/atami-dr3/analyze', {name:file.name, data:base64, brief:state.researchBrief}); state.atamiDr3=data.result; renderAtamiDr3(); }
  catch (error) { toast(error.message, true); }
}

function downloadAtamiSurvey() {
  const rows = [['ID','設問','形式','選択肢','目的'], ...state.atamiDr3.survey.questions.map(q=>[q.id,q.text,q.type,(q.options||[]).join('／'),state.atamiDr3.survey.purpose])];
  const csv='\uFEFF'+rows.map(row=>row.map(v=>`"${String(v).replaceAll('"','""')}"`).join(',')).join('\r\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'}); const link=document.createElement('a'); link.href=URL.createObjectURL(blob); link.download='atami-dr3-comparison-survey.csv'; link.click(); URL.revokeObjectURL(link.href); toast('比較調査の設問案を保存しました');
}

async function loadProjects() {
  try {
    const data = await api('/api/projects');
    state.projects = data.projects;
    state.projectsLoaded = true;
    if (state.view === 'home') render();
  } catch (error) { state.projectsLoaded = true; toast(error.message, true); render(); }
}

async function createProject() {
  const body = {
    title: document.querySelector('#projectTitle').value.trim(),
    decisionQuestion: document.querySelector('#projectDecision').value.trim(),
    target: document.querySelector('#projectTarget').value.trim(),
  };
  try {
    const data = await api('/api/projects', body);
    state.project = data.project;
    state.currentProjectId = data.project.id;
    state.selectedCandidateIds = [];
    state.researchPlan = null;
    state.projectsLoaded = false;
    setView('project');
  } catch (error) { toast(error.message, true); }
}

async function openProject(projectId) {
  try {
    const data = await api(`/api/projects/${projectId}`);
    state.project = data.project;
    state.currentProjectId = projectId;
    state.selectedCandidateIds = [];
    state.researchPlan = data.project.researchPlans?.[0]?.payload || null;
    setView('project');
  } catch (error) { toast(error.message, true); }
}

function renderProject() {
  const project = state.project;
  if (!project) return setView('home');
  const organization = project.organization;
  app.innerHTML = `
    ${pageHead('PROJECT', esc(project.title), esc(project.decision_question))}
    <section class="project-summary">
      <div><small>主な対象</small><strong>${esc(project.target || '未設定')}</strong></div>
      <div><small>登録材料</small><strong>${project.materials.length}件</strong></div>
      <div><small>保存分析</small><strong>${project.analyses.length}件</strong></div>
      <div><small>最終更新</small><strong>${esc(String(project.updated_at).slice(0, 16).replace('T', ' '))}</strong></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><p class="panel-kicker">STEP 1・MATERIALS</p><h2>今までの材料を追加</h2><p>アンケートデータと、現場で聞いたこと・論文・学生の気づきを分けて登録します。</p></div></div>
      <div class="material-input-grid">
        <div class="dataset-upload-box"><div class="field"><label for="surveyPopulation">調査対象</label><input class="text-input" id="surveyPopulation" placeholder="例：熱海駅周辺の来訪者"></div><div class="field"><label for="surveyRecruitment">募集方法</label><input class="text-input" id="surveyRecruitment" placeholder="例：駅周辺で対面依頼"></div><div class="field"><label for="surveyPeriod">調査時期</label><input class="text-input" id="surveyPeriod" placeholder="例：2026年6月"></div><label class="upload-zone compact-upload" for="projectDataset">${icons.upload}<strong>アンケートデータ</strong><span>Excel / CSV・このPC内に保存</span><input id="projectDataset" type="file" accept=".xlsx,.xlsm,.csv" hidden></label></div>
        <div class="note-editor">
          <div class="field-row"><div class="field"><label for="materialKind">材料の種類</label><select class="select-input" id="materialKind"><option>DMO・地域から聞いたこと</option><option>学生の気づき</option><option>論文・既存資料</option><option>分析メモ</option></select></div><div class="field"><label for="materialTitle">資料名</label><input class="text-input" id="materialTitle" placeholder="例：DMO担当者ヒアリング"></div></div>
          <div class="field"><label for="materialContent">分かったこと・気づいたこと</label><textarea class="text-area short" id="materialContent" placeholder="事実と解釈を分けて入力してください"></textarea></div>
          <div class="button-row"><button class="button button-secondary" id="addMaterialNote" type="button">材料を追加</button></div>
        </div>
      </div>
      <div class="material-list">${project.materials.length ? project.materials.map(material => `<div class="material-row"><span class="material-kind">${esc(material.kind)}</span><span><strong>${esc(material.title)}</strong><small>${material.kind === 'アンケートデータ' ? `${material.summary?.rows || 0}回答・${material.summary?.columnCount || 0}列${material.summary?.studyProfile ? `・${esc(material.summary.studyProfile.population)}／${esc(material.summary.studyProfile.recruitment)}／${esc(material.summary.studyProfile.period)}` : '・調査条件 未登録'}` : esc(material.content.slice(0, 100))}</small></span>${material.dataset ? `<button class="button button-secondary" data-analyze-material="${esc(material.id)}" type="button">このデータを分析</button>` : ''}</div>`).join('') : '<div class="empty">材料はまだありません</div>'}</div>
      ${project.analyses.length ? `<div class="analysis-history"><h3>保存した分析</h3>${project.analyses.map(item => `<details><summary>${esc(item.question || '分析結果')}<small>${esc(String(item.created_at).slice(0,16).replace('T',' '))}</small></summary><div class="history-body"><strong>${esc(item.result?.headline || '分析結果')}</strong>${(item.result?.claims || []).map(claim => `<p>${esc(claim.statement || claim.text || '')}</p>`).join('')}<small>回答 ${esc(item.result?.rows ?? '—')}件</small></div></details>`).join('')}</div>` : ''}
      <div class="button-row"><button class="button button-primary" id="organizeProject" type="button" ${project.materials.length ? '' : 'disabled'}>${icons.wand} 材料を整理する</button></div>
    </section>
    ${organization ? renderOrganization(organization) : ''}
    ${state.researchPlan ? renderConsolidatedPlan(state.researchPlan) : ''}`;
  bindCommon();
  bindProject();
}

function renderOrganization(organization) {
  const selected = new Set(state.selectedCandidateIds);
  return `<section class="panel" id="organizationPanel">
    <div class="panel-head"><div><p class="panel-kicker">STEP 2・EVIDENCE MAP</p><h2>分かっていること／まだ言えないこと</h2><p>${esc(organization.summary)}</p></div><span class="privacy-pill">人が根拠を確認</span></div>
    ${organization.cautions.length ? `<div class="notice">${icons.alert}<span>${organization.cautions.map(esc).join('<br>')}</span></div>` : ''}
    <div class="evidence-columns">
      <div><h3>分かっていること</h3><div class="evidence-list">${organization.known.map(item => `<article class="evidence-card"><strong>${esc(item.statement)}</strong><small>根拠：${esc(item.evidenceType)}／状態：${esc(item.strength)}／範囲：${esc(item.scope)}</small><span>出典：${esc(item.source)}</span></article>`).join('') || '<div class="empty">確認できる材料がありません</div>'}</div></div>
      <div><h3>まだ言えないこと</h3><div class="evidence-list">${organization.unknown.map(item => `<article class="gap-card"><strong>${esc(item.gap)}</strong><span>${esc(item.why)}</span></article>`).join('')}</div></div>
    </div>
  </section>
  <section class="panel" id="candidatePanel">
    <div class="panel-head"><div><p class="panel-kicker">STEP 3・VALIDATION OPTIONS</p><h2>次に確かめる候補</h2><p>結果によって判断が変わる候補を、1〜4件選んでください。</p></div><span class="privacy-pill">選択 ${selected.size}件</span></div>
    <div class="candidate-list">${organization.candidates.map(item => `<label class="candidate-card ${selected.has(item.id) ? 'selected' : ''}"><input type="checkbox" data-candidate-id="${esc(item.id)}" ${selected.has(item.id) ? 'checked' : ''}><span class="candidate-priority">${item.priority}</span><span class="candidate-main"><strong>${esc(item.hypothesis)}</strong><small>${esc(item.gap)}</small><span>${esc(item.method)}・負担 ${esc(item.effort)}・判断影響 ${esc(item.decisionImpact)}</span></span></label>`).join('')}</div>
    <div class="button-row"><button class="button button-primary" id="consolidateResearch" type="button" ${selected.size ? '' : 'disabled'}>${icons.check} 選んだ仮説を1つの調査にまとめる</button></div>
  </section>`;
}

function renderConsolidatedPlan(plan) {
  return `<section class="panel accent-panel" id="researchPlanPanel"><div class="result-hero"><div><p class="panel-kicker">STEP 4・RESEARCH PLAN</p><h2>${esc(plan.instrumentType)}：${plan.hypotheses.length}仮説を${plan.questions.length}問で検証</h2></div><div class="result-meta">推定 ${plan.estimatedMinutes}分</div></div><div class="claim"><strong>判断方法</strong><small>${esc(plan.decisionRule)}</small></div></section>
  <section class="panel"><div class="panel-head"><div><p class="panel-kicker">QUESTION SET</p><h2>統合した設問</h2><p>共通設問を再利用し、小さいアンケートを何度も行わない構成です。</p></div></div>
    <div class="question-list">${plan.questions.map((q, i) => `<div class="question-card"><span class="q-id">Q${i + 1}</span><div><h3>${esc(q.text)}</h3><p>${esc(q.type)}・${esc(q.options.join('／') || '記述')}</p></div><div class="q-meta"><div><strong>目的</strong><span>${esc(q.purpose)}</span></div></div></div>`).join('')}</div>
    ${plan.separateFollowups?.length ? `<div class="notice">${icons.alert}<span>別枠で検討：${plan.separateFollowups.map(x => `${esc(x.hypothesis)}（${esc(x.method)}）`).join('<br>')}</span></div>` : ''}
  </section>`;
}

function bindProject() {
  document.querySelector('#projectDataset')?.addEventListener('change', uploadProjectDataset);
  document.querySelector('#addMaterialNote')?.addEventListener('click', addProjectNote);
  document.querySelector('#organizeProject')?.addEventListener('click', organizeProject);
  document.querySelectorAll('[data-analyze-material]').forEach(button => button.addEventListener('click', () => analyzeProjectMaterial(button.dataset.analyzeMaterial)));
  document.querySelectorAll('[data-candidate-id]').forEach(input => input.addEventListener('change', event => {
    const id = event.target.dataset.candidateId;
    const current = new Set(state.selectedCandidateIds);
    event.target.checked ? current.add(id) : current.delete(id);
    if (current.size > 4) { event.target.checked = false; return toast('選べる候補は4件までです', true); }
    state.selectedCandidateIds = [...current];
    render();
  }));
  document.querySelector('#consolidateResearch')?.addEventListener('click', consolidateResearch);
}

async function refreshProject() {
  const data = await api(`/api/projects/${state.currentProjectId}`);
  state.project = data.project;
}

async function uploadProjectDataset(event) {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const data = await fileToBase64(file);
    await api(`/api/projects/${state.currentProjectId}/materials/dataset`, {name:file.name, data, population:document.querySelector('#surveyPopulation').value.trim(), recruitment:document.querySelector('#surveyRecruitment').value.trim(), period:document.querySelector('#surveyPeriod').value.trim()});
    await refreshProject(); render(); toast(`${file.name} を保存しました`);
  } catch (error) { toast(error.message, true); }
}

async function addProjectNote() {
  try {
    await api(`/api/projects/${state.currentProjectId}/materials/note`, {kind:document.querySelector('#materialKind').value, title:document.querySelector('#materialTitle').value.trim(), content:document.querySelector('#materialContent').value.trim()});
    await refreshProject(); render(); toast('材料を追加しました');
  } catch (error) { toast(error.message, true); }
}

async function organizeProject() {
  try {
    const data = await api(`/api/projects/${state.currentProjectId}/organize`, {});
    state.project.organization = data.organization;
    state.selectedCandidateIds = [];
    state.researchPlan = null;
    render(); setTimeout(() => document.querySelector('#organizationPanel')?.scrollIntoView({behavior:'smooth',block:'start'}), 30);
  } catch (error) { toast(error.message, true); }
}

function analyzeProjectMaterial(materialId) {
  const material = state.project.materials.find(item => item.id === materialId);
  state.dataset = material.dataset;
  state.plan = null; state.result = null; state.analysisQuestion = '';
  initGoalDialogue();
  state.currentMaterialId=materialId;
  state.conversationDataReady=true;
  setView('goal-chat');
  sendGoalMessage(state.project.decision_question || '熱海の学生調査で何を明らかにするか整理したい');
}

async function consolidateResearch() {
  const candidates = state.project.organization.candidates.filter(item => state.selectedCandidateIds.includes(item.id));
  try {
    const data = await api(`/api/projects/${state.currentProjectId}/research-plans`, {objective:state.project.decision_question, target:state.project.target, candidates, duration:7});
    state.researchPlan = data.researchPlan;
    await refreshProject();
    state.researchPlan = data.researchPlan;
    render(); setTimeout(() => document.querySelector('#researchPlanPanel')?.scrollIntoView({behavior:'smooth',block:'start'}), 30);
  } catch (error) { toast(error.message, true); }
}

function pageHead(eyebrow, title, description) {
  return `<div class="page-head"><div><p class="eyebrow">${esc(eyebrow)}</p><h1>${title}</h1><p>${description}</p></div><button class="back-button" id="backHome" type="button">${icons.back} ホーム</button></div>`;
}

function steps(active) {
  const labels = ['データと目的', '分析計画を確認', '結果と次の設問'];
  return `<div class="stepper">${labels.map((label, i) => `<div class="step ${i + 1 < active ? 'done' : ''} ${i + 1 === active ? 'active' : ''}"><b>${i + 1 < active ? '✓' : i + 1}</b>${label}</div>`).join('')}</div>`;
}

function datasetBlock() {
  if (!state.dataset) {
    return `<label class="upload-zone" for="surveyFile">${icons.upload}<strong>ExcelまたはCSVを選択</strong><span>.xlsx / .xlsm / .csv・30MB以内</span><input id="surveyFile" type="file" accept=".xlsx,.xlsm,.csv" hidden></label>`;
  }
  return `<div class="dataset-card"><div><strong>${esc(state.dataset.filename)}</strong><span>読み込み完了。個票はブラウザとローカルサーバーのメモリ上だけで扱います。</span></div><div class="dataset-stats"><div><b>${state.dataset.rows}</b><small>回答</small></div><div><b>${state.dataset.columnCount}</b><small>設問・列</small></div><div><b>${state.dataset.duplicates}</b><small>重複候補</small></div></div></div>`;
}

function renderAnalysis() {
  const active = state.result ? 3 : state.plan ? 2 : 1;
  app.innerHTML = `
    ${pageHead('ANALYZE', 'アンケート結果を分析する', '分析方法を知らなくても大丈夫です。まず、データと知りたいことを入力してください。')}
    ${steps(active)}
    <section class="panel"><div class="panel-head"><div><p class="panel-kicker">STEP 1</p><h2>回答データ</h2><p>列名と回答数だけを確認し、個票は保存しません。</p></div></div>${datasetBlock()}</section>
    <section class="panel">
      <div class="panel-head"><div><p class="panel-kicker">ANALYSIS QUESTION</p><h2>このアンケートから何を知りたいですか？</h2><p>施策案ではなく、データで確認したい問いを書いてください。</p></div></div>
      <div class="field"><label for="analysisQuestion">知りたいこと</label><textarea class="text-area" id="analysisQuestion" placeholder="例：年代によって、熱海への来訪目的や情報源に違いがあるか知りたい">${esc(state.analysisQuestion)}</textarea></div>
      <div class="prompt-examples">
        <button class="prompt-chip" data-prompt="年代によって、熱海への来訪目的や情報源に違いがあるか知りたい" type="button">年代別の目的・情報源</button>
        <button class="prompt-chip" data-prompt="熱海を検討した学生が、どの段階で訪問に至っていないか知りたい" type="button">検討から訪問までの段階</button>
        <button class="prompt-chip" data-prompt="平日に訪れたいと思う条件として、何が多いか知りたい" type="button">平日来訪の条件</button>
      </div>
      <div class="button-row"><button class="button button-primary" id="makePlan" type="button" ${!state.dataset || state.busy ? 'disabled' : ''}>${state.busy ? '<span class="spinner"></span>確認中' : `${icons.wand} 分析計画を作る`}</button></div>
    </section>
    ${state.plan ? renderPlan() : ''}
    ${state.result ? renderResult() : ''}`;
  bindCommon();
  bindAnalysis();
}

function renderPlan() {
  const plan = state.plan;
  if (plan.type === 'unavailable') {
    return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">STEP 2</p><h2>${esc(plan.label)}</h2></div></div><div class="notice">${icons.alert}<span>${esc(plan.reason)}</span></div></section>`;
  }
  const info = plan.columnInfo || {};
  let columnText = '';
  if (plan.type === 'journey') {
    columnText = [
      ['訪問経験', (info.visitColumns || []).map(c => c.shortName).join('／')],
      ['認知', info.awarenessColumn?.shortName || '—'],
      ['検討経験', info.considerationColumn?.shortName || '—'],
      ['未訪問理由', info.barrierColumn?.shortName || '未測定'],
    ].map(([k,v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  } else {
    columnText = `<dt>比較軸</dt><dd>${esc(info.groupColumn?.shortName || 'なし')}</dd><dt>分析対象</dt><dd>${(info.metricColumns || []).map(c => esc(c.shortName)).join('<br>')}</dd>`;
  }
  const options = (plan.columnOptions || []).map(c => `<option value="${esc(c.name)}">${esc(c.shortName)}（回答 ${c.nonEmpty}）</option>`).join('');
  const selectors = plan.type === 'journey' ? '' : `
    <div class="select-grid">
      <div class="field"><label for="groupColumn">比較に使う設問</label><select class="select-input" id="groupColumn"><option value="">比較しない</option>${options}</select></div>
      <div class="field"><label for="metricColumn">分析する設問</label><select class="select-input" id="metricColumn">${options}</select></div>
    </div>`;
  return `<section class="panel" id="planPanel">
    <div class="panel-head"><div><p class="panel-kicker">STEP 2・人が確認</p><h2>${esc(plan.label)}</h2><p>${esc(plan.reason)}</p></div><span class="privacy-pill">提案確度 ${esc(plan.confidence)}</span></div>
    <dl class="plan-summary"><dt>知りたいこと</dt><dd>${esc(plan.question)}</dd><dt>分析方法</dt><dd>${esc(plan.label)}</dd>${columnText}</dl>
    ${renderUsedQuestions(plan.usedQuestions)}
    ${selectors}
    <div class="notice">${icons.alert}<span>${esc(plan.approvalNote || '列と方法を確認してください。')}</span></div>
    <div class="button-row"><button class="button button-primary" id="runAnalysis" type="button" ${state.busy ? 'disabled' : ''}>${state.busy ? '<span class="spinner"></span>計算中' : `${icons.check} この計画で分析する`}</button></div>
  </section>`;
}

function renderResult() {
  const result = state.result;
  const main = result.type === 'journey' ? renderJourney(result) : renderAnalyses(result);
  return `
    <section class="panel accent-panel" id="resultPanel">
      <div class="result-hero"><div><p class="panel-kicker">ANALYSIS RESULT</p><h2>${esc(result.headline)}</h2></div><div class="result-meta">回答 ${result.rows}件<br>重複候補 ${result.duplicates}件</div></div>
      <div class="claim-list">${result.claims.map(c => `<div class="claim"><strong>${esc(c.text)}</strong><small>根拠：${esc(c.evidence)}</small></div>`).join('')}</div>
    </section>
    ${main}
    <section class="two-col">
      <div class="truth-box can"><h3>${icons.check} このデータから言えること</h3><ul>${result.canSay.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
      <div class="truth-box cannot"><h3>${icons.alert} このデータだけでは言えないこと</h3><ul>${result.cannotSay.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><p class="panel-kicker">NEXT SURVEY</p><h2>不足情報を、次の設問へ</h2><p>ここで生成する設問は、施策を決めるためではなく、残った仮説を検証するための案です。</p></div><button class="button button-secondary" id="openQuestionnaire" type="button">設問作成画面で編集</button></div>
      <div class="question-list">${result.nextQuestions.map((q, i) => `<div class="question-card"><span class="q-id">Q${i + 1}</span><div><h3>${esc(q.text)}</h3><p>${esc(q.type)}</p></div><div class="q-meta"><div><strong>目的</strong><span>${esc(q.purpose)}</span></div></div></div>`).join('')}</div>
      ${renderTrace(result.trace)}
    </section>`;
}

function renderJourney(result) {
  const maxBarrier = Math.max(1, ...result.barriers.map(b => b.n));
  return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">RESPONDENT STATES</p><h2>訪問経験・認知・検討による回答者構成</h2><p>時系列の離脱率ではなく、回答時点の状態を重複なく分類しています。</p></div></div>
    <div class="segment-grid">${result.segments.map(s => `<div class="segment-card ${s.id === 'considered' ? 'featured' : ''}"><span>${esc(s.label)}</span><b>${s.n}<small>人・${s.share}%</small></b><small>${esc(s.definition)}</small></div>`).join('')}</div>
  </section>
  <section class="panel"><div class="panel-head"><div><p class="panel-kicker">BARRIERS</p><h2>検討した未訪問者が挙げた理由</h2><p>複数回答を含むため、人数の合計は対象者数と一致しない場合があります。</p></div></div>
    ${result.barriers.length ? `<div class="bar-list">${result.barriers.map(b => `<div class="bar-row"><span>${esc(b.label)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, b.n / maxBarrier * 100)}%"></div></div><b>${b.n}件</b></div>`).join('')}</div>` : '<div class="empty">理由を測る回答がありません</div>'}
  </section>`;
}

function renderAnalyses(result) {
  return result.analyses.map(analysis => {
    if (result.type === 'frequency') {
      const max = Math.max(1, ...analysis.rows.map(row => row.n));
      return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">DISTRIBUTION</p><h2>${esc(analysis.shortName)}</h2><p>回答 ${analysis.answered}人・未回答 ${analysis.missing}人${analysis.multi ? '・複数回答' : ''}</p></div></div><div class="bar-list">${analysis.rows.map(row => `<div class="bar-row"><span>${esc(row.label)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, row.n / max * 100)}%"></div></div><b>${row.n}件</b></div>`).join('')}</div></section>`;
    }
    const summaries = analysis.groupSummaries || analysis.groups.map(group => ({group}));
    return `<section class="panel"><div class="panel-head"><div><p class="panel-kicker">CROSS TABULATION</p><h2>${esc(analysis.metricLabel)} × ${esc(analysis.groupLabel)}</h2><p>実際に1件以上あった回答項目をすべて表示しています。割合の分母は、各グループでこの設問に回答した人数です。</p></div></div><div class="table-scroll"><table class="data-table"><thead><tr><th>回答項目（全${analysis.rows.length}件）</th>${summaries.map(s => `<th><span class="group-head">${esc(s.group)}</span>${s.answered != null ? `<small>回答者 ${s.answered}人<br>${analysis.multi ? `延べ選択 ${s.selections}件` : `未回答 ${s.missing}人`}</small>` : ''}</th>`).join('')}</tr></thead><tbody>${analysis.rows.map(row => `<tr><td>${esc(row.label)}</td>${row.cells.map(cell => `<td class="number"><strong>${cell.share}%</strong><br><small>${cell.n}人が選択／回答者${cell.denominator}人</small></td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`;
  }).join('');
}

function renderTrace(trace = []) {
  return `<details class="trace-details"><summary>分析の裏側：使用した設問を確認</summary>${trace.map(row => `<div class="trace-row"><span>${esc(row.role)}</span><code>${(row.columns || []).filter(Boolean).map(esc).join('<br>') || 'なし'}</code></div>`).join('')}</details>`;
}

function bindCommon() {
  document.querySelector('#backHome')?.addEventListener('click', () => { state.projectsLoaded = false; setView('home'); });
}

function bindAnalysis() {
  const fileInput = document.querySelector('#surveyFile');
  if (fileInput) fileInput.onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    state.busy = true;
    render();
    try {
      const base64 = await fileToBase64(file);
      const data = await api('/api/datasets', {name: file.name, data: base64});
      state.dataset = data.dataset;
      state.plan = null;
      state.result = null;
      toast(`${file.name} を読み込みました`);
    } catch (error) { toast(error.message, true); }
    finally { state.busy = false; render(); }
  };
  document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => {
    const input = document.querySelector('#analysisQuestion');
    input.value = button.dataset.prompt;
    state.analysisQuestion = input.value;
    input.focus();
  });
  document.querySelector('#analysisQuestion')?.addEventListener('input', event => { state.analysisQuestion = event.target.value; });
  document.querySelector('#makePlan')?.addEventListener('click', makePlan);
  document.querySelector('#runAnalysis')?.addEventListener('click', runAnalysis);
  document.querySelector('#openQuestionnaire')?.addEventListener('click', () => {
    const focus = state.result?.nextQuestions?.[0]?.purpose || state.analysisQuestion;
    state.questionnaireSeed = focus;
    setView('questionnaire');
  });
  if (state.plan && state.plan.type !== 'journey') {
    const group = document.querySelector('#groupColumn');
    const metric = document.querySelector('#metricColumn');
    if (group) group.value = state.plan.columns.groupColumn || '';
    if (metric) metric.value = state.plan.columns.metricColumns?.[0] || '';
  }
}

// 問いを確定したときに「この問いなら、この設問を見ます」と先に示すパネル。
// 選定理由（設問文に一致／選択肢に一致）まで出して、外していたら人が直せるようにする。
function renderUsedQuestions(items) {
  if (!items || !items.length) return '';
  return `<div class="used-questions">
    <p class="used-questions-head"><strong>使う設問：</strong><small>この問いに答えるために、次の設問を見ます</small></p>
    <ul>${items.map(item => `<li>
      <span class="used-role ${item.role === '比較軸' ? 'is-group' : ''}">${esc(item.role)}</span>
      <span class="used-name">${esc(item.shortName || item.name)}</span>
      ${Number.isFinite(item.answered) ? `<span class="used-n">回答 ${item.answered}</span>` : ''}
      <small class="used-reason">${esc(item.reason || '')}</small>
    </li>`).join('')}</ul>
    <small class="used-questions-note">違う設問を見てほしいときは、下の選択で差し替えてください。</small>
  </div>`;
}

async function makePlan() {
  state.analysisQuestion = document.querySelector('#analysisQuestion').value.trim();
  if (!state.analysisQuestion) return toast('知りたいことを入力してください', true);
  state.busy = true; state.plan = null; state.result = null; render();
  try {
    const data = await api('/api/analysis/plan', {datasetId: state.dataset.id, question: state.analysisQuestion});
    state.plan = data.plan;
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; render(); setTimeout(() => document.querySelector('#planPanel')?.scrollIntoView({behavior:'smooth', block:'start'}), 30); }
}

async function runAnalysis() {
  if (state.plan.type !== 'journey') {
    const group = document.querySelector('#groupColumn').value;
    const metric = document.querySelector('#metricColumn').value;
    state.plan.columns.groupColumn = group;
    state.plan.columns.metricColumns = [metric];
    state.plan.type = group ? 'crosstab' : 'frequency';
  }
  state.busy = true; state.result = null; render();
  try {
    const data = await api('/api/analysis/run', {datasetId: state.dataset.id, plan: state.plan, projectId: state.currentProjectId});
    state.result = data.result;
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; render(); setTimeout(() => document.querySelector('#resultPanel')?.scrollIntoView({behavior:'smooth', block:'start'}), 30); }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('ファイルを読み込めませんでした'));
    reader.readAsDataURL(file);
  });
}

function renderQuestionnaire() {
  const q = state.questionnaire;
  const form = state.questionnaireForm || {};
  app.innerHTML = `
    ${pageHead('DESIGN', '検証用の調査を設計する', '調査目的を仮説に変え、学生が採用した仮説から短く自然な設問を作ります。')}
    <section class="panel">
      <div class="panel-head"><div><p class="panel-kicker">STEP 1・調査目的</p><h2>何を明らかにしたいですか？</h2><p>仮説をうまく書けなくても大丈夫です。目的から候補を提案します。</p></div></div>
      <div class="field"><label for="objective">調査目的</label><textarea class="text-area" id="objective" placeholder="例：学生が熱海を選ぶ理由と、選ばない理由を知りたい">${esc(form.objective || state.questionnaireSeed || q?.objective || '')}</textarea></div>
      <div class="field-row">
        <div class="field"><label for="target">誰に聞くか</label><input class="text-input" id="target" value="${esc(form.target || q?.target || '首都圏の大学生')}" placeholder="例：芝浦工業大学の学生"></div>
        <div class="field"><label for="duration">回答時間</label><select class="select-input" id="duration"><option value="3" ${form.duration === 3 ? 'selected' : ''}>3分</option><option value="5" ${!form.duration || form.duration === 5 ? 'selected' : ''}>5分</option><option value="8" ${form.duration === 8 ? 'selected' : ''}>8分</option><option value="10" ${form.duration === 10 ? 'selected' : ''}>10分</option></select></div>
      </div>
      <div class="field"><label for="known">既に分かっていること <small>任意</small></label><textarea class="text-area" id="known" placeholder="例：検討したが訪れていない学生がいる">${esc(form.known || q?.known || '')}</textarea></div>
      <div class="prompt-examples"><button class="prompt-chip" id="questionnaireExample" type="button">熱海の費用・情報仮説を入力</button></div>
      <div class="button-row"><button class="button button-primary" id="suggestHypotheses" type="button" ${state.busy ? 'disabled' : ''}>${state.busy ? '<span class="spinner"></span>整理中' : `${icons.wand} 仮説候補を作る`}</button></div>
    </section>
    ${state.hypothesisSuggestion ? renderHypothesisSuggestions(state.hypothesisSuggestion) : ''}
    ${q ? renderQuestionnaireResult(q) : ''}`;
  bindCommon();
  bindQuestionnaire();
}

function renderHypothesisSuggestions(suggestion) {
  const selected = state.selectedHypothesis || suggestion.hypotheses[0]?.id;
  return `<section class="panel" id="hypothesisPanel">
    <div class="panel-head"><div><p class="panel-kicker">STEP 2・学生が選ぶ</p><h2>仮説候補</h2><p>近い候補を選び、必要なら文章を直してください。</p></div><span class="privacy-pill">候補 ${suggestion.hypotheses.length}件</span></div>
    <div class="hypothesis-list">${suggestion.hypotheses.map((item, i) => `<label class="hypothesis-card ${item.id === selected ? 'selected' : ''}">
      <input type="radio" name="hypothesis" value="${esc(item.id)}" ${item.id === selected ? 'checked' : ''}>
      <span class="hypothesis-id">${esc(item.id)}</span>
      <span class="hypothesis-main"><textarea data-hypothesis-text data-i="${i}" rows="2">${esc(item.text)}</textarea><small>${esc(item.evidenceNeeded)}・推奨：${esc(item.recommendedMethod)}</small></span>
    </label>`).join('')}</div>
    <div class="notice">${icons.alert}<span>候補は仮案です。学生が修正・採用した後に設問を作ります。</span></div>
    <div class="button-row"><button class="button button-primary" id="generateQuestionnaire" type="button" ${state.busy ? 'disabled' : ''}>${icons.check} この仮説で質問案を作る</button></div>
  </section>`;
}

function renderQuestionnaireResult(q) {
  return `<section class="panel accent-panel" id="questionnaireResult"><div class="result-hero"><div><p class="panel-kicker">STEP 3・${esc(q.instrumentType)}案</p><h2>${esc(q.hypothesis)}</h2></div><div class="result-meta">${q.questions.length}問<br>推定 ${q.estimatedMinutes}分</div></div><div class="claim"><strong>文章ルール</strong><small>${esc(q.wordingRule)}</small></div></section>
    <section class="panel"><div class="panel-head"><div><p class="panel-kicker">QUESTION MAP</p><h2>質問・目的・分析方法</h2><p>回答者には質問文と必要な選択肢だけを表示します。目的と分析方法は学生向けです。</p></div><div class="button-row compact"><button class="button button-secondary" id="recheckQuality" type="button" ${state.qualityDirty ? '' : 'disabled'}>品質を再確認</button><button class="button button-secondary" id="downloadCsv" type="button" ${state.qualityDirty ? 'disabled' : ''}>CSVで保存</button></div></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>ID</th><th>設問</th><th>形式・選択肢</th><th>目的</th><th>回答後の分析</th><th>品質</th></tr></thead><tbody>${q.questions.map((item, i) => { const check = q.qualityChecks?.[i] || {status:'—', issues:[]}; return `<tr><td><strong>${esc(item.id)}</strong></td><td><textarea data-q="text" data-i="${i}" rows="3">${esc(item.text)}</textarea>${item.branch ? `<small class="branch-note">表示：${esc(item.branch)}</small>` : ''}</td><td><strong>${esc(item.type)}</strong><br><small>${esc(item.options.join('／') || '記述回答')}</small></td><td><textarea data-q="purpose" data-i="${i}" rows="3">${esc(item.purpose)}</textarea></td><td><textarea data-q="analysis" data-i="${i}" rows="3">${esc(item.analysis)}</textarea></td><td><span class="quality-badge ${check.status === 'OK' ? 'ok' : 'warn'}">${esc(check.status)}</span>${check.issues.map(x => `<small>${esc(x)}</small>`).join('<br>')}</td></tr>`; }).join('')}</tbody></table></div>
      ${q.warnings.length ? `<div class="notice">${icons.alert}<span>${q.warnings.map(esc).join('<br>')}</span></div>` : ''}
    </section>`;
}

function bindQuestionnaire() {
  document.querySelector('#questionnaireExample')?.addEventListener('click', () => {
    document.querySelector('#objective').value = '費用が高いことと、旅行費用の総額が分からないことのどちらが、大学生の熱海訪問の障壁になっているか検証したい';
    document.querySelector('#target').value = '熱海を検討したことがある首都圏の大学生';
    document.querySelector('#known').value = '学生アンケートでは、熱海を検討したが未訪問の回答者がおり、費用や何ができるか分からないことが理由に挙がった。';
  });
  document.querySelector('#suggestHypotheses')?.addEventListener('click', suggestHypotheses);
  document.querySelector('#generateQuestionnaire')?.addEventListener('click', generateQuestionnaire);
  document.querySelectorAll('input[name="hypothesis"]').forEach(input => input.addEventListener('change', event => {
    state.selectedHypothesis = event.target.value;
    render();
  }));
  document.querySelectorAll('[data-hypothesis-text]').forEach(input => input.addEventListener('input', event => {
    state.hypothesisSuggestion.hypotheses[Number(event.target.dataset.i)].text = event.target.value;
  }));
  document.querySelectorAll('[data-q]').forEach(input => input.addEventListener('input', event => {
    const item = state.questionnaire.questions[Number(event.target.dataset.i)];
    item[event.target.dataset.q] = event.target.value;
    if (event.target.dataset.q === 'text') {
      state.qualityDirty = true;
      document.querySelector('#downloadCsv').disabled = true;
      document.querySelector('#recheckQuality').disabled = false;
      document.querySelectorAll('.quality-badge').forEach(badge => { badge.textContent = '未確認'; badge.className = 'quality-badge warn'; });
    }
  }));
  document.querySelector('#recheckQuality')?.addEventListener('click', recheckQuality);
  document.querySelector('#downloadCsv')?.addEventListener('click', downloadQuestions);
}

async function suggestHypotheses() {
  const body = {
    objective: document.querySelector('#objective').value.trim(),
    target: document.querySelector('#target').value.trim(),
    duration: Number(document.querySelector('#duration').value),
    known: document.querySelector('#known').value.trim(),
  };
  state.questionnaireForm = body;
  state.questionnaireSeed = body.objective;
  state.busy = true; state.hypothesisSuggestion = null; state.questionnaire = null; render();
  try {
    const data = await api('/api/hypotheses/suggest', body);
    state.hypothesisSuggestion = data.suggestion;
    state.selectedHypothesis = data.suggestion.hypotheses[0]?.id || null;
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; render(); setTimeout(() => document.querySelector('#hypothesisPanel')?.scrollIntoView({behavior:'smooth', block:'start'}), 30); }
}

async function generateQuestionnaire() {
  const selected = state.hypothesisSuggestion?.hypotheses.find(item => item.id === state.selectedHypothesis);
  const body = {...state.questionnaireForm, hypothesis: selected?.text || '', researchMethod: selected?.recommendedMethod || 'アンケート'};
  state.questionnaireSeed = body.objective;
  state.busy = true; state.questionnaire = null; render();
  try {
    const data = await api('/api/questionnaire/generate', body);
    state.questionnaire = data.questionnaire;
    state.qualityDirty = false;
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; render(); setTimeout(() => document.querySelector('#questionnaireResult')?.scrollIntoView({behavior:'smooth', block:'start'}), 30); }
}

async function recheckQuality() {
  try {
    const data = await api('/api/questionnaire/check', {objective: state.questionnaire.objective, questions: state.questionnaire.questions});
    state.questionnaire.qualityChecks = data.qualityChecks;
    state.qualityDirty = false;
    render();
    toast('設問の品質を再確認しました');
  } catch (error) { toast(error.message, true); }
}

function downloadQuestions() {
  if (state.qualityDirty) return toast('設問を編集したため、品質を再確認してください', true);
  const rows = [['ID','設問','回答形式','選択肢','目的','分析方法'], ...state.questionnaire.questions.map(q => [q.id,q.text,q.type,q.options.join(' / '),q.purpose,q.analysis])];
  const csv = '\ufeff' + rows.map(row => row.map(value => `"${String(value).replaceAll('"','""')}"`).join(',')).join('\r\n');
  const blob = new Blob([csv], {type:'text/csv;charset=utf-8'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = 'survey-question-draft.csv';
  link.click();
  URL.revokeObjectURL(link.href);
  toast('設問案をCSVで保存しました');
}

document.querySelector('#brandHome').onclick = goHome;
loadWorkspace();
