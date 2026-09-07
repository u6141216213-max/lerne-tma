import { create } from 'zustand';
import { createDeckSlice } from './slices/createDeckSlice';
import { createFolderSlice } from './slices/createFolderSlice';
import { createTrashSlice } from './slices/createTrashSlice';
import { createShareSlice } from './slices/createShareSlice';
import { createLibrarySlice } from './slices/createLibrarySlice';

import { storage } from '../utils/auth';

/**
 * Main Deck Store assembled from modular domain slices.
 * - createDeckSlice: Decks & Cards CRUD, SRS progress & ordering
 * - createFolderSlice: Folder hierarchy, colors & ordering
 * - createTrashSlice: Trash retrieval, recovery & cleanup
 * - createShareSlice: Public sharing links & collaboration
 * - createLibrarySlice: Template categories & public deck imports
 */
export const useDeckStore = create((set, get, store) => ({
  ...createDeckSlice(set, get, store),
  ...createFolderSlice(set, get, store),
  ...createTrashSlice(set, get, store),
  ...createShareSlice(set, get, store),
  ...createLibrarySlice(set, get, store),
  resetDeckStore: () => {
    storage.remove('lerne_init_cache');
    storage.remove('lerne_current_deck_id');
    set({
      decks: [],
      folders: [],
      currentDeck: null,
      deckCards: [],
      cardsByDeck: {},
      duplicateCards: [],
      lastDuplicateCardId: null,
      syncModalOpen: false,
      deckToSync: null,
      cardsLoading: false,
      isFetchingDecks: false,
      trashItems: { decks: [], cards: [] },
      externalDecks: [],
      communityDecks: [],
    });
  },
}));
