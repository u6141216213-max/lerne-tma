import { tr } from '../i18n/locale';
import { create } from 'zustand';
import api from '../services/api';
import { db } from '../services/localDb';
import { transformCardToExamQuestion } from '../utils/lidCardAdapter';
import { getBundeslandByCode } from '../data/bundeslaender';
import { useUiStore } from './useUiStore';
import { pickRandom } from '../utils/shuffle';

const STORAGE_LAND_KEY = 'lerne_lid_selected_land';
const STORAGE_REMEMBER_KEY = 'lerne_lid_remember_land';

export const useLidStore = create((set, get) => ({
  // Land selection
  selectedLandCode: localStorage.getItem(STORAGE_LAND_KEY) || null,
  rememberLandChoice: localStorage.getItem(STORAGE_REMEMBER_KEY) === 'true',
  isLandModalOpen: false,
  isLandChangeMode: false,
  pendingExamMode: null, // 'exam' | 'practice' | null
  setPendingExamMode: (mode) => set({ pendingExamMode: mode }),

  // Exam flow
  examMode: 'exam', // 'exam' | 'practice'
  screen: 'menu', // 'menu' | 'running' | 'results'
  isLoadingTicket: false,
  currentQuestionIndex: 0,
  questions: [],
  answers: {}, // { [questionId]: optionId ('a'|'b'|'c'|'d') }
  timeRemaining: 3600, // 60 minutes in seconds
  timeSpent: 0,
  isTimerActive: false,

  // Modal for inspecting specific mistake on results screen
  selectedMistakeCard: null,
  setSelectedMistakeCard: (card) => set({ selectedMistakeCard: card }),

  // Modal controls
  openLandModal: (isChangeMode = false) => {
    set({ isLandModalOpen: true, isLandChangeMode: isChangeMode });
  },

  closeLandModal: () => {
    set({ isLandModalOpen: false, isLandChangeMode: false, pendingExamMode: null });
  },

  selectLand: (code, remember = true) => {
    if (remember) {
      localStorage.setItem(STORAGE_LAND_KEY, code);
      localStorage.setItem(STORAGE_REMEMBER_KEY, 'true');
    } else {
      localStorage.removeItem(STORAGE_LAND_KEY);
      localStorage.setItem(STORAGE_REMEMBER_KEY, 'false');
    }
    const pending = get().pendingExamMode;
    set({
      selectedLandCode: code,
      rememberLandChoice: remember,
      isLandModalOpen: false,
      isLandChangeMode: false,
      pendingExamMode: null
    });
    if (pending) {
      get().startSimulation(pending, code);
    }
  },

  // Generates 33 random questions: 10 Block 1, 10 Block 2, 10 Block 3, 3 Bundesland
  // Strategy:
  // 1. Fetch from server endpoint /lid/ticket (with automatic master decks fallback)
  // 2. Local Dexie DB fallback (when offline with synced cards)
  generateExamTicket: async (stateCode) => {
    const targetState = (stateCode || get().selectedLandCode || 'BY').toUpperCase();

    // 1. Try fetching from fast server endpoint
    try {
      const res = await api.get('/lid/ticket', { 
        params: { state_code: targetState },
        timeout: 5000 
      });
      if (res.data?.cards && res.data.cards.length >= 33) {
        return res.data.cards.map((c, idx) => transformCardToExamQuestion(c, idx + 1));
      }
    } catch (err) {
      console.warn('Could not fetch LiD ticket from API, falling back to local data:', err);
    }

    // 2. Fallback: Local Dexie DB (if user has cards cached)
    try {
      const stateInfo = getBundeslandByCode(targetState);
      const stateName = stateInfo?.nameDe || 'Bayern';

      const allDecks = await db.decks.toArray();
      const b1Deck = allDecks.find(d => d.name && (d.name.startsWith('1.') || d.name.toLowerCase().includes('politik')));
      const b2Deck = allDecks.find(d => d.name && (d.name.startsWith('2.') || d.name.toLowerCase().includes('geschichte')));
      const b3Deck = allDecks.find(d => d.name && (d.name.startsWith('3.') || d.name.toLowerCase().includes('mensch')));
      const stDeck = allDecks.find(d => d.name && d.name.toLowerCase() === stateName.toLowerCase());

      const getDeckCards = async (deck) => {
        if (!deck) return [];
        return await db.cards.where('deck_id').equals(deck.id).and(c => !c.is_deleted).toArray();
      };

      const [c1, c2, c3, cs] = await Promise.all([
        getDeckCards(b1Deck),
        getDeckCards(b2Deck),
        getDeckCards(b3Deck),
        getDeckCards(stDeck)
      ]);

      if (c1.length >= 10 && c2.length >= 10 && c3.length >= 10 && cs.length >= 3) {
        const picked1 = pickRandom(c1, 10).map(c => ({ ...c, deck_name: b1Deck?.name }));
        const picked2 = pickRandom(c2, 10).map(c => ({ ...c, deck_name: b2Deck?.name }));
        const picked3 = pickRandom(c3, 10).map(c => ({ ...c, deck_name: b3Deck?.name }));
        const pickedState = pickRandom(cs, 3).map(c => ({ ...c, deck_name: stDeck?.name }));

        const combined = [...picked1, ...picked2, ...picked3, ...pickedState];
        return combined.map((c, idx) => transformCardToExamQuestion(c, idx + 1));
      }
    } catch (localErr) {
      console.warn('Local Dexie ticket query failed:', localErr);
    }

    return [];
  },

  // Start Exam or Practice Mode
  startSimulation: async (mode = 'exam', customLand = null) => {
    const targetLand = customLand || get().selectedLandCode;
    if (!targetLand) {
      set({ isLandModalOpen: true, isLandChangeMode: true, pendingExamMode: mode });
      return;
    }

    set({ isLoadingTicket: true });
    try {
      const ticket = await get().generateExamTicket(targetLand);

      if (!ticket || ticket.length === 0) {
        console.error('Failed to generate LiD ticket: empty result');
        useUiStore.getState().showToast(tr("Не удалось загрузить вопросы экзамена. Попробуйте еще раз."), 'error');
        set({ isLoadingTicket: false });
        return;
      }

      set({
        examMode: mode,
        screen: 'running',
        questions: ticket,
        answers: {},
        currentQuestionIndex: 0,
        timeRemaining: 3600,
        timeSpent: 0,
        isTimerActive: true,
        selectedMistakeCard: null,
        pendingExamMode: null,
        isLoadingTicket: false
      });
    } catch (e) {
      console.error('Error starting simulation:', e);
      useUiStore.getState().showToast(tr("Произошла ошибка при составлении билета"), 'error');
      set({ isLoadingTicket: false });
    }
  },

  // Retake only the missed questions in practice mode
  retakeMistakes: () => {
    const { questions, answers } = get();
    const mistakes = questions.filter(q => {
      const ans = answers[q.id];
      return ans !== q.correctOption;
    });

    if (mistakes.length === 0) return;

    set({
      examMode: 'practice',
      screen: 'running',
      questions: mistakes.map((q, idx) => ({ ...q, examIndex: idx + 1 })),
      answers: {},
      currentQuestionIndex: 0,
      timeRemaining: 3600,
      timeSpent: 0,
      isTimerActive: true,
      selectedMistakeCard: null
    });
  },

  // Navigation & Answers
  goToQuestion: (index) => {
    const { questions } = get();
    if (index >= 0 && index < questions.length) {
      set({ currentQuestionIndex: index });
    }
  },

  nextQuestion: () => {
    const { currentQuestionIndex, questions } = get();
    if (currentQuestionIndex < questions.length - 1) {
      set({ currentQuestionIndex: currentQuestionIndex + 1 });
    }
  },

  prevQuestion: () => {
    const { currentQuestionIndex } = get();
    if (currentQuestionIndex > 0) {
      set({ currentQuestionIndex: currentQuestionIndex - 1 });
    }
  },

  setAnswer: (questionId, optionId) => {
    set((state) => ({
      answers: {
        ...state.answers,
        [questionId]: optionId
      }
    }));
  },

  updateQuestionAudio: (questionId, { audio_path, audio_url }) => {
    set((state) => ({
      questions: state.questions.map((q) => {
        if (q.id === questionId) {
          const resolvedUrl = audio_url || `/api/media/audio/${audio_path}`;
          return {
            ...q,
            audio_path,
            audioUrl: resolvedUrl,
            rawCard: q.rawCard ? { ...q.rawCard, audio_path, audio_url: resolvedUrl } : q.rawCard
          };
        }
        return q;
      })
    }));
  },

  tickTimer: () => {
    const { timeRemaining, isTimerActive, finishSimulation } = get();
    if (!isTimerActive) return;

    if (timeRemaining <= 1) {
      finishSimulation();
    } else {
      set((state) => ({
        timeRemaining: state.timeRemaining - 1,
        timeSpent: state.timeSpent + 1
      }));
    }
  },

  finishSimulation: () => {
    set({
      screen: 'results',
      isTimerActive: false
    });
  },

  resetToMenu: () => {
    set({
      screen: 'menu',
      isTimerActive: false,
      questions: [],
      answers: {},
      selectedMistakeCard: null
    });
  },

  // Getters & computations
  getResults: () => {
    const { questions, answers, timeSpent } = get();
    let correctCount = 0;
    const mistakes = [];
    const correctList = [];

    questions.forEach((q) => {
      const userAns = answers[q.id];
      const isCorrect = userAns === q.correctOption;
      if (isCorrect) {
        correctCount++;
        correctList.push(q);
      } else {
        mistakes.push({
          question: q,
          userAnswer: userAns || null,
          correctOption: q.correctOption
        });
      }
    });

    const isPassed = correctCount >= 17;
    const totalQuestions = questions.length;
    const scorePercent = totalQuestions > 0 ? Math.round((correctCount / totalQuestions) * 100) : 0;

    return {
      score: correctCount,
      total: totalQuestions,
      percent: scorePercent,
      isPassed,
      timeSpent,
      mistakes,
      correctList
    };
  }
}));
