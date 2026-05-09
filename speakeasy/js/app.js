const AppState = {
  environments: [],
  accentOptions: [],
  selectedAccent: 'american_ga',
  currentEnv: null,
  currentKeyword: null,
  currentStatement: null,
  currentEnvData: null,
  view: 'home'
};

window.AppState = AppState;

const App = {
  async init() {
    await this.loadEnvironments();
    this.renderHome();
    this.bindGlobalEvents();
    this.renderBreadcrumb();
    if (window.speechEngine) {
      window.speechEngine.updateAccent(AppState.selectedAccent);
    }
  },

  async loadEnvironments() {
    try {
      const resp = await fetch('data/environments.json');
      const data = await resp.json();
      AppState.environments = data.environments || [];
      AppState.accentOptions = data.accent_options || [];
    } catch (e) {
      console.error('Failed to load environments index:', e);
    }
  },

  async loadEnvData(envId) {
    const env = AppState.environments.find(e => e.id === envId);
    if (!env) return null;
    try {
      const resp = await fetch(env.file);
      const data = await resp.json();
      return data;
    } catch (e) {
      console.error('Failed to load environment data:', e);
      return null;
    }
  },

  navigate(view, data) {
    AppState.view = view;
    if (data?.env) AppState.currentEnv = data.env;
    if (data?.keyword) AppState.currentKeyword = data.keyword;
    if (data?.statement) AppState.currentStatement = data.statement;
    if (data?.envData) AppState.currentEnvData = data.envData;
    this.render();
  },

  render() {
    this.renderBreadcrumb();
    const screens = ['home', 'keywords', 'statements', 'speaking'];
    screens.forEach(s => {
      document.getElementById(`screen-${s}`).classList.toggle('active', s === AppState.view);
    });

    switch (AppState.view) {
      case 'home': this.renderHome(); break;
      case 'keywords': this.renderKeywords(); break;
      case 'statements': this.renderStatements(); break;
      case 'speaking': this.renderSpeaking(); break;
    }
  },

  showToast(msg) {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.classList.add('visible');
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
  },

  renderHome() {
    const container = document.getElementById('envGrid');
    const envs = AppState.environments;

    if (!envs.length) {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon">📚</div><p>No environments loaded yet.</p></div>';
      return;
    }

    container.innerHTML = envs.map(env => `
      <div class="env-card" data-env-id="${env.id}">
        <div class="env-icon" style="background: ${env.color}">
          ${this.getEnvIcon(env.icon)}
        </div>
        <h3>${env.name}</h3>
        <p>${env.description || ''}</p>
        <span class="env-badge">${this.getWordCount(env)} keywords</span>
      </div>
    `).join('');

    container.querySelectorAll('.env-card').forEach(card => {
      card.addEventListener('click', () => this.onEnvSelect(card.dataset.envId));
    });

    document.getElementById('headerTitle').textContent = 'SpeakEasy';
    document.getElementById('headerSubtitle').textContent = 'Choose a scenario to practice';
  },

  getEnvIcon(icon) {
    const icons = {
      utensils: '🍽️',
      taxi: '🚕',
      supermarket: '🛒',
      home: '🏠',
      hospital: '🏥',
      hotel: '🏨',
      bank: '🏦',
      airport: '✈️',
      default: '📖'
    };
    return icons[icon] || icons.default;
  },

  getWordCount(env) {
    return '25';
  },

  async onEnvSelect(envId) {
    const data = await this.loadEnvData(envId);
    if (!data) {
      this.showToast('Failed to load environment data');
      return;
    }
    AppState.currentEnv = envId;
    AppState.currentEnvData = data;
    this.navigate('keywords', { env: envId, envData: data });
  },

  renderKeywords() {
    const data = AppState.currentEnvData;
    if (!data) return;

    const container = document.getElementById('keywordList');
    const descEl = document.getElementById('envDescription');
    const titleEl = document.getElementById('keywordsTitle');

    titleEl.textContent = data.environment_name || 'Keywords';

    descEl.innerHTML = `
      <p>${data.description || ''}</p>
      ${data.regional_notes?.vocabulary_differences ? this.renderRegionalDiffs(data.regional_notes.vocabulary_differences) : ''}
    `;

    const keywords = data.keywords || [];
    if (!keywords.length) {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon">🔤</div><p>No keywords in this environment yet.</p></div>';
      return;
    }

    container.innerHTML = keywords.map(kw => `
      <div class="keyword-item" data-kw-id="${kw.keyword_id}">
        <div class="keyword-word">${kw.word}</div>
        <div class="keyword-info">
          <div class="keyword-pos">${kw.part_of_speech || ''} · ${kw.difficulty || ''}</div>
          <div class="keyword-def">${kw.definition || ''}</div>
          ${(kw.tags || []).length ? `<div class="keyword-tags">${kw.tags.map(t => `<span class="tag">${t}</span>`).join('')}</div>` : ''}
        </div>
        <div class="keyword-arrow">›</div>
      </div>
    `).join('');

    container.querySelectorAll('.keyword-item').forEach(item => {
      item.addEventListener('click', () => {
        const kw = keywords.find(k => k.keyword_id === item.dataset.kwId);
        if (kw) {
          AppState.currentKeyword = kw;
          this.navigate('statements', { keyword: kw });
        }
      });
    });

    document.getElementById('headerTitle').textContent = data.environment_name || 'Keywords';
    document.getElementById('headerSubtitle').textContent = `${keywords.length} keywords to practice`;
  },

  renderRegionalDiffs(diffs) {
    return `
      <div class="regional-diff">
        <h4>Regional Vocabulary Differences</h4>
        ${Object.entries(diffs).map(([key, val]) => `
          <div class="diff-item"><strong>${key}:</strong> US: ${val.american} · UK: ${val.british}</div>
        `).join('')}
      </div>
    `;
  },

  renderStatements() {
    const kw = AppState.currentKeyword;
    if (!kw) return;

    const container = document.getElementById('statementList');
    const titleEl = document.getElementById('statementsTitle');
    const wordEl = document.getElementById('statementWord');

    wordEl.textContent = kw.word;
    titleEl.textContent = `"${kw.word}" — ${kw.definition || ''}`;

    const statements = kw.statements || [];
    if (!statements.length) {
      container.innerHTML = '<div class="empty-state"><p>No example statements for this keyword.</p></div>';
      return;
    }

    container.innerHTML = statements.map(stmt => {
      const accent = AppState.selectedAccent;
      const variant = stmt.accent_variants?.[accent];
      const ipa = variant?.ipa_transcription || '';
      const text = variant?.alt_text_american || stmt.text;

      return `
        <div class="statement-card" data-stmt-id="${stmt.statement_id}">
          <div class="statement-meta">
            <span class="difficulty-badge ${stmt.difficulty}">${stmt.difficulty}</span>
            <span style="font-size:11px;color:var(--text-muted)">${stmt.statement_id}</span>
          </div>

          <div class="statement-text">
            ${this.highlightKeyword(text, stmt.keyword_highlight_index)}
          </div>

          ${ipa ? `<div class="ipa-display">${ipa}</div>` : ''}

          ${stmt.usage_note ? `<div class="usg-note">💡 ${stmt.usage_note}</div>` : ''}
          ${stmt.regional_variant_note ? `<div class="regional-note">🌍 ${stmt.regional_variant_note}</div>` : ''}
          ${stmt.cultural_note ? `<div class="cultural-note collapsed"><div class="cultural-label">📖 Did you know? <span style="font-size:10px;color:var(--text-light)">(tap to expand)</span></div><div class="cultural-body mt-8">${stmt.cultural_note}</div></div>` : ''}

          <div class="audio-controls">
            <button class="audio-btn play-btn" title="Play" data-stmt-id="${stmt.statement_id}">▶</button>
            <button class="audio-btn pause-btn" title="Pause" data-stmt-id="${stmt.statement_id}">⏸</button>
            <button class="audio-btn stop-btn" title="Stop" data-stmt-id="${stmt.statement_id}">⏹</button>
            <button class="audio-btn repeat-btn" title="Repeat" data-stmt-id="${stmt.statement_id}">🔁</button>
            <button class="speak-btn speak-practice-btn" data-stmt-id="${stmt.statement_id}">🎤 Practice</button>
          </div>
        </div>
      `;
    }).join('');

    container.querySelectorAll('.play-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const stmt = statements.find(s => s.statement_id === btn.dataset.stmtId);
        if (stmt) this.playStatementAudio(stmt, btn);
      });
    });
    container.querySelectorAll('.pause-btn').forEach(btn => {
      btn.addEventListener('click', () => window.audioController.pause());
    });
    container.querySelectorAll('.stop-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        window.audioController.stop();
        this.clearPlayingStates();
      });
    });
    container.querySelectorAll('.repeat-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const stmt = statements.find(s => s.statement_id === btn.dataset.stmtId);
        if (stmt) this.playStatementAudio(stmt, btn);
      });
    });
    container.querySelectorAll('.speak-practice-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const stmt = statements.find(s => s.statement_id === btn.dataset.stmtId);
        if (stmt) {
          AppState.currentStatement = stmt;
          this.navigate('speaking', { statement: stmt });
        }
      });
    });

    container.querySelectorAll('.cultural-note').forEach(el => {
      el.addEventListener('click', () => el.classList.toggle('collapsed'));
    });

    document.getElementById('headerTitle').textContent = `"${kw.word}"`;
    document.getElementById('headerSubtitle').textContent = `${statements.length} example statements`;
  },

  highlightKeyword(text, highlightIndex) {
    if (!highlightIndex || !Array.isArray(highlightIndex) || highlightIndex.length < 2) {
      return text;
    }
    const [start, end] = highlightIndex;
    if (start < 0 || end > text.length || start >= end) return text;
    const before = text.slice(0, start);
    const kw = text.slice(start, end);
    const after = text.slice(end);
    return `${before}<span class="keyword-highlight">${kw}</span>${after}`;
  },

  playStatementAudio(stmt, btn) {
    window.audioController.stop();
    this.clearPlayingStates();

    const accent = AppState.selectedAccent;
    const variant = stmt.accent_variants?.[accent];

    if (variant?.audio_url) {
      window.audioController.setUseTTSFallback(false);
      btn.classList.add('playing');
      const audio = new Audio(variant.audio_url);
      audio.addEventListener('ended', () => btn.classList.remove('playing'));
      audio.addEventListener('error', () => {
        btn.classList.remove('playing');
        this.speakFallback(stmt);
      });
      audio.play().catch(() => {
        btn.classList.remove('playing');
        this.speakFallback(stmt);
      });
    } else {
      this.speakFallback(stmt, btn);
    }
  },

  speakFallback(stmt, btn) {
    const text = stmt.accent_variants?.[AppState.selectedAccent]?.alt_text_american || stmt.text;
    window.audioController.setUseTTSFallback(true);
    window.audioController.speakText(text, () => {
      if (btn) btn.classList.remove('playing');
    });
    if (btn) btn.classList.add('playing');
  },

  clearPlayingStates() {
    document.querySelectorAll('.audio-btn.playing').forEach(b => b.classList.remove('playing'));
  },

  renderSpeaking() {
    const stmt = AppState.currentStatement;
    if (!stmt) return;

    const container = document.getElementById('speakingScreen');
    const accent = AppState.selectedAccent;
    const variant = stmt.accent_variants?.[accent];
    const text = variant?.alt_text_american || stmt.text;

    container.innerHTML = `
      <div class="speaking-target">${this.highlightKeyword(text, stmt.keyword_highlight_index)}</div>

      ${variant?.ipa_transcription ? `<div class="ipa-display">${variant.ipa_transcription}</div>` : ''}

      <div class="speaking-recording-indicator" id="recordingIndicator">
        <span class="recording-dot"></span>
        <span>Listening...</span>
      </div>

      <div style="margin: 20px 0">
        <button class="speaking-big-btn record" id="recordBtn">🎤</button>
        <p style="font-size:13px;color:var(--text-light);margin-top:8px">Tap to start speaking</p>
      </div>

      <div class="speaking-progress" id="progressContainer" style="display:none">
        <div class="progress-bar">
          <div class="progress-fill" id="progressFill" style="width:0%"></div>
        </div>
        <p style="font-size:12px;color:var(--text-muted);margin-top:6px" id="progressLabel">Recording... speak clearly</p>
      </div>

      <div class="speaking-result" id="speakingResult">
        <div class="result-score" id="resultScore">0%</div>
        <div class="result-label">Pronunciation accuracy</div>
        <div class="heard-text" id="heardText"></div>
        <div class="result-detail" id="wordResults"></div>
        <div class="speaking-actions">
          <button class="btn-try-again" id="tryAgainBtn">Try Again</button>
          <button class="btn-next" id="nextStatementBtn">Next Statement</button>
        </div>
      </div>

      <div class="audio-controls" style="justify-content:center;margin-top:16px">
        <button class="audio-btn" id="speakListenBtn" title="Listen">▶</button>
        <span style="font-size:12px;color:var(--text-light)">Listen to reference</span>
      </div>
    `;

    const recordBtn = document.getElementById('recordBtn');
    const indicator = document.getElementById('recordingIndicator');
    const progressContainer = document.getElementById('progressContainer');
    const progressFill = document.getElementById('progressFill');
    const progressLabel = document.getElementById('progressLabel');
    const resultEl = document.getElementById('speakingResult');

    let recording = false;
    let progressInterval = null;
    let progressVal = 0;

    recordBtn.addEventListener('click', () => {
      if (!recording) {
        if (!window.speechEngine.isSupported) {
          this.showToast('Speech recognition is not supported in this browser. Try Chrome.');
          return;
        }
        recording = true;
        recordBtn.classList.add('recording');
        recordBtn.textContent = '⏹';
        indicator.classList.add('active');
        progressContainer.style.display = 'block';
        resultEl.classList.remove('visible');
        progressVal = 0;
        progressFill.style.width = '0%';

        window.speechEngine.onResult = (res) => {
          if (res.isFinal) {
            clearInterval(progressInterval);
            setTimeout(() => {
              this.handleSpeechResult(res.final, stmt);
              recording = false;
              recordBtn.classList.remove('recording');
              recordBtn.textContent = '🎤';
              indicator.classList.remove('active');
              progressContainer.style.display = 'none';
            }, 500);
          }
        };

        window.speechEngine.onError = (err) => {
          clearInterval(progressInterval);
          recording = false;
          recordBtn.classList.remove('recording');
          recordBtn.textContent = '🎤';
          indicator.classList.remove('active');
          progressContainer.style.display = 'none';
          this.showToast(`Error: ${err}. Please try again.`);
        };

        window.speechEngine.startRecording();

        progressInterval = setInterval(() => {
          progressVal = Math.min(progressVal + 3, 95);
          progressFill.style.width = progressVal + '%';
        }, 200);

      } else {
        clearInterval(progressInterval);
        window.speechEngine.stopRecording();
        recording = false;
        recordBtn.classList.remove('recording');
        recordBtn.textContent = '🎤';
        indicator.classList.remove('active');
        progressContainer.style.display = 'none';
      }
    });

    document.getElementById('tryAgainBtn').addEventListener('click', () => {
      resultEl.classList.remove('visible');
    });

    document.getElementById('nextStatementBtn').addEventListener('click', () => {
      this.goToNextStatement();
    });

    document.getElementById('speakListenBtn').addEventListener('click', () => {
      this.playStatementAudio(stmt, document.getElementById('speakListenBtn'));
    });

    document.getElementById('headerTitle').textContent = 'Speaking Practice';
    document.getElementById('headerSubtitle').textContent = 'Say the sentence aloud';
  },

  handleSpeechResult(spoken, stmt) {
    const accent = AppState.selectedAccent;
    const variant = stmt.accent_variants?.[accent];
    const target = variant?.alt_text_american || stmt.text;

    const result = window.speechEngine.compareTexts(spoken, target);
    const resultEl = document.getElementById('speakingResult');
    const scoreEl = document.getElementById('resultScore');
    const heardEl = document.getElementById('heardText');
    const wordsEl = document.getElementById('wordResults');

    scoreEl.textContent = `${result.score}%`;
    heardEl.textContent = `Heard: "${spoken}"`;

    wordsEl.innerHTML = result.results.map(r =>
      `<span class="word-result ${r.correct ? 'correct' : 'incorrect'}">${r.word}</span>`
    ).join('');

    resultEl.classList.add('visible');
  },

  goToNextStatement() {
    const kw = AppState.currentKeyword;
    if (!kw || !kw.statements) return;
    const stmts = kw.statements;
    const currentIdx = stmts.findIndex(s => s.statement_id === AppState.currentStatement?.statement_id);
    const nextIdx = currentIdx + 1;
    if (nextIdx < stmts.length) {
      AppState.currentStatement = stmts[nextIdx];
      this.renderSpeaking();
    } else {
      this.showToast('You\'ve completed all statements for this keyword! 🎉');
      this.navigate('statements', { keyword: kw });
    }
  },

  renderBreadcrumb() {
    const el = document.getElementById('breadcrumb');
    const env = AppState.currentEnvData;
    const envName = env?.environment_name || '';
    const kw = AppState.currentKeyword;

    let html = '<a data-nav="home">Home</a>';

    if (AppState.view === 'keywords' || AppState.view === 'statements' || AppState.view === 'speaking') {
      html += ` <span class="sep">›</span> <a data-nav="keywords">${envName || 'Keywords'}</a>`;
    }
    if (AppState.view === 'statements' || AppState.view === 'speaking') {
      html += ` <span class="sep">›</span> <span class="current">${kw?.word || 'Statements'}</span>`;
    }
    if (AppState.view === 'speaking') {
      html += ` <span class="sep">›</span> <span class="current">Practice</span>`;
    }

    el.innerHTML = html;

    el.querySelectorAll('a[data-nav]').forEach(a => {
      a.addEventListener('click', (e) => {
        e.preventDefault();
        const nav = a.dataset.nav;
        if (nav === 'home') {
          AppState.currentEnv = null;
          AppState.currentEnvData = null;
          AppState.currentKeyword = null;
          AppState.currentStatement = null;
          this.navigate('home');
        } else if (nav === 'keywords') {
          AppState.currentKeyword = null;
          AppState.currentStatement = null;
          this.navigate('keywords', { env: AppState.currentEnv, envData: AppState.currentEnvData });
        }
      });
    });
  },

  bindGlobalEvents() {
    document.getElementById('accentSelect').addEventListener('change', (e) => {
      AppState.selectedAccent = e.target.value;
      if (window.speechEngine) {
        window.speechEngine.updateAccent(AppState.selectedAccent);
      }
      if (AppState.view === 'statements') this.renderStatements();
      this.showToast(`Accent switched to ${e.target.options[e.target.selectedIndex].text}`);
    });
  }
};

document.addEventListener('DOMContentLoaded', () => App.init());
