import { tr } from '../../i18n/locale';
import { useInterfaceLocale } from '../../i18n/useInterfaceLocale';
import React, { useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Clock, ArrowLeft, ArrowRight, 
  MapPin, RefreshCw, BookOpen, ShieldCheck, Check 
} from 'lucide-react';
import { useLidStore } from '../../store/useLidStore';
import { useUiStore } from '../../store/useUiStore';
import { getBundeslandByCode } from '../../data/bundeslaender';
import { BundeslandModal } from './BundeslandModal';
import { LidQuestionNavigator } from './LidQuestionNavigator';
import { LidQuestionCard } from './LidQuestionCard';
import { LidResultsView } from './LidResultsView';
import { useAudio } from '../../hooks/useAudio';
import { useVoicePicker } from '../../hooks/useVoicePicker';
import { CardAudioPlayer } from '../study/CardAudioPlayer';
import './LidExam.css';

export const LidExamView = () => {
  useInterfaceLocale();
  const { setView, showToast } = useUiStore();
  const {
    screen,
    examMode,
    isLoadingTicket,
    currentQuestionIndex,
    questions,
    answers,
    timeRemaining,
    isTimerActive,
    selectedLandCode,
    isLandModalOpen,
    openLandModal,
    closeLandModal,
    startSimulation,
    goToQuestion,
    nextQuestion,
    prevQuestion,
    setAnswer,
    tickTimer,
    finishSimulation,
    resetToMenu
  } = useLidStore();

  // Audio playback and voice picker for practice mode
  const audioControls = useAudio(false);
  const voicePicker = useVoicePicker('de');
  const stopAudio = audioControls.stopAudio;
  const setPreviewUrl = voicePicker.setPreviewUrl;

  // Stop audio and reset preview when changing questions
  useEffect(() => {
    stopAudio?.();
    setPreviewUrl?.(null);
  }, [currentQuestionIndex, stopAudio, setPreviewUrl]);

  // Stop audio when leaving the running simulation or in exam mode
  useEffect(() => {
    if (screen !== 'running' || examMode !== 'practice') {
      stopAudio?.();
    }
  }, [screen, examMode, stopAudio]);

  // Timer interval
  useEffect(() => {
    let interval = null;
    if (isTimerActive) {
      interval = setInterval(() => {
        tickTimer();
      }, 1000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isTimerActive, tickTimer]);

  const stateInfo = selectedLandCode ? getBundeslandByCode(selectedLandCode) : null;
  const currentQ = questions[currentQuestionIndex];
  const totalQ = questions.length;
  const answeredCount = Object.keys(answers).length;

  const formatTimer = (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins < 10 ? '0' : ''}${mins}:${secs < 10 ? '0' : ''}${secs}`;
  };

  const handleStartExamFlow = (mode) => {
    if (!selectedLandCode) {
      showToast(tr("Пожалуйста, выберите федеральную землю для экзамена"), 'warning');
      useLidStore.getState().setPendingExamMode(mode);
      openLandModal(true);
    } else {
      startSimulation(mode);
    }
  };

  const handleFinishClick = () => {
    finishSimulation();
  };

  const handleBackToDecks = () => {
    resetToMenu();
    setView('decks');
  };

  return (
    <div className="lid-exam-view-wrapper">
      {/* 1. Menu Screen: Mode & Land Selection */}
      {screen === 'menu' && (
        <motion.div
          className="lid-menu-screen"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -12 }}
        >
          {/* Header */}
          <div className="lid-menu-header glass">
            <button
              type="button"
              className="lid-back-btn"
              onClick={handleBackToDecks}
              title={tr("Назад к колодам")}
            >
              <ArrowLeft size={20} />
            </button>
            <div className="lid-menu-title-wrap">
              <div className="lid-menu-pill-row">
                <span className="lid-hero-flag">🇩🇪</span>
                <span className="lid-menu-tag">BAMF • LiD</span>
              </div>
              <h1 className="lid-menu-title">{tr("Симуляция экзамена")}</h1>
              <span className="lid-menu-subtitle">Leben in Deutschland • Einbürgerungstest</span>
            </div>
          </div>

          {/* Current Bundesland Card */}
          {selectedLandCode && stateInfo ? (
            <div className="lid-selected-land-banner glass" onClick={() => openLandModal(true)}>
              <div className="lid-land-banner-left">
                <div className="lid-land-banner-icon">
                  <span>{stateInfo?.symbol || '🇩🇪'}</span>
                </div>
                <div className="lid-land-banner-info">
                  <div className="lid-land-banner-label">{tr("Ваша земля для теста:")}</div>
                  <div className="lid-land-banner-name">
                    {stateInfo?.nameDe} <span className="lid-ru-sub">({stateInfo?.nameRu})</span>
                  </div>
                  <div className="lid-land-banner-capital">
                    <MapPin size={12} />
                    <span>{tr("Столица:")}{' '}{stateInfo?.capital}</span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                className="btn btn-secondary lid-btn-change-land-sm"
                onClick={(e) => {
                  e.stopPropagation();
                  openLandModal(true);
                }}
              >
                <RefreshCw size={14} />
                <span>{tr("Сменить землю")}</span>
              </button>
            </div>
          ) : (
            <div 
              className="lid-selected-land-banner glass lid-land-banner-unselected"
              onClick={() => openLandModal(true)}
            >
              <div className="lid-land-banner-left">
                <div className="lid-land-flag-unselected">
                  <MapPin size={22} color="#facc15" />
                </div>
                <div className="lid-land-banner-info">
                  <div className="lid-land-banner-label" style={{ color: '#facc15' }}>{tr("⚠️ Земля не выбрана")}{' '}</div>
                  <div className="lid-land-banner-name">{tr("Выберите землю")}{' '}</div>
                  <div className="lid-land-banner-capital">
                    <span>{tr("3 региональных вопроса войдут в билет")}</span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                className="lid-btn-choose-land-sm"
                onClick={(e) => {
                  e.stopPropagation();
                  openLandModal(true);
                }}
              >
                <span>{tr("Выбрать землю")}</span>
                <ArrowRight size={14} />
              </button>
            </div>
          )}

          {/* Exam Rules Pill */}
          <div className="lid-rules-banner glass">
            <div className="lid-rule-item">
              <span className="lid-rule-val">33</span>
              <span className="lid-rule-lbl">{tr("вопроса")}</span>
            </div>
            <div className="lid-rule-divider" />
            <div className="lid-rule-item">
              <span className="lid-rule-val">60:00</span>
              <span className="lid-rule-lbl">{tr("таймер")}</span>
            </div>
            <div className="lid-rule-divider" />
            <div className="lid-rule-item">
              <span className="lid-rule-val">17 / 33</span>
              <span className="lid-rule-lbl">{tr("порог сдачи")}</span>
            </div>
          </div>

          {/* Mode Cards */}
          <div className="lid-modes-container">
            {/* Mode 1: Exam Mode */}
            <motion.div
              className="lid-mode-card glass exam-mode"
              whileHover={{ scale: 1.015, y: -2 }}
              whileTap={{ scale: 0.985 }}
              onClick={() => handleStartExamFlow('exam')}
            >
              <div className="lid-mode-badge exam">
                <ShieldCheck size={16} />
                <span>{tr("Реальный экзамен")}</span>
              </div>
              <h3 className="lid-mode-title">{tr("Режим экзамена (Exam Mode)")}</h3>
              <ul className="lid-mode-features">
                <li>{tr("⏱️ Строгий таймер 60 минут")}</li>
                <li>{tr("🔒 Ответы скрыты до завершения билета")}</li>
                <li>{tr("🚫 Без озвучки и перевода (условия реального теста)")}</li>
                <li>{tr("🔄 Свободный возврат и смена вариантов (1..33)")}</li>
                <li>{tr("📊 Итоговый балл (🟢 СДАНО / 🔴 НЕ СДАНО) и разбор ошибок")}</li>
              </ul>
              <button
                type="button"
                className="btn btn-primary lid-btn-start-mode exam"
                onClick={(e) => {
                  e.stopPropagation();
                  handleStartExamFlow('exam');
                }}
              >
                <span>{tr("Начать экзамен")}</span>
                <ArrowRight size={18} />
              </button>
            </motion.div>

            {/* Mode 2: Practice Mode */}
            <motion.div
              className="lid-mode-card glass practice-mode"
              whileHover={{ scale: 1.015, y: -2 }}
              whileTap={{ scale: 0.985 }}
              onClick={() => handleStartExamFlow('practice')}
            >
              <div className="lid-mode-badge practice">
                <BookOpen size={16} />
                <span>{tr("Обучение")}</span>
              </div>
              <h3 className="lid-mode-title">{tr("Режим тренировки (Practice Mode)")}</h3>
              <ul className="lid-mode-features">
                <li>{tr("⏱️ Таймер 60 минут")}</li>
                <li>{tr("🔊 Озвучка вопросов и перевод на русский")}</li>
                <li>{tr("💡 Мгновенная подсветка правильного ответа")}</li>
                <li>{tr("📖 Подробные объяснения и разбор на обороте")}</li>
              </ul>
              <button
                type="button"
                className="btn btn-secondary lid-btn-start-mode practice"
                onClick={(e) => {
                  e.stopPropagation();
                  handleStartExamFlow('practice');
                }}
              >
                <span>{tr("Начать тренировку")}</span>
                <ArrowRight size={18} />
              </button>
            </motion.div>
          </div>
        </motion.div>
      )}

      {/* Loading Ticket Overlay */}
      {isLoadingTicket && (
        <div className="lid-ticket-loading-overlay glass" style={{
          position: 'fixed',
          inset: 0,
          zIndex: 9999,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(9, 13, 22, 0.85)',
          backdropFilter: 'blur(10px)',
          padding: '20px'
        }}>
          <RefreshCw size={40} className="spin" color="#38bdf8" />
          <h3 style={{ marginTop: 16, marginBottom: 6, color: '#f8fafc', fontSize: '1.2rem', fontWeight: 700 }}>{tr("Формирование билета...")}{' '}</h3>
          <p style={{ color: '#94a3b8', fontSize: '0.85rem', margin: 0, textAlign: 'center' }}>{tr("Подбираем 33 официальных вопроса экзамена BAMF")}{' '}</p>
        </div>
      )}

      {/* Defensive fallback if screen is running but questions are empty */}
      {screen === 'running' && (!currentQ || totalQ === 0) && !isLoadingTicket && (
        <div className="lid-error-state glass" style={{ 
          padding: '36px 20px', 
          textAlign: 'center', 
          marginTop: 40, 
          borderRadius: 20,
          background: 'rgba(30, 41, 59, 0.7)',
          border: '1px solid rgba(255, 255, 255, 0.1)'
        }}>
          <h3 style={{ color: '#f87171', marginBottom: 8, fontSize: '1.2rem' }}>{tr("Не удалось загрузить вопросы")}</h3>
          <p style={{ color: '#94a3b8', fontSize: '0.9rem', marginBottom: 20, maxWidth: 320, margin: '0 auto 20px' }}>{tr("Произошла задержка при получении вопросов. Нажмите кнопку ниже, чтобы вернуться в меню.")}{' '}</p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => resetToMenu()}
            style={{ margin: '0 auto', display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 24px' }}
          >
            <ArrowLeft size={16} />
            <span>{tr("Вернуться в меню")}</span>
          </button>
        </div>
      )}

      {/* 2. Running Simulation Screen */}
      {screen === 'running' && currentQ && (
        <div className="lid-running-screen">
          {/* Top Sticky Header */}
          <div className="lid-exam-top-bar glass">
            <div className="lid-top-bar-left">
              <button
                type="button"
                className="lid-exam-exit-btn"
                onClick={() => resetToMenu()}
                title={tr("Назад в меню")}
              >
                <ArrowLeft size={18} />
              </button>
              <div className="lid-exam-mode-tag">
                {examMode === 'exam' ? tr("📝 Экзамен") : tr("🎓 Тренировка")}
              </div>
            </div>

            {/* Timer Badge */}
            <div className={`lid-exam-timer ${timeRemaining < 300 ? 'timer-warning' : ''}`}>
              <Clock size={16} className={timeRemaining < 300 ? 'pulse' : ''} />
              <span className="lid-timer-digits">{formatTimer(timeRemaining)}</span>
            </div>

            {/* Finish Button */}
            <button
              type="button"
              className="btn btn-primary lid-btn-finish-exam"
              onClick={handleFinishClick}
            >{tr("Завершить")}{' '}</button>
          </div>

          {/* Progress bar info */}
          <div className="lid-exam-progress-bar-row">
            <span className="lid-progress-answered">{tr("Отвечено:")}{' '}<strong>{answeredCount}</strong>{' '}{tr("из")}{' '}{totalQ}
            </span>
            <div className="lid-progress-track">
              <div
                className="lid-progress-fill"
                style={{ width: `${(answeredCount / totalQ) * 100}%` }}
              />
            </div>
          </div>

          {/* Navigator 1..33 */}
          <LidQuestionNavigator
            questions={questions}
            currentIndex={currentQuestionIndex}
            answers={answers}
            examMode={examMode}
            onSelectIndex={goToQuestion}
          />

          {/* Question Card */}
          <div className="lid-question-view-area">
            <AnimatePresence mode="wait">
              <motion.div
                key={`question-${currentQ.id}`}
                initial={{ opacity: 0, x: 15 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -15 }}
                transition={{ duration: 0.18 }}
              >
                <LidQuestionCard
                  question={currentQ}
                  examIndex={currentQuestionIndex + 1}
                  totalQuestions={totalQ}
                  examMode={examMode}
                  selectedAnswer={answers[currentQ.id]}
                  onSelectAnswer={setAnswer}
                />
              </motion.div>
            </AnimatePresence>
          </div>

          {/* Bottom Navigation Buttons */}
          <div className="lid-bottom-nav-bar glass">
            <button
              type="button"
              className="btn btn-secondary lid-nav-prev-btn"
              disabled={currentQuestionIndex === 0}
              onClick={prevQuestion}
            >
              <ArrowLeft size={16} />
              <span>{tr("Назад")}</span>
            </button>

            {/* Official BAMF Question Number (1–300): center between Назад and Далее, shown only after answering */}
            <div className="lid-nav-bamf-num-center">
              {answers[currentQ?.id] && currentQ?.bamfNumber ? (
                <div className="lid-nav-bamf-num-badge" title={tr("Номер карточки в каталоге BAMF (1–300)")}>
                  <span>№ {currentQ.bamfNumber}</span>
                </div>
              ) : null}
            </div>

            {currentQuestionIndex < totalQ - 1 ? (
              <button
                type="button"
                className="btn btn-primary lid-nav-next-btn"
                onClick={nextQuestion}
              >
                <span>{tr("Далее")}</span>
                <ArrowRight size={16} />
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-primary lid-nav-submit-btn"
                onClick={handleFinishClick}
              >
                <span>{tr("Завершить тест")}</span>
                <Check size={16} />
              </button>
            )}
          </div>

          {/* Floating Audio Player in Practice Mode */}
          {examMode === 'practice' && currentQ && (
            <CardAudioPlayer
              audioUrl={currentQ.audioUrl || currentQ.audio_path}
              playAudio={audioControls.playAudio}
              pauseAudio={audioControls.pauseAudio}
              resumeAudio={audioControls.resumeAudio}
              togglePlayPause={audioControls.togglePlayPause}
              stopAudio={audioControls.stopAudio}
              seekAudio={audioControls.seekAudio}
              setPlaybackSpeed={audioControls.setPlaybackSpeed}
              audioState={audioControls.audioState}
              currentUrl={audioControls.currentUrl}
              currentTime={audioControls.currentTime}
              duration={audioControls.duration}
              playbackRate={audioControls.playbackRate}
              isAudioLoading={audioControls.isAudioLoading}
              voicePicker={voicePicker}
              cardText={currentQ.question}
              cardId={currentQ.id}
              wrapperClassName="lid-exam-audio-wrapper"
            />
          )}
        </div>
      )}

      {/* 3. Results Screen */}
      {screen === 'results' && (
        <LidResultsView onBackToMenu={handleBackToDecks} />
      )}

      {/* Bundesland Selection Modal */}
      <BundeslandModal
        isOpen={isLandModalOpen}
        onClose={closeLandModal}
        onConfirm={() => {
          closeLandModal();
        }}
      />
    </div>
  );
};
