import { tr } from '../i18n/locale';

const storage = {
  get: (key) => { try { return localStorage.getItem(key); } catch { return null; } },
  set: (key, value) => { try { localStorage.setItem(key, value); } catch { /* unavailable */ } },
  remove: (key) => { try { localStorage.removeItem(key); } catch { /* unavailable */ } },
};

const SESSION_KEY = 'lerne_auth_v2_session';
const PROFILE_KEY = 'lerne_user_profile';

let memorySession = null;

export const getAuthSession = () => {
  if (memorySession?.access_token && memorySession?.refresh_token) {
    return memorySession;
  }
  try {
    const value = storage.get(SESSION_KEY);
    const parsed = value ? JSON.parse(value) : null;
    if (parsed?.access_token && parsed?.refresh_token) {
      memorySession = parsed;
      return memorySession;
    }
    return null;
  } catch { return null; }
};

export const getAccessToken = () => getAuthSession()?.access_token || null;

export const saveAuthSession = (session) => {
  if (!session?.access_token || !session?.refresh_token) throw new Error('Invalid authentication session');
  memorySession = session;
  storage.set(SESSION_KEY, JSON.stringify(session));
};

export const clearAuthSession = () => {
  memorySession = null;
  storage.remove(SESSION_KEY);
  storage.remove(PROFILE_KEY);
  storage.remove('lerne_user_id');
  storage.remove('lerne_init_cache');
  storage.remove('lerne_last_sync_time');
  storage.remove('lerne_current_deck_id');
};

const FALLBACK_USER_ID = import.meta.env?.VITE_TMA_USER_ID_FALLBACK;
const LOCAL_HOST_PATTERNS = [
  /^localhost$/i,
  /^127\./,
  /^10\./,
  /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[0-1])\./
];

const isLocalHost = (hostname) => LOCAL_HOST_PATTERNS.some(pattern => pattern.test(hostname));

export const parseUserId = (value) => {
  const id = parseInt(value, 10);
  return Number.isNaN(id) || id <= 0 ? null : id;
};

export const getUserProfile = () => {
  try {
    const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
    const isResetRequested = params?.get('guest') === '1' || params?.get('reset') === '1';

    if (isResetRequested) {
      clearAuthSession();
    }

    // 1. Check URL (?user_id=123) for testing or switching accounts
    const urlIdStr = params?.get('user_id');
    if (urlIdStr) {
      const urlId = parseUserId(urlIdStr);
      if (urlId !== null) {
        const savedId = storage.get('lerne_user_id');
        if (savedId && String(savedId) !== String(urlId)) {
          storage.remove('lerne_init_cache');
          storage.remove('lerne_current_deck_id');
          storage.remove(SESSION_KEY);
          memorySession = null;
        }
        const profile = {
          user_id: urlId,
          first_name: params.get('first_name') || params.get('account') || null,
          last_name: params.get('last_name') || null,
          username: params.get('username') || null,
          photo_url: params.get('photo') || params.get('photo_url') || null,
          is_guest: false
        };
        storage.set('lerne_user_id', String(urlId));
        storage.set(PROFILE_KEY, JSON.stringify(profile));
        return profile;
      }
    }

    // 2. Telegram WebApp user (when running in Telegram Mini App)
    const tg = typeof window !== 'undefined' ? window.Telegram?.WebApp : null;
    if (!isResetRequested && tg?.initDataUnsafe?.user?.id) {
      const u = tg.initDataUnsafe.user;
      const tgId = parseUserId(u.id);
      if (tgId !== null) {
        const savedId = storage.get('lerne_user_id');
        if (savedId && String(savedId) !== String(tgId)) {
          storage.remove('lerne_init_cache');
          storage.remove('lerne_current_deck_id');
          storage.remove(SESSION_KEY);
          memorySession = null;
        }
        const profile = {
          user_id: tgId,
          first_name: u.first_name,
          last_name: u.last_name,
          username: u.username,
          photo_url: u.photo_url,
          is_guest: false
        };
        storage.set('lerne_user_id', String(tgId));
        storage.set(PROFILE_KEY, JSON.stringify(profile));
        return profile;
      }
    }

    // 3. Saved localStorage profile
    const value = storage.get(PROFILE_KEY);
    if (value) {
      const parsed = JSON.parse(value);
      if (parsed?.user_id) return parsed;
    }

    // 4. Saved lerne_user_id in localStorage
    const savedId = storage.get('lerne_user_id');
    if (savedId) {
      const id = parseUserId(savedId);
      if (id !== null) return { user_id: id, is_guest: true };
    }

    // 5. Localhost Vite dev fallback
    const fallbackId = parseUserId(FALLBACK_USER_ID) || 642478257;
    if (typeof window !== 'undefined' && import.meta.env?.DEV && isLocalHost(window.location.hostname) && !window.Capacitor) {
      const profile = { user_id: fallbackId, is_guest: false, first_name: 'Aruna Андрей', username: 'Aruna27' };
      storage.set('lerne_user_id', String(fallbackId));
      storage.set(PROFILE_KEY, JSON.stringify(profile));
      return profile;
    }

    return null;
  } catch {
    return null;
  }
};

export const getUserId = () => getUserProfile()?.user_id ?? null;

export const saveUserProfile = (profile) => {
  if (!profile?.user_id) return;
  storage.set(PROFILE_KEY, JSON.stringify({ ...profile, is_guest: false }));
  storage.set('lerne_user_id', String(profile.user_id));
};

export const resetUserSession = () => {
  clearAuthSession();
  window.location.assign(window.location.origin + window.location.pathname);
};

export const cloudStorage = {
  get: (key) => new Promise((resolve) => {
    try {
      const cloud = window.Telegram?.WebApp?.CloudStorage;
      cloud?.getItem ? cloud.getItem(key, (error, value) => resolve(error ? null : value)) : resolve(null);
    } catch { resolve(null); }
  }),
  set: (key, value) => new Promise((resolve) => {
    try {
      const cloud = window.Telegram?.WebApp?.CloudStorage;
      cloud?.setItem ? cloud.setItem(key, String(value), (error) => resolve(!error)) : resolve(false);
    } catch { resolve(false); }
  }),
};

export { storage, tr };
