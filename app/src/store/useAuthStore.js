import { create } from 'zustand';
import { tr } from '../i18n/locale';
import api from '../services/api';
import { getUserProfile, saveUserProfile, saveAuthSession, clearAuthSession, storage } from '../utils/auth';
import { openExternalLink } from '../utils/platform';
import { useUiStore } from './useUiStore';
import { useDeckStore } from './useDeckStore';

const PENDING_KEY = 'lerne_auth_v2_pending';
let pollTimer = null;
let listenersReady = false;
let exchangeInFlight = false;
const stopPolling = () => {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
};

const pendingAuth = () => {
  try { return JSON.parse(sessionStorage.getItem(PENDING_KEY) || 'null'); } catch { return null; }
};
const savePending = (value) => {
  try { sessionStorage.setItem(PENDING_KEY, JSON.stringify(value)); } catch { /* unavailable */ }
};
const clearPending = () => {
  try { sessionStorage.removeItem(PENDING_KEY); } catch { /* unavailable */ }
};
const authMessage = (error, fallback) => {
  const messages = {
    reauthentication_required: tr('Войдите заново через привязанный Telegram или Google и повторите в течение 5 минут.'),
    invalid_credentials: tr('Неверный email или пароль.'),
    identity_conflict: tr('Этот способ входа уже принадлежит другому аккаунту. Аккаунты не объединены.'),
    email_unavailable: tr('Этот email недоступен для регистрации. Попробуйте войти.'),
    too_many_attempts: tr('Слишком много попыток. Повторите через 15 минут.'),
  };
  return messages[error.response?.data?.detail] || fallback;
};

export const useAuthStore = create((set, get) => ({
  userProfile: getUserProfile(),
  isPolling: false,
  isVerifyingCode: false,
  pendingGuestId: null,
  authModalOpen: false,
  authModalTab: 'telegram',
  authError: null,
  isStarting: false,
  authMethods: null,
  passwordSettings: null,
  showTelegramPrompt: false,

  refreshAuthMethods: async () => {
    try {
      const [identities, settings] = await Promise.all([
        api.get('/auth/v2/identities'), api.get('/auth/v2/password-settings'),
      ]);
      set({ authMethods: identities.data.providers, passwordSettings: settings.data });
      return identities.data.providers;
    } catch {
      set({ authMethods: null, passwordSettings: null });
      return null;
    }
  },

  setAuthModalOpen: (isOpen, tab = 'telegram') => {
    set({ authModalOpen: isOpen, authModalTab: tab, authError: null });
    useUiStore.getState().setIsAuthModalOpen(isOpen, tr('Вход в аккаунт'));
  },
  setAuthModalTab: (tab) => set({ authModalTab: tab, authError: null }),
  setUserProfile: (profile) => {
    set({ userProfile: profile });
    if (profile) saveUserProfile(profile);
    useUiStore.setState({ userProfile: profile });
  },

  finishLogin: async (tokens, { recovery = false, suggestTelegram = false } = {}) => {
    saveAuthSession(tokens);
    const response = await api.get('/auth/v2/me');
    const profile = response.data;
    get().setUserProfile(profile);
    storage.remove('lerne_init_cache');
    storage.remove('lerne_last_sync_time');
    clearPending();
    stopPolling();
    const methods = await get().refreshAuthMethods();
    const prompt = suggestTelegram && methods && !methods.includes('telegram');
    set({ isPolling: false, authModalOpen: Boolean(prompt), showTelegramPrompt: Boolean(prompt), authError: null });
    useUiStore.getState().setIsAuthModalOpen(Boolean(prompt));
    if (recovery) useUiStore.getState().openSettings('profile');
    useUiStore.getState().showToast(tr('Вход выполнен. Ваши колоды и прогресс доступны.'), 'success');
    try {
      await useDeckStore.getState().fetchDecks(true);
      await useDeckStore.getState().fetchFolders();
    } catch { /* the authenticated UI may retry */ }
    return profile;
  },

  startProvider: async (provider, purpose = 'login', recovery = false) => {
    if (get().isStarting || get().isPolling) return { success: false };
    set({ isStarting: true, authError: null });
    try {
      const miniAppData = window.Telegram?.WebApp?.initData;
      if (provider === 'telegram' && purpose === 'login' && miniAppData && !recovery) {
        const response = await api.post('/auth/v2/telegram/mini-app', { init_data: miniAppData, purpose });
        await get().finishLogin(response.data);
        return { success: true };
      }
      const response = await api.post('/auth/v2/challenges', { provider, purpose });
      const pending = { id: response.data.challenge_id, secret: response.data.secret, provider, purpose,
        expiresAt: response.data.expires_at, recovery };
      savePending(pending);
      set({ isPolling: true, authError: null });
      get().beginPolling();
      openExternalLink(response.data.authorization_url);
      return { success: true };
    } catch (error) {
      const message = authMessage(error, tr('Не удалось начать вход. Попробуйте ещё раз.'));
      set({ isPolling: false, authError: message });
      useUiStore.getState().showToast(message, 'error');
      return { success: false, error: message };
    } finally {
      set({ isStarting: false });
    }
  },

  startTelegramLinking: () => get().startProvider('telegram'),
  startGoogleLogin: () => get().startProvider('google'),
  linkProvider: (provider) => get().startProvider(provider, 'link'),
  startPasswordRecovery: (provider = 'telegram') => get().startProvider(provider, 'login', true),
  cancelPendingAuth: () => {
    clearPending();
    stopPolling();
    set({ isPolling: false, authError: null });
  },

  loginWithEmailPassword: async (email, password, register = false) => {
    try {
      const path = register ? '/auth/v2/email-password/register' : '/auth/v2/email-password/login';
      const response = await api.post(path, { email, password });
      await get().finishLogin(response.data, { suggestTelegram: register });
      return { success: true };
    } catch (error) {
      const message = authMessage(error, tr('Не удалось выполнить вход по email.'));
      set({ authError: message });
      useUiStore.getState().showToast(message, 'error');
      return { success: false, error: message };
    }
  },
  linkEmailPassword: async (email, password) => {
    try {
      const reset = get().passwordSettings?.email;
      await api.post(reset ? '/auth/v2/email-password/set' : '/auth/v2/email-password/link', { email, password });
      await get().refreshAuthMethods();
      useUiStore.getState().showToast(tr('Email и пароль сохранены как способ входа.'), 'success');
      return { success: true };
    } catch (error) {
      const message = error.response?.data?.detail === 'reauthentication_required'
        ? tr('Войдите заново через привязанный Telegram или Google и повторите в течение 5 минут.')
        : tr('Не удалось сохранить пароль. Проверьте данные и подключение.');
      useUiStore.getState().showToast(message, 'error');
      return { success: false, error: message };
    }
  },

  checkPendingSession: async () => {
    if (exchangeInFlight) return false;
    const pending = pendingAuth();
    if (!pending?.id || !pending?.secret) { stopPolling(); return false; }
    if (Date.parse(pending.expiresAt || '') <= Date.now()) {
      clearPending();
      stopPolling();
      set({ isPolling: false, authError: tr('Время подтверждения истекло. Начните вход ещё раз.') });
      return false;
    }
    exchangeInFlight = true;
    try {
      const response = await api.post(`/auth/v2/challenges/${pending.id}/exchange`, { secret: pending.secret });
      if (pendingAuth()?.id !== pending.id) return false;
      if (pending.purpose === 'link') {
        clearPending();
        stopPolling();
        await get().refreshAuthMethods();
        set({ isPolling: false, authModalOpen: false, showTelegramPrompt: false });
        useUiStore.getState().setIsAuthModalOpen(false);
        useUiStore.getState().showToast(tr('Способ входа привязан.'), 'success');
      } else {
        await get().finishLogin(response.data, { recovery: pending.recovery });
      }
      return true;
    } catch (error) {
      // A challenge is expected to be unconfirmed while the app polls.
      if (pendingAuth()?.id !== pending.id) return false;
      const detail = error.response?.data?.detail;
      if (detail === 'invalid_challenge') return false;
      clearPending();
      stopPolling();
      set({ isPolling: false });
      const message = authMessage(error, tr('Не удалось завершить вход.'));
      set({ authError: message });
      useUiStore.getState().showToast(message, 'error');
      return false;
    } finally {
      exchangeInFlight = false;
    }
  },

  beginPolling: () => {
    stopPolling();
    pollTimer = setInterval(() => get().checkPendingSession(), 2000);
  },

  // Kept as a clear UI response for obsolete controls in older cached bundles.
  loginWithCode: async () => ({ success: false, error: tr('Вход по коду больше не поддерживается.') }),
  logout: () => {
    clearAuthSession();
    clearPending();
    stopPolling();
    set({ userProfile: null, isPolling: false, authMethods: null, passwordSettings: null, showTelegramPrompt: false });
    useUiStore.setState({ userProfile: null });
  },
  initListeners: () => {
    if (listenersReady || typeof window === 'undefined') return;
    listenersReady = true;
    const resume = () => {
      if (!document.hidden && pendingAuth()) {
        set({ isPolling: true });
        get().checkPendingSession();
        get().beginPolling();
      }
    };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('focus', resume);
  },
}));
