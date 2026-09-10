import { tr } from '../../i18n/locale';
import { useInterfaceLocale } from '../../i18n/useInterfaceLocale';
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { RefreshCw, Eye, Volume2, Sparkles, AlertTriangle, RotateCw, BookOpen, HelpCircle } from 'lucide-react';
import { stripMarkdown } from '../../utils/text';
import { CardBackground } from '../common/CardBackground';
import { useDeckStore } from '../../store/useDeckStore';
import { useLanguageStore } from '../../store/useLanguageStore';

// Extracted Sub-components and Utilities
import { playSuccessSound, playErrorSound } from '../../utils/audioSynth';
import { parseClozeData, cleanBracketSyntax, autoGenerateChoices } from '../../utils/clozeParser';
import { parseQuizData } from '../../utils/quizParser';
import { StudyCardTrainer } from './StudyCardTrainer';
import { StudyCardQuiz } from './StudyCardQuiz';
import { StudyCardPuzzle } from './StudyCardPuzzle';
import { StudyCardSpeech } from './StudyCardSpeech';
import { CardAudioPlayer } from './CardAudioPlayer';
import { KaraokeText } from './KaraokeText';
import { useVoicePicker } from '../../hooks/useVoicePicker';
import { useKaraokeSync } from '../../hooks/useKaraokeSync';

// Re-export for backward compatibility
// eslint-disable-next-line react-refresh/only-export-components
export { playSuccessSound, playErrorSound, cleanBracketSyntax, autoGenerateChoices };
import { getCardStyle, getBackCardStyle, getContextStyle, getHarmonizedOptionStyles } from '../../utils/cardStyles';
import { triggerHaptic } from '../../utils/platform';

import { getFlagStyle, FLAG_COLORS } from '../../constants/cardFlags';
import { CardLevelBadge } from '../common/CardLevelBadge';

export const StudyCard = React.memo(({
  card,
  isFlipped,
  onFlip,
  loading,
  historyIndex,
  playAudio,
  audioControls,
  sessionVoice = null, // from useSessionVoice in StudyView
  isAudioLoading,
  isAutoplayActive,
  onPlayBackAudio,
  styles,
  resolvedBgFront,
  resolvedBgBack,
  studyMode = 'classic',
  onTrainerAnswer,
  onNextCard
}) => {
  useInterfaceLocale();
  const flagStyle = useMemo(() => getFlagStyle(card?.flag), [card?.flag]);

  // Card language: prefer card-level, then deck-level, then global active language
  const cardLang = card?.target_language
    || useDeckStore.getState().currentDeck?.target_language
    || useLanguageStore.getState().activeLanguage
    || 'de';

  const deckId = useDeckStore.getState().currentDeck?.id;

  // Propagate voice choice back to session store so it survives card navigation
  const handleVoiceChange = useCallback((voiceValue) => {
    sessionVoice?.setSessionVoice(deckId, voiceValue);
  }, [sessionVoice, deckId]);

  // Voice picker — scoped to this card's language, initialized from session store
  const storedVoice = sessionVoice?.getSessionVoice(deckId) || null;
  const frontVoicePicker = useVoicePicker(cardLang, storedVoice, handleVoiceChange, true);

  // Provide current card text to the picker so auto-generate works on voice switch
  const frontText = card ? stripMarkdown(studyMode === 'reverse' ? card.back : card.front) : '';

  // Karaoke: sync word boundaries with audio playback position (with fallback estimation)
  const { activeWordIndex, effectiveBoundaries } = useKaraokeSync(
    frontVoicePicker.wordBoundaries,
    frontText,
    audioControls?.duration ?? 0,
    audioControls?.currentTime ?? 0,
    audioControls?.audioState ?? 'idle',
  );

  useEffect(() => {
    frontVoicePicker.setCardText(frontText);
  }, [frontText]); // eslint-disable-line react-hooks/exhaustive-deps

  // On card change: keep voice selection (session) but clear stale preview URL
  useEffect(() => {
    frontVoicePicker.setPreviewUrl(null);
  }, [card?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Interactive Cloze states
  const [wrongSelected, setWrongSelected] = useState([]);
  const [correctSelected, setCorrectSelected] = useState(null);

  // Reset interactive states when card changes
  useEffect(() => {
    setWrongSelected([]);
    setCorrectSelected(null);
  }, [card?.id, card?.front, card?.back, card?.updated_at, studyMode]);

  // Reactive image height — subscribes to store so editor changes apply instantly
  const storedImageHeight = useDeckStore(state => {
    try {
      const meta = state.currentDeck?.metadata;
      const parsed = meta ? (typeof meta === 'string' ? JSON.parse(meta) : meta) : {};
      return parsed.imageHeight || 220;
    } catch { return 220; }
  });

  const cardMetaImageHeight = useMemo(() => {
    if (card?.image_height) return card.image_height;
    if (card?.deck_metadata) {
      try {
        const dm = typeof card.deck_metadata === 'string' ? JSON.parse(card.deck_metadata) : card.deck_metadata;
        if (dm?.imageHeight) return dm.imageHeight;
      } catch { /* ignore */ }
    }
    return null;
  }, [card?.image_height, card?.deck_metadata]);

  const [cardImageHeight, setCardImageHeight] = useState(cardMetaImageHeight || storedImageHeight);

  // Keep in sync when card or metadata changes externally (e.g. editor slider)
  useEffect(() => {
    setCardImageHeight(cardMetaImageHeight || storedImageHeight);
  }, [cardMetaImageHeight, card?.id, storedImageHeight]);

  const startCardImageResize = useCallback((e) => {
    e.stopPropagation();
    e.preventDefault();
    const startY = e.touches ? e.touches[0].clientY : e.clientY;
    const startH = cardImageHeight;
    const onMove = (ev) => {
      const clientY = ev.touches ? ev.touches[0].clientY : ev.clientY;
      const newH = Math.max(60, Math.min(800, startH + (clientY - startY)));
      setCardImageHeight(newH);
    };
    const onUp = (ev) => {
      const clientY = ev.changedTouches ? ev.changedTouches[0].clientY : ev.clientY;
      const finalH = Math.max(60, Math.min(800, startH + (clientY - startY)));
      const deck = useDeckStore.getState().currentDeck;
      if (deck) {
        const meta = deck.metadata
          ? (typeof deck.metadata === 'string' ? JSON.parse(deck.metadata) : deck.metadata)
          : {};
        useDeckStore.getState().updateDeckMetadata(deck.id, { ...meta, imageHeight: Math.round(finalH) }).catch(() => {});
      }
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('touchend', onUp);
  }, [cardImageHeight]);

  const cardStyle = useMemo(() => getCardStyle(styles), [styles?.cardFont, styles?.cardTextColor, styles?.cardFontSize, styles?.cardFontWeight, styles?.cardFontStyle, styles?.cardTextShadow, styles?.cardTextAlign]); // eslint-disable-line react-hooks/exhaustive-deps
  const backCardStyle = useMemo(() => getBackCardStyle(styles), [styles?.cardFont, styles?.cardTextColor, styles?.backTextColor, styles?.cardFontSize, styles?.cardFontWeight, styles?.cardFontStyle, styles?.cardTextShadow, styles?.contextTextAlign, styles?.cardTextAlign]); // eslint-disable-line react-hooks/exhaustive-deps
  const contextStyle = useMemo(() => getContextStyle(styles), [styles?.cardFont, styles?.cardTextColor, styles?.backTextColor, styles?.cardFontSize, styles?.cardFontWeight, styles?.cardFontStyle, styles?.cardTextShadow, styles?.contextFont, styles?.contextTextColor, styles?.contextFontSize, styles?.contextFontWeight, styles?.contextFontStyle, styles?.contextTextShadow, styles?.contextTextAlign]); // eslint-disable-line react-hooks/exhaustive-deps
  const harmonizedOptions = useMemo(() => getHarmonizedOptionStyles(styles?.cardTextColor), [styles?.cardTextColor]);

  // Quiz / Exam Data Parsing
  const quizData = useMemo(() => {
    return parseQuizData(card);
  }, [card?.id, card?.front, card?.back, card?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  // Cloze / Trainer Data Parsing
  const clozeData = useMemo(() => {
    const allDeckCards = useDeckStore.getState().deckCards || [];
    return parseClozeData(card, studyMode, allDeckCards);
  }, [card?.id, card?.front, card?.back, card?.updated_at, studyMode]); // eslint-disable-line react-hooks/exhaustive-deps

  const getResolvedAudioUrl = (c, isBack = false) => {
    if (!c) return '';
    const urlVal = isBack ? c.audio_back_url : c.audio_url;
    const pathVal = isBack ? c.audio_back_path : c.audio_path;
    const raw = urlVal || pathVal;
    if (!raw) return '';
    if (raw.startsWith('http') || raw.startsWith('/api/') || raw.startsWith('data:') || raw.startsWith('blob:')) return raw;
    if (raw.startsWith('audio/')) return `/api/media/${raw}`;
    return `/api/media/audio/${raw}`;
  };

  const renderFrontAudioPlayer = () => {
    const isBackSide = studyMode === 'reverse';
    const audioUrl = getResolvedAudioUrl(card, isBackSide) || getResolvedAudioUrl(card, !isBackSide);
    if (!audioUrl && !frontText) return null;

    return (
      <CardAudioPlayer
        audioUrl={audioUrl}
        playAudio={audioControls?.playAudio || playAudio}
        pauseAudio={audioControls?.pauseAudio}
        resumeAudio={audioControls?.resumeAudio}
        togglePlayPause={audioControls?.togglePlayPause}
        stopAudio={audioControls?.stopAudio}
        seekAudio={audioControls?.seekAudio}
        setPlaybackSpeed={audioControls?.setPlaybackSpeed}
        audioState={audioControls?.audioState}
        currentUrl={audioControls?.currentUrl}
        currentTime={audioControls?.currentTime}
        duration={audioControls?.duration}
        playbackRate={audioControls?.playbackRate}
        isAudioLoading={isAudioLoading || audioControls?.isAudioLoading}
        isGenerating={card.audio_is_generating}
        voicePicker={frontVoicePicker}
        cardText={frontText}
        cardId={card?.id}
        isBack={isBackSide}
        disabled={loading || isAutoplayActive}
      />
    );
  };

  // --- Early return after all hooks ---
  if (!card) return null;

  let deckMeta = card.deck_metadata;
  if (!deckMeta) {
    const currentDeck = useDeckStore.getState().currentDeck;
    if (currentDeck?.metadata) {
      deckMeta = typeof currentDeck.metadata === 'string' ? JSON.parse(currentDeck.metadata) : currentDeck.metadata;
    }
  } else if (typeof deckMeta === 'string') {
    try { deckMeta = JSON.parse(deckMeta); } catch { /* pass */ }
  }

  const deckResources = deckMeta?.resources || [];
  const deckImage = deckResources.find(r => r.type === 'image' && r.show_in_cards !== false);
  const deckVideo = deckResources.find(r => r.type === 'video');

  const getDeckImageUrl = (resItem) => {
    if (!resItem) return null;
    if (resItem.url) return resItem.url;
    if (resItem.path) {
      const cleanPath = resItem.path.replace(/^(images|audio|videos)\//, '');
      return `/api/media/images/${cleanPath}`;
    }
    return null;
  };

  const imageUrl = card.image_url || getDeckImageUrl(deckImage);

  const hasQuizSyntax = quizData && quizData.isQuiz;
  const hasBracketSyntax = /\{([^}]+)\}/.test(card?.front || '');
  const hasTrainerGaps = clozeData && clozeData.gaps && clozeData.gaps.length > 0;
  const effectiveStudyMode = hasQuizSyntax
    ? 'quiz'
    : ((hasBracketSyntax || hasTrainerGaps)
        ? 'trainer'
        : (studyMode === 'trainer' ? 'classic' : studyMode));

  const handleClozeClick = (option, e) => {
    e.stopPropagation();
    if (correctSelected || isFlipped) return;

    if (option.toLowerCase() === clozeData.correctAnswer.toLowerCase()) {
      setCorrectSelected(option);
      triggerHaptic('success');
      
      if (studyMode === 'trainer' && onTrainerAnswer) {
        const isFirstTry = wrongSelected.length === 0;
        onTrainerAnswer(card.id, isFirstTry);
      }

      setTimeout(() => {
        onFlip(true);
      }, 700);
    } else {
      if (!wrongSelected.includes(option)) {
        if (studyMode === 'trainer' && wrongSelected.length === 0 && onTrainerAnswer) {
          onTrainerAnswer(card.id, false);
        }
        setWrongSelected([...wrongSelected, option]);
        triggerHaptic('error');
      }
    }
  };

  const renderRevealButton = () => (
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
  );

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        id="tut-study-card"
        key={`${card.id}-${card.front}-${historyIndex}`}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
        className={`card-container ${loading ? 'loading-card' : ''}`}
        style={{ borderRadius: '20px', transition: 'all 0.3s ease', ...flagStyle }}
      >
        {!isFlipped ? (
          <div className="card-inner card-front glass" onClick={() => onFlip(true)} style={{ cursor: 'pointer', ...flagStyle }}>
            <CardBackground styleType={resolvedBgFront} />
            <div className="card-face">
              
              {/* Type Badge (Top-Right Corner) */}
              {effectiveStudyMode === 'trainer' && (
                <div style={{
                  position: 'absolute',
                  top: '12px',
                  right: '12px',
                  fontSize: '0.72rem',
                  fontWeight: 700,
                  color: '#c084fc',
                  background: 'rgba(168, 85, 247, 0.18)',
                  border: '1px solid rgba(168, 85, 247, 0.35)',
                  borderRadius: '8px',
                  padding: '3px 8px',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  backdropFilter: 'blur(8px)',
                  boxShadow: '0 2px 8px rgba(0, 0, 0, 0.25)',
                  zIndex: 15
                }}>{tr("🏋️ Тренажер")}{' '}</div>
              )}

              {/* Leech Indicator */}
              {Boolean(card?.is_leech || (card?.lapses && card.lapses >= 5)) && (
                <div
                  className="leech-badge"
                  title={tr("Сложная карточка ({{p0}} ошибок). Рекомендуем упростить или добавить мнемонику.", { p0: card?.lapses || 5 })}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontSize: '0.72rem',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '20px',
                    background: 'rgba(239, 68, 68, 0.2)',
                    color: '#fca5a5',
                    border: '1px solid rgba(239, 68, 68, 0.4)',
                    boxShadow: '0 2px 6px rgba(0, 0, 0, 0.2)',
                    marginBottom: '12px',
                    alignSelf: 'center'
                  }}
                >
                  <AlertTriangle size={12} color="#ef4444" />
                  <span>{tr("Сложная карточка (")}{card?.lapses || 5}{' '}{tr("ошибок)")}</span>
                </div>
              )}
              
              {/* Media Preview Header — shown on all modes */}
              {imageUrl && (
                <>
                  <div style={{
                    width: '100%',
                    height: `${cardImageHeight}px`,
                    overflow: 'hidden',
                    borderRadius: '12px',
                    marginBottom: '4px',
                    flexShrink: 0
                  }}>
                    <img
                      src={imageUrl}
                      alt=""
                      style={{
                        display: 'block',
                        width: '100%',
                        height: '100%',
                        objectFit: 'contain',
                        borderRadius: '12px'
                      }}
                    />
                  </div>
                  {/* Resize handle */}
                  <div
                    onMouseDown={startCardImageResize}
                    onTouchStart={startCardImageResize}
                    title={tr("Потяни чтобы изменить высоту")}
                    style={{
                      height: '14px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'ns-resize',
                      marginBottom: '10px',
                      userSelect: 'none',
                      touchAction: 'none',
                      flexShrink: 0
                    }}
                  >
                    <div style={{
                      width: '40px',
                      height: '4px',
                      borderRadius: '2px',
                      background: 'rgba(168, 85, 247, 0.45)',
                      transition: 'background 0.2s'
                    }}
                    onMouseOver={e => e.currentTarget.style.background = 'rgba(168, 85, 247, 0.9)'}
                    onMouseOut={e => e.currentTarget.style.background = 'rgba(168, 85, 247, 0.45)'}
                    />
                  </div>
                </>
              )}

              {/* Classic / Reverse Mode Text */}
              {(effectiveStudyMode === 'classic' || effectiveStudyMode === 'reverse') && (
                <>
                  <div id="tut-study-front" className="text-front" style={cardStyle}>
                    <KaraokeText
                      text={cleanBracketSyntax(frontText)}
                      wordBoundaries={effectiveBoundaries}
                      activeWordIndex={activeWordIndex}
                      style={cardStyle}
                    />
                  </div>
                  <div className="flip-hint-badge">
                    <Eye size={16} />
                    <span>{tr("Посмотреть ответ")}</span>
                  </div>
                  {renderFrontAudioPlayer()}
                </>
              )}


              {/* Quiz / Exam Mode Component */}
              {effectiveStudyMode === 'quiz' && quizData && (
                <StudyCardQuiz
                  card={card}
                  quizData={quizData}
                  isFlipped={isFlipped}
                  setIsFlipped={onFlip}
                  onFlip={onFlip}
                  playAudio={playAudio}
                  onTrainerAnswer={onTrainerAnswer}
                  renderAudioPlayer={renderFrontAudioPlayer}
                  styles={styles}
                />
              )}

              {/* Trainer Mode Component */}
              {effectiveStudyMode === 'trainer' && clozeData && (
                <StudyCardTrainer
                  card={card}
                  clozeData={clozeData}
                  isFlipped={isFlipped}
                  onFlip={onFlip}
                  playAudio={playAudio}
                  onTrainerAnswer={onTrainerAnswer}
                  onNextCard={onNextCard}
                  renderAudioPlayer={renderFrontAudioPlayer}
                  styles={styles}
                />
              )}

              {/* Cloze (Fill-in-the-blanks) Mode */}
              {effectiveStudyMode === 'cloze' && clozeData && (
                <div className="interactive-mode-container" onClick={e => e.stopPropagation()}>
                  <div className="text-front cloze-masked-text" style={{ ...cardStyle, margin: '14px 0', lineHeight: 1.5 }}>
                    {(() => {
                      const parts = clozeData.maskedText.split('_____');
                      const activeWord = correctSelected || wrongSelected[wrongSelected.length - 1];

                      let borderColor = 'rgba(255,255,255,0.4)';
                      let bgColor = 'rgba(255,255,255,0.05)';
                      let textColor = 'rgba(255,255,255,0.5)';

                      if (correctSelected) {
                        borderColor = '#22c55e';
                        bgColor = 'rgba(34, 197, 94, 0.2)';
                        textColor = '#4ade80';
                      } else if (wrongSelected.length > 0) {
                        borderColor = '#ef4444';
                        bgColor = 'rgba(239, 68, 68, 0.2)';
                        textColor = '#f87171';
                      }

                      return (
                        <>
                          {parts[0]}
                          <span
                            style={{
                              display: 'inline-block',
                              minWidth: '64px',
                              padding: '2px 10px',
                              margin: '0 4px',
                              borderRadius: '8px',
                              border: `2px ${correctSelected ? 'solid' : 'dashed'} ${borderColor}`,
                              background: bgColor,
                              color: textColor,
                              fontWeight: 700,
                              textAlign: 'center',
                              transition: 'all 0.2s ease-in-out'
                            }}
                          >
                            {activeWord || '_____'}
                          </span>
                          {parts[1]}
                        </>
                      );
                    })()}
                  </div>

                  {renderFrontAudioPlayer()}

                  <div className="cloze-choices-grid" style={{ marginTop: '16px' }}>
                    {clozeData.choices.map((opt, i) => {
                      const isWrong = wrongSelected.includes(opt);
                      const isCorrect = correctSelected === opt;

                      let btnClass = 'btn-cloze-option';
                      let customStyle = {
                        fontFamily: contextStyle.fontFamily,
                        fontSize: contextStyle.fontSize || `${styles?.cardFontSize}rem`,
                        fontWeight: contextStyle.fontWeight,
                        fontStyle: contextStyle.fontStyle,
                        textShadow: contextStyle.textShadow,
                        color: contextStyle.color
                      };

                      if (isCorrect) { btnClass += ' correct'; delete customStyle.color; }
                      if (isWrong) { btnClass += ' wrong shake-animation'; delete customStyle.color; }

                      return (
                        <button
                          key={i}
                          className={btnClass}
                          style={customStyle}
                          onClick={(e) => handleClozeClick(opt, e)}
                          disabled={!!correctSelected}
                        >
                          {opt}
                        </button>
                      );
                    })}
                  </div>
                  {renderRevealButton()}
                </div>
              )}

              {/* Puzzle (Sentence Builder) Mode */}
              {studyMode === 'puzzle' && (
                <StudyCardPuzzle
                  card={card}
                  isFlipped={isFlipped}
                  onFlip={onFlip}
                  loading={loading}
                  playAudio={playAudio}
                  styles={styles}
                />
              )}

              {/* Speech Recognition Mode */}
              {studyMode === 'speak' && (
                <StudyCardSpeech
                  card={card}
                  onFlip={onFlip}
                  loading={loading}
                  playAudio={playAudio}
                  onPlayCardAudio={async () => {
                    const url = await frontVoicePicker.generateAndSaveToCard(card?.id, frontText, false);
                    if (url) playAudio?.(url);
                  }}
                  stopAudio={audioControls?.stopAudio}
                  isAudioLoading={isAudioLoading}
                  isAutoplayActive={isAutoplayActive}
                  styles={styles}
                />
              )}

              {/* Card Level Badge (Bottom-Left Corner) */}
              <CardLevelBadge card={card} textColor={cardStyle?.color} style={{ position: 'absolute', bottom: '12px', left: '12px', zIndex: 15 }} />

            </div>
          </div>
        ) : (
          <div className="card-inner card-back glass" onClick={() => onFlip(false)} style={{ cursor: 'pointer', ...flagStyle }}>
            <CardBackground styleType={resolvedBgBack} />
            <div className="card-face">
              {/* Type Badge (Top-Right Corner) */}
              {effectiveStudyMode === 'trainer' && (
                <div style={{
                  position: 'absolute',
                  top: '12px',
                  right: '12px',
                  fontSize: '0.72rem',
                  fontWeight: 700,
                  color: '#c084fc',
                  background: 'rgba(168, 85, 247, 0.18)',
                  border: '1px solid rgba(168, 85, 247, 0.35)',
                  borderRadius: '8px',
                  padding: '3px 8px',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  backdropFilter: 'blur(8px)',
                  boxShadow: '0 2px 8px rgba(0, 0, 0, 0.25)',
                  zIndex: 15
                }}>{tr("🏋️ Тренажер")}{' '}</div>
              )}

              {/* Leech Indicator */}
              {Boolean(card?.is_leech || (card?.lapses && card.lapses >= 5)) && (
                <div
                  className="leech-badge"
                  title={tr("Сложная карточка ({{p0}} ошибок). Рекомендуем упростить или добавить мнемонику.", { p0: card?.lapses || 5 })}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontSize: '0.72rem',
                    fontWeight: 600,
                    padding: '4px 10px',
                    borderRadius: '20px',
                    background: 'rgba(239, 68, 68, 0.2)',
                    color: '#fca5a5',
                    border: '1px solid rgba(239, 68, 68, 0.4)',
                    boxShadow: '0 2px 6px rgba(0, 0, 0, 0.2)',
                    marginBottom: '12px',
                    alignSelf: 'center'
                  }}
                >
                  <AlertTriangle size={12} color="#ef4444" />
                  <span>{tr("Сложная карточка (")}{card?.lapses || 5}{' '}{tr("ошибок)")}</span>
                </div>
              )}
              {/* 1. FRONT REFERENCE SECTION */}
              <div className="card-back-front-section">
                <div className="card-back-section-badge front-badge">
                  <HelpCircle size={12} />
                  <span>{effectiveStudyMode === 'quiz' ? tr("Вопрос") : (studyMode === 'reverse' ? tr("Перевод") : tr("Лицевая сторона"))}</span>
                </div>

                {effectiveStudyMode === 'quiz' && quizData ? (
                  <div>
                    <div className="back-quiz-question" style={{ ...cardStyle, textAlign: 'left', fontSize: '1.2rem', marginBottom: '8px' }}>
                      {quizData.question}
                    </div>

                    <div className="back-quiz-subdivider">
                      <span>{tr("Варианты ответа")}</span>
                    </div>

                    <div className="back-quiz-options-compact">
                      {quizData.options.map((opt, i) => (
                        <div 
                          key={i} 
                          className={`back-quiz-opt-row ${opt.isCorrect ? 'is-correct' : ''}`}
                          style={opt.isCorrect ? undefined : { color: harmonizedOptions.textColor }}
                        >
                          <span className="back-quiz-opt-tag">{['A', 'B', 'C', 'D', 'E', 'F'][i] || (i + 1)}</span>
                          <span className="back-quiz-opt-label">{opt.text}</span>
                          {opt.isCorrect && <span className="back-quiz-opt-badge">{tr("✓ Верный")}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="front-mini-container" style={{ position: 'relative', width: '100%' }}>
                    <div className="text-front-mini" style={{ marginBottom: 0, opacity: 0.95, whiteSpace: 'pre-wrap', ...cardStyle }}>
                      {cleanBracketSyntax(stripMarkdown(studyMode === 'reverse' ? card.back : card.front))}
                    </div>
                  </div>
                )}

                {(() => {
                  const resolvedFrontUrl = studyMode === 'reverse'
                    ? (getResolvedAudioUrl(card, true) || getResolvedAudioUrl(card, false))
                    : getResolvedAudioUrl(card, false);
                  if (!resolvedFrontUrl) return null;

                  return (
                    <div style={{ marginTop: '8px' }}>
                      <CardAudioPlayer
                        audioUrl={resolvedFrontUrl}
                        playAudio={audioControls?.playAudio || playAudio}
                        pauseAudio={audioControls?.pauseAudio}
                        resumeAudio={audioControls?.resumeAudio}
                        togglePlayPause={audioControls?.togglePlayPause}
                        stopAudio={audioControls?.stopAudio}
                        seekAudio={audioControls?.seekAudio}
                        setPlaybackSpeed={audioControls?.setPlaybackSpeed}
                        audioState={audioControls?.audioState}
                        currentUrl={audioControls?.currentUrl}
                        currentTime={audioControls?.currentTime}
                        duration={audioControls?.duration}
                        playbackRate={audioControls?.playbackRate}
                        isAudioLoading={isAudioLoading || audioControls?.isAudioLoading}
                        isGenerating={card.audio_is_generating}
                        voicePicker={frontVoicePicker}
                        cardText={frontText}
                        cardId={card?.id}
                        isBack={studyMode === 'reverse'}
                        disabled={loading || isAutoplayActive}
                      />
                    </div>
                  );
                })()}
              </div>

              {/* 2. EXPLICIT SEPARATOR BETWEEN FRONT & BACK */}
              <div className="card-side-separator">
                <div className="separator-line" />
                <div className="separator-badge">
                  <RotateCw size={12} />
                  <span>{studyMode === 'reverse' ? tr("Оригинал") : tr("Ответ / Перевод")}</span>
                </div>
                <div className="separator-line" />
              </div>

              {(card.video_back_url || deckVideo?.url) && (
                <div className="video-container-card">
                  <video src={card.video_back_url || deckVideo?.url} autoPlay loop muted playsInline />
                </div>
              )}
              
              {/* 3. BACK ANSWER BLOCK */}
              <div className="back-answer-block">
                {(() => {
                  const targetBackAudioUrl = studyMode === 'reverse'
                    ? getResolvedAudioUrl(card, false)
                    : getResolvedAudioUrl(card, true);
                  if (targetBackAudioUrl) {
                    return (
                      <CardAudioPlayer
                        audioUrl={targetBackAudioUrl}
                        playAudio={() => {
                          if (studyMode === 'reverse') {
                            const frontAudio = getResolvedAudioUrl(card, false);
                            if (frontAudio && playAudio) playAudio(frontAudio);
                          } else {
                            onPlayBackAudio?.(card);
                          }
                        }}
                        pauseAudio={audioControls?.pauseAudio}
                        resumeAudio={audioControls?.resumeAudio}
                        togglePlayPause={(url) => {
                          if (audioControls?.currentUrl === url && audioControls?.audioState !== 'idle') {
                            audioControls.togglePlayPause(url);
                          } else {
                            if (studyMode === 'reverse') {
                              const frontAudio = getResolvedAudioUrl(card, false);
                              if (frontAudio && playAudio) playAudio(frontAudio);
                            } else {
                              onPlayBackAudio?.(card);
                            }
                          }
                        }}
                        stopAudio={audioControls?.stopAudio}
                        seekAudio={audioControls?.seekAudio}
                        setPlaybackSpeed={audioControls?.setPlaybackSpeed}
                        audioState={audioControls?.audioState}
                        currentUrl={audioControls?.currentUrl}
                        currentTime={audioControls?.currentTime}
                        duration={audioControls?.duration}
                        playbackRate={audioControls?.playbackRate}
                        isAudioLoading={isAudioLoading || audioControls?.isAudioLoading}
                        isGenerating={card.audio_is_generating}
                        voicePicker={frontVoicePicker}
                        cardText={frontText}
                        cardId={card?.id}
                        isBack={studyMode !== 'reverse'}
                        disabled={loading || isAudioLoading}
                      />
                    );
                  }
                  return (
                    <button
                      className="audio-btn-translation"
                      disabled={loading || isAudioLoading}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (studyMode === 'reverse') {
                          if (card.audio_url && playAudio) playAudio(card.audio_url);
                        } else {
                          onPlayBackAudio?.(card);
                        }
                      }}
                      title={tr("Озвучить")}
                    >
                      {isAudioLoading ? (
                        card.audio_is_generating ? (
                          <Sparkles size={22} className="sparkles-spin" style={{ color: '#a855f7' }} />
                        ) : (
                          <RefreshCw size={22} className="spin" />
                        )
                      ) : (
                        <Volume2 size={22} />
                      )}
                    </button>
                  );
                })()}
                <div id="tut-study-answer" className="text-back" style={backCardStyle}>
                  {cleanBracketSyntax(stripMarkdown(studyMode === 'reverse' ? card.front : card.back))}
                </div>
              </div>
              
              {imageUrl && (
                <div style={{
                  width: '100%',
                  height: `${cardImageHeight}px`,
                  overflow: 'hidden',
                  borderRadius: '12px',
                  marginTop: '12px',
                  marginBottom: '12px',
                  flexShrink: 0
                }}>
                  <img
                    src={imageUrl}
                    alt=""
                    style={{
                      display: 'block',
                      width: '100%',
                      height: '100%',
                      objectFit: 'contain',
                      borderRadius: '12px'
                    }}
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                </div>
              )}

              {/* 4. EXPLICIT SEPARATOR & CONTEXT BLOCK */}
              {card.context && (
                <div className="card-context-wrapper">
                  <div className="context-separator">
                    <div className="separator-line" />
                    <div className="context-badge">
                      <BookOpen size={12} />
                      <span>{tr("Контекст и примеры")}</span>
                    </div>
                    <div className="separator-line" />
                  </div>
                  <div className="text-context" style={contextStyle}>
                    {stripMarkdown(card.context)}
                  </div>
                </div>
              )}

              {card.creator_name && (
                <div className="creator-badge-corner" style={{ position: 'absolute', bottom: '10px', right: '10px', display: 'flex', alignItems: 'center', gap: '6px', background: 'rgba(0,0,0,0.4)', padding: '4px 10px', borderRadius: '20px', zIndex: 10, opacity: 0.8 }}>
                  {card.creator_avatar ? (
                    <img src={card.creator_avatar} alt="avatar" style={{ width: 18, height: 18, borderRadius: '50%' }} />
                  ) : (
                    <div style={{ width: 18, height: 18, borderRadius: '50%', background: 'var(--primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white', fontSize: '9px' }}>
                      {card.creator_name.charAt(0)}
                    </div>
                  )}
                  <span style={{ fontSize: '0.65rem', color: '#fff', fontWeight: 500 }}>{card.creator_name}</span>
                </div>
              )}

              {/* Card Level Badge (Bottom-Left Corner) */}
              <CardLevelBadge card={card} textColor={backCardStyle?.color} style={{ position: 'absolute', bottom: '12px', left: '12px', zIndex: 15 }} />

              <div 
                className="flip-hint-badge" 
                style={{ 
                  marginTop: '18px', 
                  cursor: 'pointer', 
                  background: 'rgba(20, 15, 38, 0.9)', 
                  border: '1.5px solid rgba(168, 85, 247, 0.55)', 
                  color: '#ffffff', 
                  fontWeight: 700, 
                  fontSize: '0.92rem', 
                  padding: '8px 16px', 
                  borderRadius: '14px',
                  boxShadow: '0 4px 18px rgba(0, 0, 0, 0.45)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '8px'
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  onFlip(false);
                }}
              >
                <Eye size={16} style={{ color: '#c084fc' }} />
                <span>{tr("Вернуться к лицевой стороне")}</span>
              </div>
            </div>
          </div>
        )}

        {loading && (
          <div className="card-loading-overlay">
            <RefreshCw size={40} className="spin" />
          </div>
        )}
      </motion.div>
    </AnimatePresence>
  );
});
