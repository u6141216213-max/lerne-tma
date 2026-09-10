import { tr } from '../i18n/locale';
import { useState, useCallback, useRef, useEffect } from 'react';
import api from '../services/api';
import { VOICES_BY_LANG, getTtsVoiceForLang } from '../constants/languageConstants';
import { useSettingsStore } from '../store/useSettingsStore';

/**
 * useVoicePicker — manages per-card voice selection and on-demand audio preview generation.
 *
 * Design principles:
 *  - Preview URLs and word boundaries are cached in-memory for the session lifetime.
 *    We never write a preview to the DB; the card's original audio_url is untouched.
 *  - `sessionVoice` (optional): if passed, the selected voice initializes from it
 *    and any voice change is propagated back via `onVoiceChange`, enabling
 *    cross-card voice persistence in the parent (useSessionVoice).
 *  - Auto-generate: when `autoGenerate=true` and cardText is set, changing the
 *    voice triggers immediate preview generation.
 *
 * @param {string} lang         - language code for the card (e.g. 'de', 'en')
 * @param {string|null} sessionVoice  - voice value to start with (from session store)
 * @param {function|null} onVoiceChange - called with new voice when user changes selection
 * @param {boolean} autoGenerate - auto-generate preview when voice changes
 */
export const useVoicePicker = (
  lang = 'de',
  sessionVoice = null,
  onVoiceChange = null,
  autoGenerate = true,
) => {
  const rawCode = (lang || 'de').toLowerCase().trim().replace('_', '-');
  const code = rawCode.split('-')[0] || 'de';
  const adminSettings = useSettingsStore((s) => s.adminSettings);
  const defaultVoice = getTtsVoiceForLang(code, adminSettings);

  const voices = VOICES_BY_LANG[code] || [];

  const [selectedVoice, setSelectedVoiceState] = useState(sessionVoice || null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [wordBoundaries, setWordBoundaries] = useState(null); // for karaoke
  const [isGenerating, setIsGenerating] = useState(false);
  const [generateError, setGenerateError] = useState(null);

  // In-memory cache: Map<`${voice}|${text}`, { url, boundaries }>
  const cacheRef = useRef(new Map());
  // Ref to pending cardText for auto-generate
  const cardTextRef = useRef('');

  const isDefaultVoice = selectedVoice === defaultVoice;

  // Sync with session voice when it changes from outside (e.g. deck changed)
  useEffect(() => {
    if (sessionVoice !== selectedVoice) {
      setSelectedVoiceState(sessionVoice || null);
      setPreviewUrl(null);
      setWordBoundaries(null);
    }
  }, [sessionVoice, selectedVoice]);

  const generatePreview = useCallback(async (text, voiceOverride = null) => {
    const voice = voiceOverride || selectedVoice || defaultVoice;
    if (!text?.trim() || !voice) return null;

    const cacheKey = `${voice}|${text.trim()}`;
    const cached = cacheRef.current.get(cacheKey);
    if (cached) {
      setPreviewUrl(cached.url);
      setWordBoundaries(cached.boundaries || null);
      return cached.url;
    }

    setIsGenerating(true);
    setGenerateError(null);

    try {
      const res = await api.post('/media/generate-audio', {
        text: text.trim(),
        lang: code,
        voice,
        with_boundaries: true, // signals backend to include word timing
      });

      const url = res.data?.url;
      const boundaries = res.data?.word_boundaries || null;

      if (url) {
        cacheRef.current.set(cacheKey, { url, boundaries });
        setPreviewUrl(url);
        setWordBoundaries(boundaries);
      }
      return url || null;
    } catch (err) {
      const msg = err?.response?.data?.detail || err.message || tr("Ошибка генерации");
      setGenerateError(msg);
      console.error('[useVoicePicker] generate failed:', msg);
      return null;
    } finally {
      setIsGenerating(false);
    }
  }, [code, selectedVoice, defaultVoice]);

  // Change voice, propagate to session store, and auto-generate if enabled
  const setSelectedVoice = useCallback(async (voiceValue) => {
    setSelectedVoiceState(voiceValue);
    setPreviewUrl(null);
    setWordBoundaries(null);
    onVoiceChange?.(voiceValue);

    if (autoGenerate && cardTextRef.current) {
      // Give React a tick to commit the voice state before generating
      setTimeout(() => {
        generatePreview(cardTextRef.current, voiceValue);
      }, 0);
    }
  }, [autoGenerate, generatePreview, onVoiceChange]);

  // Update the text ref whenever cardText changes (used by auto-generate)
  const setCardText = useCallback((text) => {
    cardTextRef.current = text;
  }, []);

  const resetToDefault = useCallback(() => {
    setSelectedVoiceState(sessionVoice || defaultVoice);
    setPreviewUrl(null);
    setWordBoundaries(null);
    setGenerateError(null);
    cardTextRef.current = '';
  }, [defaultVoice, sessionVoice]);

  const generateAndSaveToCard = useCallback(async (cardId, text, isBack = false, voiceOverride = null) => {
    const voice = voiceOverride || selectedVoice || defaultVoice;
    if (!text?.trim() || !voice || !cardId) return null;

    setIsGenerating(true);
    setGenerateError(null);

    try {
      const res = await api.post('/media/generate-card-audio', {
        card_id: cardId,
        side: isBack ? 'back' : 'front',
        text: text.trim(),
        lang: code,
        voice,
        with_boundaries: true,
      });

      const url = res.data?.url;
      const path = res.data?.path;
      const boundaries = res.data?.word_boundaries || null;

      if (url && path) {
        const { useUiStore } = await import('../store/useUiStore');
        const { useSessionStore } = await import('../store/useSessionStore');
        const sessionState = useSessionStore.getState();
        const sessionCard = String(sessionState.card?.id) === String(cardId) ? sessionState.card : null;
        const currentPath = isBack ? sessionCard?.audio_back_path : sessionCard?.audio_path;
        const cleanP = (p) => (p || '').replace('/api/media/audio/', '').replace('audio/', '').trim();

        // Smart check: if card already uses this exact audio file, don't re-save
        if (sessionCard && currentPath && cleanP(currentPath) === cleanP(path)) {
          useUiStore.getState().showToast(tr("Этот голос уже используется для карточки"), 'info');
          setPreviewUrl(url);
          setWordBoundaries(boundaries);
          return url;
        }

        // Audio has its own sharing permission; do not update the whole card here.

        const cardPatch = isBack
          ? { audio_back_url: url, audio_back_path: path }
          : { audio_url: url, audio_path: path };

        // 1. Update session store: BOTH current card and all matching entries in studyHistory!
        if (sessionState.updateCardInSession) {
          sessionState.updateCardInSession(cardId, cardPatch);
        } else {
          if (sessionState.card && String(sessionState.card.id) === String(cardId)) {
            sessionState.setCard({ ...sessionState.card, ...cardPatch });
          }
          if (sessionState.studyHistory) {
            sessionState.setStudyHistory(
              sessionState.studyHistory.map(item =>
                item && String(item.id) === String(cardId) ? { ...item, ...cardPatch } : item
              )
            );
          }
        }

        // 2. Update deck cards list in useDeckStore
        const { useDeckStore } = await import('../store/useDeckStore');
        const updateCardLocal = useDeckStore.getState().updateCardLocal;
        if (updateCardLocal) {
          updateCardLocal(cardId, cardPatch);
        }

        try {
          const { useLidStore } = await import('../store/useLidStore');
          const updateQuestionAudio = useLidStore.getState().updateQuestionAudio;
          if (updateQuestionAudio) {
            updateQuestionAudio(cardId, { audio_path: path, audio_url: url });
          }
        } catch { /* ignore */ }

        try {
          const { db } = await import('../services/localDb');
          if (db?.cards) {
            await db.cards.update(cardId, isBack ? { audio_back_path: path } : { audio_path: path });
          }
        } catch { /* ignore */ }

        useUiStore.getState().showToast(tr("Озвучка обновлена"), 'success');
        setPreviewUrl(url);
        setWordBoundaries(boundaries);
      }
      return url || null;
    } catch (err) {
      const msg = err?.response?.data?.detail || err.message || tr("Ошибка генерации и сохранения");
      setGenerateError(msg);
      console.error('[useVoicePicker] generateAndSaveToCard failed:', msg);
      return null;
    } finally {
      setIsGenerating(false);
    }
  }, [code, selectedVoice, defaultVoice]);

  return {
    voices,
    selectedVoice,
    setSelectedVoice,
    isDefaultVoice,
    previewUrl,
    setPreviewUrl,
    wordBoundaries,
    generatePreview,
    generateAndSaveToCard,
    isGenerating,
    generateError,
    resetToDefault,
    setCardText,
  };
};
