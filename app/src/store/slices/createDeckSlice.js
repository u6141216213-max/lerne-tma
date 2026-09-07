import api from '../../services/api';
import { storage } from '../../utils/auth';
import { getInitialCachedData, saveToInitCache, sortFolders, sortDecks } from './initCacheHelper';

let reorderTimeout = null;
let cardReorderTimeout = null;
let pendingFetchCardsPromise = null;
let pendingFetchCardsDeckId = null;

export const createDeckSlice = (set, get) => ({
  decks: getInitialCachedData().decks,
  currentDeck: null,
  deckCards: [],
  cardsByDeck: {},
  duplicateCards: [],
  lastDuplicateCardId: null,
  syncModalOpen: false,
  deckToSync: null,
  cardsLoading: false,
  isFetchingDecks: false,

  setDecks: (decks) => {
    const sortedDecks = sortDecks(decks);
    set({ decks: sortedDecks });
    saveToInitCache({ decks: sortedDecks });
  },

  setDecksAndFolders: (decks, folders) => {
    const state = get();
    const sortedFolders = sortFolders(folders);
    const sortedDecks = sortDecks(decks);
    const updatedCurrentDeck = state.currentDeck 
      ? sortedDecks.find(d => d.id === state.currentDeck.id) || state.currentDeck 
      : null;
    set({ 
      decks: sortedDecks, 
      folders: sortedFolders, 
      currentDeck: updatedCurrentDeck,
      isFetchingDecks: false 
    });
    saveToInitCache({ decks: sortedDecks, folders: sortedFolders });
  },

  setCurrentDeck: (deck) => {
    const prevDeck = get().currentDeck;
    if (deck?.id) {
      storage.set('lerne_current_deck_id', String(deck.id));
    }
    const cached = deck?.id ? get().cardsByDeck[deck.id] : null;
    if (cached && cached.length > 0) {
      set({ currentDeck: deck, deckCards: cached, cardsLoading: false });
    } else if (!prevDeck || prevDeck.id !== deck?.id) {
      set({ currentDeck: deck, deckCards: [], cardsLoading: true });
    } else {
      set({ currentDeck: deck });
    }
  },

  setDeckCards: (cards) => {
    const currentId = get().currentDeck?.id;
    set((prev) => ({
      deckCards: cards,
      cardsByDeck: currentId ? { ...prev.cardsByDeck, [currentId]: cards } : prev.cardsByDeck
    }));
  },
  updateCardLocal: (cardId, fields) => {
    const { deckCards, duplicateCards, currentDeck, cardsByDeck } = get();
    const updated = (deckCards || []).map(c => String(c.id) === String(cardId) ? { ...c, ...fields } : c);
    const updatedDuplicates = (duplicateCards || []).map(c => String(c.id) === String(cardId) ? { ...c, ...fields } : c);
    const currentId = currentDeck?.id;
    set({
      deckCards: updated,
      duplicateCards: updatedDuplicates,
      cardsByDeck: currentId ? { ...cardsByDeck, [currentId]: updated } : cardsByDeck
    });
  },
  setDuplicateCards: (cards) => set({ duplicateCards: cards }),
  setLastDuplicateCardId: (id) => set({ lastDuplicateCardId: id }),
  setSyncModalOpen: (isOpen) => set({ syncModalOpen: isOpen }),
  setDeckToSync: (deck) => set({ deckToSync: deck }),

  fetchDuplicates: async () => {
    try {
      const res = await api.get('/cards/duplicates');
      set({ duplicateCards: res.data });
    } catch (err) {
      console.error('Fetch Duplicates Error:', err);
    }
  },

  fetchDecks: async (force = false, attempts = 3) => {
    const { decks } = get();
    if (!force && decks.length > 0) return;
    
    set({ isFetchingDecks: true });
    let lastError;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        if (force) {
          const [decksRes, foldersRes] = await Promise.all([
            api.get('/decks'),
            api.get('/folders')
          ]);
          const newDecks = sortDecks(decksRes.data || []);
          const sortedFolders = sortFolders(foldersRes.data || []);
          const state = get();
          const updatedCurrentDeck = state.currentDeck 
            ? newDecks.find(d => d.id === state.currentDeck.id) || state.currentDeck 
            : null;

          set({ 
            decks: newDecks, 
            folders: sortedFolders, 
            currentDeck: updatedCurrentDeck,
            isFetchingDecks: false 
          });
          saveToInitCache({ decks: newDecks, folders: sortedFolders });
        } else {
          const res = await api.get('/decks');
          const newDecks = sortDecks(res.data || []);
          const state = get();
          const updatedCurrentDeck = state.currentDeck 
            ? newDecks.find(d => d.id === state.currentDeck.id) || state.currentDeck 
            : null;
          set({ decks: newDecks, currentDeck: updatedCurrentDeck, isFetchingDecks: false });
          saveToInitCache({ decks: newDecks });
        }
        return;
      } catch (err) {
        lastError = err;
        const status = err?.response?.status || err?.status;
        if (status === 401 || status === 403) {
          break;
        }
        if (attempt < attempts) {
          await new Promise(r => setTimeout(r, 400 * attempt));
        }
      }
    }
    set({ isFetchingDecks: false });
    console.error('Fetch Decks Error:', lastError);
    throw lastError;
  },

  fetchDeckCards: async (deckId, attempts = 3, forceLoading = false) => {
    if (pendingFetchCardsDeckId === deckId && pendingFetchCardsPromise) {
      return pendingFetchCardsPromise;
    }

    const state = get();
    const isSameDeck = state.currentDeck?.id === deckId;
    const cached = state.cardsByDeck[deckId];
    const hasCards = (isSameDeck && state.deckCards && state.deckCards.length > 0) || (cached && cached.length > 0);
    
    if (forceLoading || (!hasCards && !isSameDeck)) {
      set({ cardsLoading: true });
    }

    const fetchPromise = (async () => {
      try {
        let lastError;
        for (let attempt = 1; attempt <= attempts; attempt++) {
          try {
            const endpoint = `/decks/${deckId}/cards`;
            const res = await api.get(endpoint);
            const newCards = res.data || [];
            set((prev) => ({
              deckCards: prev.currentDeck?.id === deckId ? newCards : prev.deckCards,
              cardsByDeck: {
                ...prev.cardsByDeck,
                [deckId]: newCards
              }
            }));
            return newCards;
          } catch (err) {
            lastError = err;
            if (attempt < attempts) {
              await new Promise(r => setTimeout(r, 150 * attempt));
            }
          }
        }
        console.error('Fetch Deck Cards Error after retries:', lastError);
        throw lastError;
      } finally {
        if (pendingFetchCardsDeckId === deckId) {
          pendingFetchCardsPromise = null;
          pendingFetchCardsDeckId = null;
        }
        set({ cardsLoading: false });
      }
    })();

    pendingFetchCardsPromise = fetchPromise;
    pendingFetchCardsDeckId = deckId;

    return fetchPromise;
  },

  handleDeleteDeck: async (deckId) => {
    try {
      await api.delete(`/decks/${deckId}`);
      const { fetchDecks } = get();
      await fetchDecks(true);
    } catch (err) {
      console.error('Delete Deck Error:', err);
      throw err;
    }
  },

  handleSyncDeck: async (deckId, mode = 'merge') => {
    try {
      await api.post(`/decks/${deckId}/sync`, { mode });
      const { fetchDecks } = get();
      await fetchDecks(true);
    } catch (err) {
      console.error('Sync Deck Error:', err);
      throw err;
    }
  },

  handleResetProgress: async (deckId) => {
    try {
      await api.post(`/decks/${deckId}/reset`);
      const { fetchDecks } = get();
      await fetchDecks(true);
    } catch (err) {
      console.error('Reset Progress Error:', err);
      throw err;
    }
  },

  createDeck: async (name, folderId = null, targetLang = null, deckType = 'standard') => {
    try {
      const { useLanguageStore } = await import('../useLanguageStore');
      const targetLanguage = targetLang || useLanguageStore.getState().activeLanguage || 'de';
      await api.post('/decks', { name, folder_id: folderId, target_language: targetLanguage, deck_type: deckType });
      const { fetchDecks } = get();
      await fetchDecks(true);
    } catch (err) {
      console.error('Create Deck Error:', err);
      throw err;
    }
  },

  renameDeck: async (deckId, newName) => {
    try {
      await api.post(`/decks/${deckId}/rename`, { name: newName });
      const { currentDeck, decks } = get();
      const updatedDecks = (decks || []).map(d => d.id === deckId ? { ...d, name: newName } : d);
      set({ decks: updatedDecks });
      saveToInitCache({ decks: updatedDecks });
      if (currentDeck && currentDeck.id === deckId) {
        set({ currentDeck: { ...currentDeck, name: newName } });
      }
      get().fetchDecks(true).catch(() => {});
    } catch (err) {
      console.error('Rename Deck Error:', err);
      throw err;
    }
  },

  toggleDeckLearning: async (deckId, explicitStatus = null) => {
    const { decks, currentDeck } = get();
    const targetDeck = decks.find(d => d.id === deckId);
    if (!targetDeck) return;
    
    const nextStatus = explicitStatus !== null ? explicitStatus : !targetDeck.is_learning;
    
    // Optimistic update
    const updatedDecks = decks.map(d => {
      if (d.id === deckId) {
        return {
          ...d,
          is_learning: nextStatus,
          metadata: { ...(d.metadata || {}), is_learning: nextStatus }
        };
      }
      return d;
    });
    
    set({ decks: updatedDecks });
    saveToInitCache({ decks: updatedDecks });
    
    if (currentDeck && currentDeck.id === deckId) {
      set({ 
        currentDeck: { 
          ...currentDeck, 
          is_learning: nextStatus,
          metadata: { ...(currentDeck.metadata || {}), is_learning: nextStatus }
        } 
      });
    }

    try {
      await api.post(`/decks/${deckId}/toggle-learning`, { is_learning: nextStatus });
    } catch (err) {
      console.error('Toggle Deck Learning Error:', err);
      // Revert on failure
      const revertedDecks = get().decks.map(d => d.id === deckId ? targetDeck : d);
      set({ decks: revertedDecks });
      saveToInitCache({ decks: revertedDecks });
      throw err;
    }
  },
  
  updateDeckMetadata: async (deckId, metadata) => {
    try {
      const res = await api.post(`/decks/${deckId}/metadata`, metadata);
      const { fetchDecks, currentDeck, decks } = get();
      const updatedMeta = res.data.metadata;
      const updatedDecks = (decks || []).map(d => d.id === deckId ? { ...d, metadata: updatedMeta } : d);
      set({ decks: updatedDecks });
      saveToInitCache({ decks: updatedDecks });
      if (currentDeck && currentDeck.id === deckId) {
        set({ currentDeck: { ...currentDeck, metadata: updatedMeta } });
      }
      await fetchDecks(true);
      return updatedMeta;
    } catch (err) {
      console.error('Update Deck Metadata Error:', err);
      throw err;
    }
  },

  handleFileUpload: async (event, callback) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      const formData = new FormData();
      formData.append('file', file);
      await api.post('/decks/import-json', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      const { fetchDecks } = get();
      await fetchDecks(true);
      if (callback) callback();
    } catch (err) {
      console.error('Upload JSON Error:', err);
      throw err;
    }
  },

  moveDeckToFolder: async (deckId, folderId) => {
    try {
      await api.post(`/decks/${deckId}/move`, { folder_id: folderId });
      const { currentDeck, decks } = get();
      const updatedDecks = (decks || []).map(d => d.id === deckId ? { ...d, folder_id: folderId } : d);
      set({ decks: updatedDecks });
      saveToInitCache({ decks: updatedDecks });
      if (currentDeck && currentDeck.id === deckId) {
        set({ currentDeck: { ...currentDeck, folder_id: folderId } });
      }
      get().fetchDecks(true).catch(() => {});
    } catch (err) {
      console.error('Move Deck to Folder Error:', err);
      throw err;
    }
  },

  copyDeckToFolder: async (deckId, folderId) => {
    try {
      await api.post(`/decks/${deckId}/copy`, { folder_id: folderId });
      const { fetchDecks } = get();
      await fetchDecks(true);
    } catch (err) {
      console.error('Copy Deck to Folder Error:', err);
      throw err;
    }
  },

  togglePinDeck: async (deckId) => {
    const { decks } = get();
    // Optimistic update
    const updated = decks.map(d =>
      d.id === deckId ? { ...d, is_pinned: !d.is_pinned } : d
    );
    const sorted = [...updated].sort((a, b) => {
      const aPinned = a.is_pinned ? 1 : 0;
      const bPinned = b.is_pinned ? 1 : 0;
      if (aPinned !== bPinned) return bPinned - aPinned;
      const aInbox = a.is_inbox ? 1 : 0;
      const bInbox = b.is_inbox ? 1 : 0;
      if (aInbox !== bInbox) return bInbox - aInbox;
      const aPos = a.position ?? 0;
      const bPos = b.position ?? 0;
      if (aPos !== bPos) return aPos - bPos;
      return b.id - a.id;
    });
    set({ decks: sorted });

    try {
      const res = await api.post(`/decks/${deckId}/pin`);
      if (res.data.status === 'success') {
        const serverDecks = await api.get('/decks');
        set({ decks: serverDecks.data });
      }
    } catch (err) {
      console.error('Toggle Pin Deck Error:', err);
      const serverDecks = await api.get('/decks');
      set({ decks: serverDecks.data });
      throw err;
    }
  },

  reorderDecks: async (orderedIds) => {
    const { decks } = get();
    if (!orderedIds || orderedIds.length === 0) return;

    const posMap = new Map();
    orderedIds.forEach((id, idx) => posMap.set(id, idx));

    const updated = [...decks];
    const reorderedItems = [];
    const positions = [];

    updated.forEach((d, idx) => {
      if (posMap.has(d.id)) {
        reorderedItems.push(d);
        positions.push(idx);
      }
    });

    reorderedItems.sort((a, b) => posMap.get(a.id) - posMap.get(b.id));

    positions.forEach((pos, i) => {
      const item = reorderedItems[i];
      updated[pos] = { ...item, position: posMap.get(item.id) };
    });

    set({ decks: updated });
    saveToInitCache({ decks: updated });

    if (reorderTimeout) {
      clearTimeout(reorderTimeout);
    }

    reorderTimeout = setTimeout(async () => {
      try {
        await api.post('/decks/reorder', { deck_ids: orderedIds });
      } catch (err) {
        console.error('Reorder Decks Error:', err);
      }
    }, 400);
  },

  reorderCards: async (orderedIds) => {
    const { deckCards } = get();
    if (!orderedIds || orderedIds.length === 0) return;

    const posMap = new Map();
    orderedIds.forEach((id, idx) => posMap.set(id, idx));

    const updated = [...deckCards];
    const reorderedItems = [];
    const positions = [];

    updated.forEach((c, idx) => {
      if (posMap.has(c.id)) {
        reorderedItems.push(c);
        positions.push(idx);
      }
    });

    reorderedItems.sort((a, b) => posMap.get(a.id) - posMap.get(b.id));

    positions.forEach((pos, i) => {
      const item = reorderedItems[i];
      updated[pos] = { ...item, position: posMap.get(item.id) };
    });

    const currentId = get().currentDeck?.id;
    set((prev) => ({
      deckCards: updated,
      cardsByDeck: currentId ? { ...prev.cardsByDeck, [currentId]: updated } : prev.cardsByDeck
    }));

    if (cardReorderTimeout) {
      clearTimeout(cardReorderTimeout);
    }

    cardReorderTimeout = setTimeout(async () => {
      try {
        await api.post('/cards/reorder', { card_ids: orderedIds });
      } catch (err) {
        console.error('Reorder Cards Error:', err);
      }
    }, 400);
  },
});
