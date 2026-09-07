import { storage, getUserId } from '../../utils/auth';

export const getInitialCachedData = () => {
  try {
    const raw = storage.get('lerne_init_cache');
    if (raw) {
      const data = JSON.parse(raw);
      const currentUserId = getUserId();
      if (data.user_id && currentUserId && String(data.user_id) !== String(currentUserId)) {
        return { decks: [], folders: [] };
      }
      return {
        decks: Array.isArray(data.decks) ? data.decks : [],
        folders: Array.isArray(data.folders) ? data.folders : []
      };
    }
  } catch { /* ignore */ }
  return { decks: [], folders: [] };
};

export const saveToInitCache = (partialData) => {
  try {
    const raw = storage.get('lerne_init_cache');
    const existing = raw ? JSON.parse(raw) : {};
    const currentUserId = getUserId();
    const merged = { ...existing, ...partialData, ...(currentUserId ? { user_id: currentUserId } : {}) };
    storage.set('lerne_init_cache', JSON.stringify(merged));
  } catch { /* ignore */ }
};

export const sortFolders = (rawFolders) => {
  if (!rawFolders || !Array.isArray(rawFolders)) return [];
  const storedOrderStr = localStorage.getItem('lerne_folder_order');
  let storedOrder = null;
  try {
    storedOrder = storedOrderStr ? JSON.parse(storedOrderStr) : null;
  } catch { /* ignore */ }
  const sorted = [...rawFolders];
  sorted.sort((a, b) => {
    if (storedOrder && Array.isArray(storedOrder)) {
      const idxA = storedOrder.indexOf(a.id);
      const idxB = storedOrder.indexOf(b.id);
      if (idxA !== -1 && idxB !== -1) return idxA - idxB;
      if (idxA !== -1) return -1;
      if (idxB !== -1) return 1;
    }
    const aPos = a.position ?? 0;
    const bPos = b.position ?? 0;
    if (aPos !== bPos) return aPos - bPos;
    return (a.id || 0) - (b.id || 0);
  });
  return sorted;
};

export const sortDecks = (rawDecks) => {
  if (!rawDecks || !Array.isArray(rawDecks)) return [];
  const sorted = [...rawDecks];
  sorted.sort((a, b) => {
    const aPinned = a.is_pinned ? 1 : 0;
    const bPinned = b.is_pinned ? 1 : 0;
    if (aPinned !== bPinned) return bPinned - aPinned;

    const aInbox = a.is_inbox ? 1 : 0;
    const bInbox = b.is_inbox ? 1 : 0;
    if (aInbox !== bInbox) return bInbox - aInbox;

    const aPos = a.position ?? 0;
    const bPos = b.position ?? 0;
    if (aPos !== bPos) return aPos - bPos;

    return (a.id || 0) - (b.id || 0);
  });
  return sorted;
};


