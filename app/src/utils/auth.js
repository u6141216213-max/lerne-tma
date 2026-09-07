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

export const getUserProfile = () => {
  try {
    const value = storage.get(PROFILE_KEY);
    const profile = value ? JSON.parse(value) : null;
    return profile?.user_id ? profile : null;
  } catch { return null; }
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
