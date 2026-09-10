import { tr, getInterfaceLanguage } from '../i18n/locale';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api from '../services/api';
import { useDeckStore } from '../store/useDeckStore';
import { useSessionStore } from '../store/useSessionStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { useLanguageStore } from '../store/useLanguageStore';
import { getTtsVoiceForLang } from '../constants/languageConstants';
import { stripMarkdown } from '../utils/text';

const formatRate = (value) => `${value >= 0 ? '+' : ''}${value}%`;
const getCardText = (targetCard, side) => {
  if (!targetCard) return '';
  return side === 'back'
    ? (targetCard.back ?? targetCard.back_text ?? '')
    : (targetCard.front ?? targetCard.front_text ?? '');
};

export const filterAndSortAutoplayCards = (cards, order) => {
  if (!cards || !cards.length) return [];

  if (order === 'srs') {
    const endOfToday = new Date();
    endOfToday.setHours(23, 59, 59, 999);

    const dueReviewCards = [];
    const dueLearningCards = [];
    const newCards = [];

    for (const c of cards) {
      if (!c.queue || c.queue === 'new') {
        newCards.push(c);
      } else if (c.queue === 'learning' || c.queue === 'relearning') {
        if (!c.next_review || new Date(c.next_review) <= endOfToday) {
          dueLearningCards.push(c);
        }
      } else if (c.queue === 'review') {
        if (!c.next_review || new Date(c.next_review) <= endOfToday) {
          dueReviewCards.push(c);
        }
      }
    }

    // Overdue / due review cards first (oldest review time first)
    dueReviewCards.sort((a, b) => {
      const timeA = a.next_review ? new Date(a.next_review).getTime() : 0;
      const timeB = b.next_review ? new Date(b.next_review).getTime() : 0;
      return timeA - timeB;
    });

    // Learning / relearning cards scheduled for today
    dueLearningCards.sort((a, b) => {
      const timeA = a.next_review ? new Date(a.next_review).getTime() : 0;
      const timeB = b.next_review ? new Date(b.next_review).getTime() : 0;
      return timeA - timeB;
    });

    // New cards sorted by position asc, id asc
    newCards.sort((a, b) => (a.position || 0) - (b.position || 0) || (a.id || 0) - (b.id || 0));

    return [...dueReviewCards, ...dueLearningCards, ...newCards];
  }

  // Default 'list' mode: linear sequence by position asc, id asc
  return [...cards].sort((a, b) => (a.position || 0) - (b.position || 0) || (a.id || 0) - (b.id || 0));
};

export const useAutoplay = ({ card, playAudio, stopAudio, showToast, startBackgroundLock, stopBackgroundLock }) => {
  const runRef = useRef(0);
  const timerRef = useRef(null);
  const cardRef = useRef(card);
  const autoplayCardsRef = useRef([]);
  const currentCardIdRef = useRef(card?.id);
  const runCardCycleRef = useRef(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    cardRef.current = card;
    if (card?.id !== currentCardIdRef.current) {
      currentCardIdRef.current = card?.id;
    }
  }, [card]);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const isCurrentRun = useCallback((runId) => (
    runRef.current === runId && useSessionStore.getState().autoplayState === 'playing'
  ), []);

  const wait = useCallback((seconds, runId) => new Promise((resolve) => {
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      resolve(isCurrentRun(runId));
    }, Math.max(0, Number(seconds) || 0) * 1000);
  }), [clearTimer, isCurrentRun]);

  const waitForAudio = useCallback((url, runId) => new Promise((resolve) => {
    if (!url || !isCurrentRun(runId)) {
      resolve(false);
      return;
    }

    playAudio(url, () => resolve(isCurrentRun(runId)));
  }), [isCurrentRun, playAudio]);

  const updateCardAudio = useCallback((cardId, patch) => {
    const session = useSessionStore.getState();
    const deck = useDeckStore.getState();

    session.setCard((current) => (
      current?.id === cardId ? { ...current, ...patch } : current
    ));
    if (typeof session.setStudyHistory === 'function') {
      const currentHistory = session.studyHistory || [];
      session.setStudyHistory(currentHistory.map((item) => (
        item?.id === cardId ? { ...item, ...patch } : item
      )));
    }
    deck.setDeckCards((deck.deckCards || []).map((item) => (
      item.id === cardId ? { ...item, ...patch } : item
    )));

    // Mutate local autoplayCardsRef so subsequent loops or card reviews don't re-generate audio
    autoplayCardsRef.current = (autoplayCardsRef.current || []).map((item) => (
      item.id === cardId ? { ...item, ...patch } : item
    ));
  }, []);

  const ensureAudio = useCallback(async (targetCard, side, runId) => {
    if (!targetCard || !isCurrentRun(runId)) return null;

    const settings = useSettingsStore.getState();
    const isBack = side === 'back';
    const urlKey = isBack ? 'audio_back_url' : 'audio_url';
    const pathKey = isBack ? 'audio_back_path' : 'audio_path';
    const text = getCardText(targetCard, side);
    const deckTargetLang = targetCard.target_language || useDeckStore.getState().currentDeck?.target_language || useLanguageStore.getState().activeLanguage || 'de';
    const nativeLang = getInterfaceLanguage();
    const lang = isBack ? nativeLang : deckTargetLang;
    const rate = formatRate(isBack ? settings.ttsSpeedRu : settings.ttsSpeed);
    const voice = isBack 
      ? getTtsVoiceForLang(nativeLang, settings.adminSettings)
      : getTtsVoiceForLang(deckTargetLang, settings.adminSettings);
    const forceGenerate = isBack ? settings.autoplayForceBackAudio : settings.autoplayForceFrontAudio;
    const hasWrongBackAudio = isBack && (
      (targetCard.audio_back_url && targetCard.audio_url && targetCard.audio_back_url === targetCard.audio_url) ||
      (targetCard.audio_back_path && targetCard.audio_path && targetCard.audio_back_path === targetCard.audio_path)
    );

    const existingUrl = targetCard[urlKey] || (targetCard[pathKey] ? (targetCard[pathKey].startsWith('http') || targetCard[pathKey].startsWith('/api/') ? targetCard[pathKey] : `/api/media/audio/${targetCard[pathKey]}`) : null);
    if (existingUrl && !hasWrongBackAudio && !forceGenerate) return existingUrl;
    if (!text?.trim()) return null;

    setStatus(isBack ? tr("Генерируем перевод") : tr("Генерируем фразу"));
    let generated;
    try {
      generated = await api.post('/media/generate-card-audio', {
        card_id: targetCard.id,
        side,
        text,
        lang,
        rate,
        voice,
      });
    } catch (err) {
      console.error('Audio generation failed:', err);
      showToast?.(tr("Не удалось сгенерировать {{p0}}: {{p1}}", { p0: isBack ? tr("перевод") : tr("фразу"), p1: err.response?.data?.detail || err.message }));
      return null;
    }
    if (!isCurrentRun(runId)) return null;

    const audioPatch = {
      [pathKey]: generated.data.path,
      [urlKey]: generated.data.url
    };

    if (!isCurrentRun(runId)) return null;

    const mergedPatch = {
      ...audioPatch,
      [pathKey]: generated.data.path,
      [urlKey]: generated.data.url
    };
    updateCardAudio(targetCard.id, mergedPatch);
    return mergedPatch[urlKey];
  }, [isCurrentRun, showToast, updateCardAudio]);

  const getAutoplayCards = useCallback(async () => {
    const deckStore = useDeckStore.getState();
    const settings = useSettingsStore.getState();
    const currentDeck = deckStore.currentDeck;

    if (!currentDeck) return [];
    if (currentDeck.id === 'duplicates') {
      return filterAndSortAutoplayCards(deckStore.duplicateCards || [], settings.autoplayOrder);
    }

    await deckStore.fetchDeckCards(currentDeck.id);
    const rawCards = useDeckStore.getState().deckCards || [];
    return filterAndSortAutoplayCards(rawCards, settings.autoplayOrder);
  }, []);

  const prepareAutoplayCards = useCallback(async () => {
    const cards = await getAutoplayCards();
    autoplayCardsRef.current = cards;
    return cards;
  }, [getAutoplayCards]);

  const deckCards = useDeckStore(s => s.deckCards);
  const duplicateCards = useDeckStore(s => s.duplicateCards);
  const currentDeck = useDeckStore(s => s.currentDeck);
  const autoplayOrder = useSettingsStore(s => s.autoplayOrder);

  const activeAutoplayCards = useMemo(() => {
    if (!currentDeck) return [];
    const source = currentDeck.id === 'duplicates' ? (duplicateCards || []) : (deckCards || []);
    return filterAndSortAutoplayCards(source, autoplayOrder);
  }, [currentDeck, duplicateCards, deckCards, autoplayOrder]);

  useEffect(() => {
    autoplayCardsRef.current = activeAutoplayCards;
  }, [activeAutoplayCards]);

  const moveToNextCard = useCallback(async (currentCard, runId) => {
    const settings = useSettingsStore.getState();
    let cards = autoplayCardsRef.current.length
      ? autoplayCardsRef.current
      : await prepareAutoplayCards();
    if (!isCurrentRun(runId) || !cards.length) return false;

    let currentIndex = cards.findIndex((item) => String(item.id) === String(currentCard.id));
    let nextIndex = currentIndex >= 0 ? currentIndex + 1 : 0;

    if (nextIndex >= cards.length && useDeckStore.getState().currentDeck?.id !== 'duplicates') {
      const refreshedCards = await getAutoplayCards();
      if (!isCurrentRun(runId)) return false;

      const refreshedIndex = refreshedCards.findIndex((item) => String(item.id) === String(currentCard.id));
      if (refreshedIndex >= 0 && refreshedIndex + 1 < refreshedCards.length) {
        cards = refreshedCards;
        autoplayCardsRef.current = refreshedCards;
        currentIndex = refreshedIndex;
        nextIndex = currentIndex + 1;
      }
    }

    if (nextIndex >= cards.length) {
      if (!settings.autoplayLoop) {
        useSessionStore.getState().stopAutoplay();
        setStatus('');
        stopAudio();
        return false;
      }
      useSessionStore.getState().setCard(cards[0]);
      return true;
    }

    useSessionStore.getState().setCard(cards[nextIndex]);
    return true;
  }, [getAutoplayCards, isCurrentRun, prepareAutoplayCards, stopAudio]);

  const runCardCycle = useCallback(async (runId) => {
    const targetCard = cardRef.current;
    if (!targetCard || !isCurrentRun(runId)) return;

    const session = useSessionStore.getState();
    const settings = useSettingsStore.getState();

    try {
      session.setIsFlipped(false);
      const frontRepeats = Math.max(1, Number(settings.autoplayFrontRepeat) || 1);
      const backRepeats = Math.max(1, Number(settings.autoplayBackRepeat) || 1);

      // --- 1. FRONT SIDE REPEAT CYCLE ---
      for (let i = 1; i <= frontRepeats; i++) {
        if (!isCurrentRun(runId)) return;
        const repeatPrefix = frontRepeats > 1 ? tr("[Фраза {{p0}}/{{p1}}] ", { p0: i, p1: frontRepeats }) : '';

        setStatus(tr("{{p0}}Озвучиваем фразу", { p0: repeatPrefix }));
        if ('mediaSession' in navigator) {
          navigator.mediaSession.metadata = new MediaMetadata({
            title: stripMarkdown(targetCard.front || ''),
            artist: tr("Lerne TMA (Фраза{{p0}})", { p0: frontRepeats > 1 ? ` ${i}/${frontRepeats}` : '' }),
            album: useDeckStore.getState().currentDeck?.name || tr("Режим изучения")
          });
        }
        const frontUrl = await ensureAudio(targetCard, 'front', runId);
        if (frontUrl) await waitForAudio(frontUrl, runId);
        if (!isCurrentRun(runId)) return;

        setStatus(tr("{{p0}}Пауза {{p1}}с", { p0: repeatPrefix, p1: settings.autoplayFrontPause }));
        const afterFrontPause = await wait(settings.autoplayFrontPause, runId);
        if (!afterFrontPause) return;
      }

      if (!isCurrentRun(runId)) return;

      // --- 2. BACK SIDE REPEAT CYCLE ---
      session.setIsFlipped(true);
      for (let i = 1; i <= backRepeats; i++) {
        if (!isCurrentRun(runId)) return;
        const repeatPrefix = backRepeats > 1 ? tr("[Перевод {{p0}}/{{p1}}] ", { p0: i, p1: backRepeats }) : '';

        setStatus(tr("{{p0}}Озвучиваем перевод", { p0: repeatPrefix }));
        const latestCard = useSessionStore.getState().card || targetCard;
        if ('mediaSession' in navigator) {
          navigator.mediaSession.metadata = new MediaMetadata({
            title: stripMarkdown(latestCard.back || ''),
            artist: tr("Lerne TMA (Перевод{{p0}})", { p0: backRepeats > 1 ? ` ${i}/${backRepeats}` : '' }),
            album: useDeckStore.getState().currentDeck?.name || tr("Режим изучения")
          });
        }
        const backUrl = await ensureAudio(latestCard, 'back', runId);
        if (backUrl) await waitForAudio(backUrl, runId);
        if (!isCurrentRun(runId)) return;

        setStatus(tr("{{p0}}Пауза {{p1}}с", { p0: repeatPrefix, p1: settings.autoplayBackPause }));
        const afterBackPause = await wait(settings.autoplayBackPause, runId);
        if (!afterBackPause) return;
      }

      if (!isCurrentRun(runId)) return;

      const latestCard = useSessionStore.getState().card || targetCard;
      await moveToNextCard(latestCard, runId);
    } catch (err) {
      console.error('Autoplay error:', err);
      if (isCurrentRun(runId)) {
        showToast?.(tr("Ошибка авто-режима: {{p0}}", { p0: err.response?.data?.detail || err.message }));
        useSessionStore.getState().stopAutoplay();
        stopAudio();
        setStatus('');
      }
    }
  }, [ensureAudio, isCurrentRun, moveToNextCard, showToast, stopAudio, wait, waitForAudio]);

  useEffect(() => {
    runCardCycleRef.current = runCardCycle;
  }, [runCardCycle]);

  const restart = useCallback(() => {
    clearTimer();
    stopAudio();
    const runId = runRef.current + 1;
    runRef.current = runId;
    runCardCycle(runId);
  }, [clearTimer, runCardCycle, stopAudio]);

  const start = useCallback(() => {
    const settings = useSettingsStore.getState();
    startBackgroundLock?.();
    prepareAutoplayCards().then((cards) => {
      if (!cards || !cards.length) {
        if (settings.autoplayOrder === 'srs') {
          showToast?.(tr("На сегодня нет карточек для повторения по SRS"));
          setStatus(tr("На сегодня нет карточек по SRS"));
        } else {
          showToast?.(tr("В колоде нет доступных карточек"));
          setStatus(tr("Нет карточек в колоде"));
        }
        stopAudio();
        stopBackgroundLock?.();
        useSessionStore.getState().stopAutoplay();
        return;
      }

      useSessionStore.getState().setAutoplayState('playing');

      // Check if current card is in the autoplay queue
      const currentCard = cardRef.current;
      const existsInQueue = currentCard && cards.some(c => String(c.id) === String(currentCard.id));
      if (!existsInQueue) {
        useSessionStore.getState().setCard(cards[0]);
      }

      restart();
    });
  }, [prepareAutoplayCards, restart, showToast, startBackgroundLock, stopAudio, stopBackgroundLock]);

  const stop = useCallback(() => {
    runRef.current += 1;
    autoplayCardsRef.current = [];
    clearTimer();
    stopAudio();
    stopBackgroundLock?.();
    setStatus('');
    useSessionStore.getState().stopAutoplay();
  }, [clearTimer, stopAudio, stopBackgroundLock]);

  const pause = useCallback(() => {
    runRef.current += 1;
    clearTimer();
    stopAudio();
    stopBackgroundLock?.();
    setStatus(tr("Пауза"));
    useSessionStore.getState().pauseAutoplay();
  }, [clearTimer, stopAudio, stopBackgroundLock]);

  const resume = useCallback(() => {
    if (!cardRef.current) return;
    useSessionStore.getState().setAutoplayState('playing');
    startBackgroundLock?.();
    restart();
  }, [restart, startBackgroundLock]);

  const cancelCurrent = useCallback(() => {
    runRef.current += 1;
    clearTimer();
    stopAudio();
    setStatus('');
  }, [clearTimer, stopAudio]);

  useEffect(() => {
    const state = useSessionStore.getState().autoplayState;
    if (state === 'playing' && card?.id) {
      restart();
    }
  }, [card?.id, restart]);

  useEffect(() => () => {
    runRef.current += 1;
    autoplayCardsRef.current = [];
    clearTimer();
    stopAudio();
    stopBackgroundLock?.();
  }, [clearTimer, stopAudio, stopBackgroundLock]);

  return {
    start,
    stop,
    pause,
    resume,
    restart,
    cancelCurrent,
    status,
    autoplayCards: activeAutoplayCards
  };
};
