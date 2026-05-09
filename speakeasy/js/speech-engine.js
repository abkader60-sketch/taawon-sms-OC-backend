class SpeechEngine {
  constructor() {
    this.recognition = null;
    this.isRecording = false;
    this.onResult = null;
    this.onError = null;
    this.isSupported = false;
    this.init();
  }

  init() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      this.isSupported = false;
      return;
    }
    this.isSupported = true;
    this.recognition = new SpeechRecognition();
    this.recognition.continuous = false;
    this.recognition.interimResults = true;
    this.recognition.maxAlternatives = 1;

    const accentMap = {
      american_ga: 'en-US',
      british_rp: 'en-GB',
      british_estuary: 'en-GB'
    };
    const lang = accentMap[window.AppState?.selectedAccent] || 'en-US';
    this.recognition.lang = lang;

    this.recognition.onresult = (event) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript.trim();
        if (event.results[i].isFinal) {
          final += transcript;
        } else {
          interim += transcript;
        }
      }
      if (this.onResult) this.onResult({ final, interim, isFinal: !!final });
    };

    this.recognition.onerror = (event) => {
      this.isRecording = false;
      if (this.onError) this.onError(event.error);
    };

    this.recognition.onend = () => {
      this.isRecording = false;
    };
  }

  updateAccent(accentId) {
    const accentMap = {
      american_ga: 'en-US',
      british_rp: 'en-GB',
      british_estuary: 'en-GB'
    };
    const lang = accentMap[accentId] || 'en-US';
    if (this.recognition) this.recognition.lang = lang;
  }

  startRecording() {
    if (!this.isSupported || !this.recognition) return false;
    if (this.isRecording) return true;
    try {
      this.recognition.start();
      this.isRecording = true;
      return true;
    } catch (e) {
      return false;
    }
  }

  stopRecording() {
    if (this.recognition && this.isRecording) {
      try { this.recognition.stop(); } catch (e) {}
      this.isRecording = false;
    }
  }

  compareTexts(spoken, target) {
    const norm = (s) => s.toLowerCase().replace(/[^\w\s']/g, '').replace(/\s+/g, ' ').trim();
    const spokenWords = norm(spoken).split(' ');
    const targetWords = norm(target).split(' ');

    let correct = 0;
    const results = targetWords.map((tw, i) => {
      const sw = spokenWords[i] || '';
      const isCorrect = sw === tw;
      if (isCorrect) correct++;
      return { word: tw, spoken: sw || '(missing)', correct: isCorrect };
    });

    const score = targetWords.length > 0 ? Math.round((correct / targetWords.length) * 100) : 0;
    return { score, results, spokenWords, targetWords };
  }
}

window.speechEngine = new SpeechEngine();
