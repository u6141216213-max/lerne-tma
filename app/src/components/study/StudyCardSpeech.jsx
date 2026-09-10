import { tr } from '../../i18n/locale';
import { useInterfaceLocale } from '../../i18n/useInterfaceLocale';
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { RefreshCw, Eye, Volume2, Mic, Check, AlertCircle, Sparkles, Sliders } from 'lucide-react';
import { stripMarkdown, normalizeSpeechText } from '../../utils/text';
import { getTextShadow } from '../../utils/style';
import { useDeckStore } from '../../store/useDeckStore';
import { useSettingsStore } from '../../store/useSettingsStore';
import { useLanguageStore } from '../../store/useLanguageStore';
import { getSpeechLocaleForLang } from '../../constants/languageConstants';
import { getCardStyle } from '../../utils/cardStyles';
import { triggerHaptic } from '../../utils/platform';
import { stopGlobalAudio } from '../../hooks/useAudio';


export const StudyCardSpeech = React.memo(({
  card,
  onFlip,
  loading,
  playAudio,
  onPlayCardAudio,
  stopAudio,
  isAudioLoading,
  isAutoplayActive,
  styles = {}
}) => {
  useInterfaceLocale();
  const [isListening, setIsListening] = useState(false);
  const [recognizedText, setRecognizedText] = useState("");
  const [speechError, setSpeechError] = useState("");
  const [speechSuccess, setSpeechSuccess] = useState(false);

  const recognitionRef = useRef(null);
  const silenceTimerRef = useRef(null);
  const cardFrontRef = useRef(card?.front);

  const recognizedTextRef = useRef("");
  const speechSuccessRef = useRef(false);

  const speechMatchThreshold = useSettingsStore(s => s.speechMatchThreshold) ?? 75;
  const setSpeechMatchThreshold = useSettingsStore(s => s.setSpeechMatchThreshold);

  const {
    cardFont,
    cardTextColor,
    cardFontSize = 1,
    cardFontWeight,
    cardFontStyle,
    cardTextShadow
  } = styles;

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const cardStyle = useMemo(() => getCardStyle(styles), [
    styles?.cardFont,
    styles?.cardTextColor,
    styles?.cardFontSize,
    styles?.cardFontWeight,
    styles?.cardFontStyle,
    styles?.cardTextShadow,
    styles?.cardTextAlign
  ]);

  useEffect(() => {
    cardFrontRef.current = card?.front;
  }, [card?.front]);

  useEffect(() => {
    recognizedTextRef.current = recognizedText;
  }, [recognizedText]);

  useEffect(() => {
    speechSuccessRef.current = speechSuccess;
  }, [speechSuccess]);

  // Reset speech state on card change
  useEffect(() => {
    stopSpeechRecognition();
    setRecognizedText("");
    setSpeechError("");
    setSpeechSuccess(false);

    speechSuccessRef.current = false;
    recognizedTextRef.current = "";
  }, [card?.id]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
      if (recognitionRef.current) {
        try { recognitionRef.current.abort(); } catch { /* ignore */ }
      }
    };
  }, []);

  const stopSpeechRecognition = (e) => {
    e?.stopPropagation();
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch { /* ignore */ }
    }
  };

  const evaluateSpeech = (transcript, isFinalCheck = false, overrideThreshold = null) => {
    if (!transcript || !card || speechSuccessRef.current) return false;

    const currentDeck = useDeckStore.getState().currentDeck;
    const activeLang = useLanguageStore.getState().activeLanguage;
    const cardLang = card.target_language || currentDeck?.target_language || activeLang || 'de';
    const cleanTranscript = normalizeSpeechText(transcript, cardLang);
    const cleanOriginal = normalizeSpeechText(cardFrontRef.current || card.front, cardLang);

    if (!cleanTranscript || !cleanOriginal) return false;

    const originalWords = cleanOriginal.split(/\s+/).filter(Boolean);
    const transcriptWords = cleanTranscript.split(/\s+/).filter(Boolean);

    let matchCount = 0;
    originalWords.forEach(w => {
      if (transcriptWords.includes(w)) {
        matchCount++;
      }
    });

    const matchRatio = originalWords.length > 0 ? matchCount / originalWords.length : 0;
    const currentThreshold = overrideThreshold !== null ? overrideThreshold : (speechMatchThreshold || 75);

    const ratioMatched = (matchRatio * 100) >= currentThreshold;
    const exactMatched = cleanTranscript === cleanOriginal;
    const extraSpokenMatched = cleanTranscript.includes(cleanOriginal) && (originalWords.length / transcriptWords.length >= 0.6);
    const fragmentMatched = cleanOriginal.includes(cleanTranscript) && ((matchRatio * 100) >= currentThreshold);

    const isMatched = ratioMatched || exactMatched || extraSpokenMatched || fragmentMatched;

    if (isMatched) {
      speechSuccessRef.current = true;
      setSpeechSuccess(true);
      setIsListening(false);
      
      stopSpeechRecognition();

      triggerHaptic('success');
      setTimeout(() => {
        onFlip(true);
      }, 800);
      return true;
    } else if (isFinalCheck) {
      setSpeechSuccess(false);
      triggerHaptic('error');
      return false;
    }
    return false;
  };

  const startSpeechRecognition = (e) => {
    e?.stopPropagation();
    
    if (speechSuccessRef.current) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setSpeechError(tr("Ваш девайс не поддерживает распознавание голоса."));
      return;
    }

    // Stop any playing audio immediately so mic does not catch speaker audio
    try {
      stopAudio?.();
      stopGlobalAudio();
    } catch { /* ignore */ }

    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch { /* ignore */ }
    }

    if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);

    setSpeechError("");
    setRecognizedText("");
    setSpeechSuccess(false);
    speechSuccessRef.current = false;
    recognizedTextRef.current = "";

    try {
      const rec = new SpeechRecognition();
      const currentDeck = useDeckStore.getState().currentDeck;
      const activeLang = useLanguageStore.getState().activeLanguage;
      const cardLang = card.target_language || currentDeck?.target_language || activeLang || 'de';
      
      rec.lang = getSpeechLocaleForLang(cardLang);
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 1;

      rec.onstart = () => {
        setIsListening(true);
        triggerHaptic('medium');
      };

      rec.onerror = (err) => {
        console.error("Speech Error:", err);
        if (err.error === 'not-allowed') {
          setSpeechError(tr("Нет доступа к микрофону."));
        } else if (err.error !== 'no-speech' && err.error !== 'aborted') {
          setSpeechError(tr("Ошибка распознавания. Попробуйте еще раз."));
        }
        setIsListening(false);
      };

      rec.onend = () => {
        setIsListening(false);
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }
        if (!speechSuccessRef.current && recognizedTextRef.current) {
          evaluateSpeech(recognizedTextRef.current, true);
        }
      };

      rec.onresult = (event) => {
        let textChunks = [];

        for (let i = 0; i < event.results.length; i++) {
          const chunk = event.results[i][0].transcript.trim();
          if (!chunk) continue;

          if (textChunks.length === 0) {
            textChunks.push(chunk);
          } else {
            const prev = textChunks[textChunks.length - 1];
            // If Brave/Android cumulative chunk starts with the previous chunk, replace it!
            if (chunk.toLowerCase().startsWith(prev.toLowerCase())) {
              textChunks[textChunks.length - 1] = chunk;
            } 
            // If previous chunk ends with the new chunk or equals it, skip
            else if (prev.toLowerCase().endsWith(chunk.toLowerCase())) {
              continue;
            } 
            // Otherwise, it's a new consecutive phrase chunk, append it!
            else {
              textChunks.push(chunk);
            }
          }
        }

        const transcript = textChunks.join(' ').replace(/\s+/g, ' ').trim();
        setRecognizedText(transcript);
        recognizedTextRef.current = transcript;

        // Auto-evaluate 100% success while speaking on the fly
        const matched = evaluateSpeech(transcript, false, 100);
        if (matched) return;

        // Silence timer (1.5s): if user stops speaking and 100% wasn't reached, evaluate user's selected threshold (e.g. 75%)
        if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = setTimeout(() => {
          stopSpeechRecognition();
          evaluateSpeech(recognizedTextRef.current, true);
        }, 1500);
      };

      recognitionRef.current = rec;
      rec.start();
    } catch {
      setSpeechError(tr("Ошибка при запуске микрофона."));
      setIsListening(false);
    }
  };

  const handleMicClick = (e) => {
    e?.stopPropagation();
    e?.preventDefault();
    if (speechSuccessRef.current) return;

    // Immediately stop and interrupt any previously played audio
    try {
      stopAudio?.();
      stopGlobalAudio();
    } catch { /* ignore */ }

    if (isListening) {
      stopSpeechRecognition(e);
      if (recognizedTextRef.current) {
        evaluateSpeech(recognizedTextRef.current, true);
      }
    } else {
      startSpeechRecognition(e);
    }
  };

  if (!card) return null;

  return (
    <div className="interactive-mode-container" onClick={e => e.stopPropagation()}>
      <div 
        className="text-front speak-target-text" 
        style={{ 
          ...cardStyle, 
          marginBottom: '28px' 
        }}
      >
        {stripMarkdown(card.front)}
      </div>

      {/* Accuracy Threshold Selector */}
      <div className="speak-threshold-selector" onClick={e => e.stopPropagation()}>
        <span className="threshold-label"><Sliders size={14} />{' '}{tr("Точность:")}</span>
        {[50, 75, 85, 100].map(val => (
          <button
            key={val}
            type="button"
            className={`btn-threshold-pill ${speechMatchThreshold === val ? 'active' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              setSpeechMatchThreshold(val);
              triggerHaptic('selection');
            }}
          >
            {val}%
          </button>
        ))}
      </div>

      {/* Microphone Controls */}
      <div className="speak-mic-area">
        <div className="speak-mic-controls-row">
          <button 
            type="button"
            className={`btn-speak-mic ${isListening ? 'listening' : ''} ${speechSuccess ? 'success' : ''}`}
            onClick={handleMicClick}
          >
            {isListening ? (
              <div className="recording-wave-rings">
                <span className="ring"></span>
                <span className="ring"></span>
                <span className="ring"></span>
              </div>
            ) : null}
            {speechSuccess ? <Check size={32} /> : <Mic size={32} />}
          </button>

          {(card.audio_url || onPlayCardAudio) && (
            <button
              type="button"
              className="btn-speak-audio"
              disabled={loading || isAutoplayActive}
              onClick={(e) => {
                e.stopPropagation();
                if (isAutoplayActive) return;
                if (card.audio_url) playAudio?.(card.audio_url);
                else onPlayCardAudio?.();
              }}
              title={tr("Озвучить карточку")}
            >
              {isAudioLoading ? (
                card.audio_is_generating ? (
                  <Sparkles size={22} className="sparkles-spin" style={{ color: '#a855f7' }} />
                ) : (
                  <RefreshCw size={22} className="spin" />
                )
              ) : (
                <Volume2 size={24} />
              )}
            </button>
          )}
        </div>
        <p className="mic-help-label">
          {isListening ? tr("Слушаю... Произнесите фразу или нажмите для проверки") : tr("Нажмите на микрофон для записи")}
        </p>
      </div>

      {/* Recognized Transcript Bubble */}
      {recognizedText && (
        <div 
          className="recognized-transcript-bubble glass"
          style={{
            borderColor: speechSuccess ? 'rgba(16, 185, 129, 0.4)' : (isListening ? 'rgba(255, 255, 255, 0.15)' : 'rgba(244, 63, 94, 0.4)'),
            flexDirection: 'column',
            padding: '12px 18px',
            width: '100%'
          }}
        >
          <span style={{ fontSize: '0.85rem', opacity: 0.75, color: '#cbd5e1', marginBottom: '4px' }}>{tr("Вы сказали:")}</span>
          <div style={{
            fontFamily: cardFont,
            color: cardTextColor,
            fontSize: `${Math.max(cardFontSize * 1.1, 1.4)}rem`,
            fontWeight: cardFontWeight,
            fontStyle: cardFontStyle,
            textShadow: getTextShadow(cardTextShadow, cardTextColor),
            textAlign: 'center',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            wordBreak: 'break-word'
          }}>
            <span>{recognizedText}</span>
            {speechSuccess ? (
              <Check size={24} color="#10b981" style={{ flexShrink: 0 }} />
            ) : (!isListening ? (
              <AlertCircle size={24} color="#f43f5e" style={{ flexShrink: 0 }} />
            ) : null)}
          </div>
        </div>
      )}

      {speechError && (
        <div className="speech-error-badge">
          <AlertCircle size={16} />
          <span>{speechError}</span>
        </div>
      )}

      {/* Reveal Answer Button */}
      <button 
        className="btn-interactive-reveal"
        onClick={(e) => {
          e.stopPropagation();
          onFlip(true);
        }}
      >
        <Eye size={18} />
        <span>{tr("Показать ответ")}</span>
      </button>
    </div>
  );
});
