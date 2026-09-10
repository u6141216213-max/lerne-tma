import { useEffect, useRef, useCallback } from 'react';
import api from '../services/api';
import { useDeckStore } from '../store/useDeckStore';
import { db, isOfflineMode } from '../services/localDb';

const POLL_INTERVAL_MS = 15000; // 15 seconds

/**
 * useCollaborativeSync
 * Polls the server every 15 seconds for changes made by other collaborators.
 * Pauses automatically when the app is in the background (Page Visibility API).
 * On resume, immediately checks for changes without waiting for the interval.
 */
export const useCollaborativeSync = () => {
  const lastSyncRef = useRef(null);
  const accessVersionRef = useRef(undefined);
  const intervalRef = useRef(null);
  const isRunningRef = useRef(false);

  const applyCollabChanges = useCallback(async (data) => {
    if (!data.has_changes) return;

    const store = useDeckStore.getState();
    const { decks, folders, deckCards, currentDeck } = store;

    let decksUpdated = false;
    let foldersUpdated = false;
    let cardsUpdated = false;

    // Persist incoming collaborative items to IndexedDB
    try {
      if (data.folders && data.folders.length > 0) {
        const foldersToPut = data.folders.map(f => ({
          id: f.id,
          name: f.name,
          is_deleted: f.is_deleted ? 1 : 0,
          is_pinned: f.is_pinned ? 1 : 0,
          position: f.position || 0,
          updated_at: f.updated_at,
          is_dirty: 0
        }));
        await db.folders.bulkPut(foldersToPut);
      }

      if (data.decks && data.decks.length > 0) {
        const decksToPut = data.decks.map(d => ({
          id: d.id,
          name: d.name,
          level: d.level,
          topic: d.topic,
          is_deleted: d.is_deleted ? 1 : 0,
          is_inbox: d.is_inbox ? 1 : 0,
          is_pinned: d.is_pinned ? 1 : 0,
          position: d.position || 0,
          folder_id: d.folder_id || null,
          updated_at: d.updated_at,
          is_dirty: 0
        }));
        await db.decks.bulkPut(decksToPut);
      }

      if (data.cards && data.cards.length > 0) {
        const cardsToPut = data.cards.map(c => ({
          id: c.id,
          deck_id: c.deck_id,
          front_text: c.front_text,
          back_text: c.back_text,
          context: c.context,
          image_path: c.image_path || null,
          audio_path: c.audio_path || null,
          audio_back_path: c.audio_back_path || null,
          video_front_path: c.video_front_path || null,
          video_back_path: c.video_back_path || null,
          is_deleted: c.is_deleted ? 1 : 0,
          flag: c.flag || 0,
          position: c.position || 0,
          updated_at: c.updated_at,
          is_dirty: 0
        }));
        await db.cards.bulkPut(cardsToPut);
      }
    } catch (dbErr) {
      console.warn('[CollabSync] Error persisting changes to Dexie DB:', dbErr);
    }

    // Apply deck changes to store
    let updatedDecks = [...decks];
    for (const incoming of (data.decks || [])) {
      const idx = updatedDecks.findIndex(d => String(d.id) === String(incoming.id));
      if (idx !== -1) {
        updatedDecks[idx] = { ...updatedDecks[idx], ...incoming };
        decksUpdated = true;
      } else if (!incoming.is_deleted) {
        updatedDecks.push(incoming);
        decksUpdated = true;
      }
    }

    // Apply folder changes to store
    let updatedFolders = [...folders];
    for (const incoming of (data.folders || [])) {
      const idx = updatedFolders.findIndex(f => String(f.id) === String(incoming.id));
      if (idx !== -1) {
        updatedFolders[idx] = { ...updatedFolders[idx], ...incoming };
        foldersUpdated = true;
      } else if (!incoming.is_deleted) {
        updatedFolders.push(incoming);
        foldersUpdated = true;
      }
    }

    // Apply card changes to store — only for currently viewed deck
    let updatedCards = [...(deckCards || [])];
    for (const incoming of (data.cards || [])) {
      if (currentDeck && String(incoming.deck_id) !== String(currentDeck.id)) continue;

      if (incoming.is_deleted) {
        const idx = updatedCards.findIndex(c => String(c.id) === String(incoming.id));
        if (idx !== -1) { updatedCards.splice(idx, 1); cardsUpdated = true; }
      } else {
        const idx = updatedCards.findIndex(c => String(c.id) === String(incoming.id));
        if (idx !== -1) {
          updatedCards[idx] = { ...updatedCards[idx], ...incoming };
          cardsUpdated = true;
        } else {
          updatedCards.push(incoming);
          cardsUpdated = true;
        }
      }
    }

    const nextState = {};
    if (decksUpdated) nextState.decks = updatedDecks;
    if (foldersUpdated) nextState.folders = updatedFolders;
    if (cardsUpdated) nextState.deckCards = updatedCards;

    if (Object.keys(nextState).length > 0) {
      useDeckStore.setState(nextState);
      console.log('[CollabSync] Applied changes:', {
        decks: (data.decks || []).length,
        folders: (data.folders || []).length,
        cards: (data.cards || []).length,
      });
    }
  }, []);

  const poll = useCallback(async () => {
    if (isRunningRef.current || isOfflineMode()) return;
    isRunningRef.current = true;
    try {
      const params = lastSyncRef.current ? { since: lastSyncRef.current } : {};
      const res = await api.get('/sync/collab-pull', { params });
      if (res.data) {
        if (res.data.server_time) lastSyncRef.current = res.data.server_time;
        if (res.data.access_version !== accessVersionRef.current) {
          const hadVersion = accessVersionRef.current !== undefined;
          accessVersionRef.current = res.data.access_version;
          // Grants do not change folder/deck timestamps, so refresh the index.
          if (hadVersion) {
            const store = useDeckStore.getState();
            await Promise.all([store.fetchDecks(true), store.fetchFolders()]);
          }
        }
        applyCollabChanges(res.data);
      }
    } catch (err) {
      console.debug('[CollabSync] Poll error (will retry):', err?.message);
    } finally {
      isRunningRef.current = false;
    }
  }, [applyCollabChanges]);

  const startPolling = useCallback(() => {
    if (intervalRef.current) return;
    intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);
  }, [poll]);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    lastSyncRef.current = new Date().toISOString();
    startPolling();

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        startPolling();
        poll(); // Immediate check on resume
      } else {
        stopPolling();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      stopPolling();
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [startPolling, stopPolling, poll]);
};
