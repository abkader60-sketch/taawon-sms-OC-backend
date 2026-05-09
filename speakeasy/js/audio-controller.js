class AudioController {
  constructor() {
    this.currentAudio = null;
    this.isPlaying = false;
    this.onEnded = null;
    this.synth = window.speechSynthesis;
    this.currentUtterance = null;
    this.useTTSFallback = true;
  }

  setUseTTSFallback(val) { this.useTTSFallback = val; }

  playAudio(url, onEnded) {
    this.stop();
    this.onEnded = onEnded || null;
    const audio = new Audio(url);
    audio.preload = 'auto';
    audio.addEventListener('ended', () => {
      this.isPlaying = false;
      this.currentAudio = null;
      if (this.onEnded) this.onEnded();
    });
    audio.addEventListener('error', () => {
      this.isPlaying = false;
      this.currentAudio = null;
      if (this.useTTSFallback) {
        console.warn('Audio file not found, using TTS fallback');
      } else if (this.onEnded) this.onEnded();
    });
    audio.play().then(() => {
      this.currentAudio = audio;
      this.isPlaying = true;
    }).catch((err) => {
      this.isPlaying = false;
      if (this.useTTSFallback) {
        this.speakText('', onEnded);
      } else if (this.onEnded) this.onEnded();
    });
  }

  speakText(text, onEnded) {
    this.stop();
    if (!this.synth) { if (onEnded) onEnded(); return; }
    this.synth.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.9;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;
    const accentMap = {
      american_ga: 'en-US',
      british_rp: 'en-GB',
      british_estuary: 'en-GB'
    };
    const lang = accentMap[window.AppState?.selectedAccent] || 'en-US';
    utterance.lang = lang;

    const voices = this.synth.getVoices();
    const matched = voices.find(v => v.lang.startsWith(lang.split('-')[0]));
    if (matched) utterance.voice = matched;

    utterance.onend = () => {
      this.isPlaying = false;
      this.currentUtterance = null;
      if (onEnded) onEnded();
    };
    utterance.onerror = () => {
      this.isPlaying = false;
      this.currentUtterance = null;
      if (onEnded) onEnded();
    };
    this.currentUtterance = utterance;
    this.isPlaying = true;
    this.synth.speak(utterance);
  }

  playStatement(statement, accentId, onEnded) {
    if (!statement || !accentId) return;
    const variant = statement.accent_variants?.[accentId];
    if (variant?.audio_url) {
      this.useTTSFallback = false;
      this.playAudio(variant.audio_url, onEnded);
    } else {
      this.useTTSFallback = true;
      const text = variant?.alt_text_american || statement.text;
      this.speakText(text, onEnded);
    }
  }

  stop() {
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio.currentTime = 0;
      this.currentAudio = null;
    }
    if (this.synth) {
      this.synth.cancel();
      this.currentUtterance = null;
    }
    this.isPlaying = false;
  }

  pause() {
    if (this.currentAudio && this.isPlaying) {
      this.currentAudio.pause();
      this.isPlaying = false;
    }
    if (this.synth && this.synth.speaking) {
      this.synth.pause();
      this.isPlaying = false;
    }
  }

  resume() {
    if (this.currentAudio && !this.isPlaying) {
      this.currentAudio.play().then(() => { this.isPlaying = true; }).catch(() => {});
    }
    if (this.synth && this.synth.paused) {
      this.synth.resume();
      this.isPlaying = true;
    }
  }
}

window.audioController = new AudioController();
