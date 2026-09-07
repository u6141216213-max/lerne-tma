import { tr } from '../i18n/locale';
import { getInterfaceLanguage, setInterfaceLanguage, normalizeInterfaceLanguage } from '../i18n/locale';
import { useEffect } from 'react';
import { getAccessToken, getUserId, getUserProfile, storage, cloudStorage } from '../utils/auth';
import api from '../services/api';
import { useUiStore } from '../store/useUiStore';
import { useAuthStore } from '../store/useAuthStore';
import { useDeckStore } from '../store/useDeckStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { DESIGN_PRESETS } from '../constants/appConstants';
import { isOfflineMode } from '../services/localDb';
import { syncService } from '../services/syncService';
import { remapOfflineUi, refreshOfflineUi } from '../services/offlineUi';

const SETTINGS_VERSION = '6';

export const useAppInitialization = (checkStartParam) => {
  const { setUserProfile, showToast } = useUiStore();
  const { setAdminSettings, setUserPrompts, applyDesignPreset } = useSettingsStore();

  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (tg) {
      tg.ready();
      console.log("Telegram WebApp Ready");
    }

    const init = async () => {
      const profile = getUserProfile();
      setUserProfile(profile);

      // A local profile or URL is never an authentication credential. Do not
      // request account data until a v2 bearer session exists.
      if (!getAccessToken()) {
        useUiStore.getState().setIsAuthModalOpen(true, tr('Войдите, чтобы открыть свои колоды и прогресс'));
        useUiStore.setState({ loading: false, hasInitialized: true });
        return;
      }

      // Instant UI restore from cache if available (synchronous)
      if (!isOfflineMode()) loadCachedInitData();

      // CloudStorage language restoration in background (non-blocking)
      (async () => {
        try {
          const [cloudHasSelected, cloudLang, cloudNativeSel, cloudNativeLang] = await Promise.all([
            cloudStorage.get('lerne_has_selected_language'),
            cloudStorage.get('lerne_target_language'),
            cloudStorage.get('lerne_native_language_selected'),
            cloudStorage.get('lerne_native_language')
          ]);
          if (cloudHasSelected === 'true' && cloudLang) {
            const { useLanguageStore } = await import('../store/useLanguageStore');
            useLanguageStore.getState().syncLanguageFromExternal(cloudLang, true);
          }
          if (!localStorage.getItem('native_language') && cloudNativeSel === 'true' && cloudNativeLang) {
            setInterfaceLanguage(cloudNativeLang);
            localStorage.setItem('native_language_selected', 'true');
          }
        } catch (e) {
          console.warn("CloudStorage language restore failed:", e);
        }
      })();

      // 1. Fetch fresh init data from backend immediately
      const initPromise = fetchInitData();

      // 2. Sync profile in parallel
      syncProfile(profile).catch(e => console.error("Profile sync error:", e));

      // 3. Perform background sync in offline mode if online
      if (isOfflineMode() && navigator.onLine) {
        syncService.sync().catch(e => console.error("Startup sync failed:", e));
      }

      await initPromise;
    };

    init();
    useAuthStore.getState().initListeners();

    // Check start param on mount
    checkStartParam();

    // Listen for visibility changes
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        console.log("App became visible, re-checking parameters and auth...");
        useAuthStore.getState().checkPendingSession();
        setTimeout(checkStartParam, 500);
        if (navigator.onLine && getAccessToken()) {
          if (isOfflineMode()) {
            syncService.sync().catch(e => console.error("Visibility sync failed:", e));
          } else {
            // Fresh reload of decks and folders when returning to Telegram Mini App
            useDeckStore.getState().fetchDecks();
            useDeckStore.getState().fetchFolders();
          }
        }
      }
    };

    // Listen for online event (reconnection)
    const handleOnline = () => {
      console.log("[Sync] Network online detected. Triggering auto-sync...");
      if (isOfflineMode()) {
        syncService.sync().catch(e => console.error("Online event sync failed:", e));
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('online', handleOnline);
    window.addEventListener('lerne:ids-remapped', remapOfflineUi);
    window.addEventListener('lerne:synced', refreshOfflineUi);

    // Periodic background sync every 60 seconds
    const syncInterval = setInterval(() => {
      if (isOfflineMode() && navigator.onLine) {
        syncService.sync().catch(e => console.error("Periodic sync failed:", e));
      }
    }, 60000);

    const USER_ID = getUserId();
    const params = new URLSearchParams(window.location.search);
    const adminIds = (import.meta.env.VITE_ADMIN_IDS || '642478257')
      .split(',')
      .map(id => Number(id.trim()))
      .filter(Boolean);
    if (params.get('admin') === '1' || (USER_ID && adminIds.includes(Number(USER_ID)))) {
      useSettingsStore.setState({ isAdmin: true });
    }

    const currentVersion = storage.get('lerne_settings_version');
    if (currentVersion !== SETTINGS_VERSION) {
      console.log(`Migrating settings to v${SETTINGS_VERSION}...`);
      const defaultSettings = DESIGN_PRESETS.find(p => p.id === 'lerne_2026')?.settings;
      if (defaultSettings) {
        applyDesignPreset({ name: 'Lerne 2026', settings: defaultSettings });
      }
      storage.set('lerne_settings_version', SETTINGS_VERSION);
    }

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('lerne:ids-remapped', remapOfflineUi);
      window.removeEventListener('lerne:synced', refreshOfflineUi);
      clearInterval(syncInterval);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const CACHE_VERSION = '3'; // bump to invalidate cached deck ids after cloud DB migration

  const loadCachedInitData = () => {
    try {
      const cacheVer = storage.get('lerne_init_cache_version');
      if (cacheVer !== CACHE_VERSION) {
        // Stale cache — wipe it so we always get fresh data from server
        storage.remove('lerne_init_cache');
        storage.set('lerne_init_cache_version', CACHE_VERSION);
        return;
      }
      const cachedRaw = storage.get('lerne_init_cache');
      if (cachedRaw) {
        const data = JSON.parse(cachedRaw);
        const { setDecksAndFolders, setCurrentDeck } = useDeckStore.getState();
        if ((data.decks && data.decks.length > 0) || (data.folders && data.folders.length > 0)) {
          setDecksAndFolders(data.decks || [], data.folders || []);
          const savedDeckId = storage.get('lerne_current_deck_id');
          if (savedDeckId && data.decks) {
            const cachedCurrent = data.decks.find(d => String(d.id) === String(savedDeckId));
            if (cachedCurrent) {
              setCurrentDeck(cachedCurrent);
            }
          }
        }
        if (data.settings) setAdminSettings(data.settings);
        if (data.prompts) setUserPrompts(data.prompts);

        if (data.user_info && data.user_info.user_id) {
          const current = useUiStore.getState().userProfile || {};
          const sUser = data.user_info;
          const validCachedName = sUser.first_name && sUser.first_name !== 'Пользователь' ? sUser.first_name : null;
          const validLocalName = current.first_name && current.first_name !== 'Пользователь' ? current.first_name : null;
          const resolvedName = validLocalName || validCachedName || sUser.username || current.username || null;
          const resolvedPhoto = current.photo_url || sUser.photo_url || null;

          const hasIdentifyingInfo = Boolean(resolvedName || sUser.username || current.username);
          const restoredProfile = {
            ...current,
            ...sUser,
            first_name: resolvedName,
            photo_url: resolvedPhoto,
            is_guest: hasIdentifyingInfo ? Boolean(sUser.is_guest) : true
          };
          setUserProfile(restoredProfile);
        }
      }
    } catch (e) {
      console.error("Failed to load cached init data:", e);
    }
  };

  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

  // ⚠️ CRITICAL NETWORK STABILITY GUARANTEE: DO NOT REMOVE OR BYPASS RETRY LOGIC (requestWithRetry).
  // Required to protect against Supabase cloud DB cold starts, TCP disconnects, and backend reboots!
  const requestWithRetry = async (requestFn, label, attempts = 3) => {
    let lastError;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        return await requestFn();
      } catch (err) {
        lastError = err;
        const status = err?.response?.status || err?.status;
        if (status === 401 || status === 403) {
          // Authentication errors should fail immediately without useless retries
          break;
        }
        if (attempt === attempts) break;
        console.warn(`${label} failed on attempt ${attempt}, retrying...`, err);
        await sleep(400 * attempt);
      }
    }
    throw lastError;
  };

  const fetchInitData = async () => {
    const languageAtStart = getInterfaceLanguage();
    const { setDecksAndFolders, fetchDuplicates } = useDeckStore.getState();
    const currentDecks = useDeckStore.getState().decks;
    const currentFolders = useDeckStore.getState().folders;
    if ((!currentDecks || currentDecks.length === 0) && (!currentFolders || currentFolders.length === 0)) {
      useUiStore.setState({ loading: true });
    }
    useDeckStore.setState({ isFetchingDecks: true });
    try {
      const res = await requestWithRetry(() => api.get('/init'), 'Init data load');
      const freshDecks = res.data.decks || [];
      const freshFolders = res.data.folders || [];
      setDecksAndFolders(freshDecks, freshFolders);
      setAdminSettings(res.data.settings);
      setUserPrompts(res.data.prompts);

      if (res.data.user_settings && Object.keys(res.data.user_settings).length > 0) {
        useSettingsStore.getState().syncUserSettingsFromServer(res.data.user_settings);
      } else {
        useSettingsStore.getState().saveCurrentSettingsToServer().catch(() => {});
      }

      if (res.data.user_info && res.data.user_info.user_id) {
        const sUser = res.data.user_info;
        const current = useUiStore.getState().userProfile || {};
        const validLocalName = current.first_name && current.first_name !== 'Пользователь' ? current.first_name : null;
        const validServerName = sUser.first_name && sUser.first_name !== 'Пользователь' ? sUser.first_name : null;
        const fallbackName = validServerName || validLocalName || sUser.username || current.username || null;

        const hasIdentifyingInfo = Boolean(fallbackName || sUser.username || current.username);
        const mergedProfile = {
          ...current,
          ...sUser,
          first_name: fallbackName,
          photo_url: sUser.photo_url || current.photo_url || null,
          is_guest: hasIdentifyingInfo ? Boolean(sUser.is_guest) : true
        };

        setUserProfile(mergedProfile);
        storage.set('lerne_user_profile', JSON.stringify(mergedProfile));
      }

      if (res.data.user_info?.has_selected_language || res.data.user_info?.active_language) {
        const { useLanguageStore } = await import('../store/useLanguageStore');
        useLanguageStore.getState().syncLanguageFromExternal(
          res.data.user_info.active_language || 'de',
          Boolean(res.data.user_info.has_selected_language)
        );
      }

      if (res.data.user_info?.native_language && getInterfaceLanguage() === languageAtStart) {
        setInterfaceLanguage(res.data.user_info.native_language);
        localStorage.setItem('native_language_selected', 'true');
        cloudStorage.set('lerne_native_language', normalizeInterfaceLanguage(res.data.user_info.native_language) || 'uk');
        cloudStorage.set('lerne_native_language_selected', 'true');
      }

      // Cache init response for instant future starts
      storage.set('lerne_init_cache', JSON.stringify(res.data));
      storage.set('lerne_init_cache_version', CACHE_VERSION);
      fetchDuplicates();

      // If user opened a deck or refreshed, re-sync currentDeck & cards for current deck
      const uiState = useUiStore.getState();
      const savedDeckId = storage.get('lerne_current_deck_id');
      const currDeck = useDeckStore.getState().currentDeck 
        || (savedDeckId ? freshDecks.find(d => String(d.id) === String(savedDeckId)) : null);

      if (currDeck) {
        const freshCurrentDeck = freshDecks.find(d => d.id === currDeck.id)
          || freshDecks.find(d => d.name === currDeck.name && d.stats?.total > 0)
          || freshDecks.find(d => d.name === currDeck.name)
          || currDeck;
        useDeckStore.getState().setCurrentDeck(freshCurrentDeck);
        if (uiState.view === 'cards') {
          useDeckStore.getState().fetchDeckCards(freshCurrentDeck.id);
        }
      } else if (uiState.view === 'cards') {
        useUiStore.setState({ view: 'decks' });
      }
    } catch (err) {
      console.error("Init Data Error:", err);
      const isAuthError = err?.response?.status === 401 || err?.status === 401;
      if (isAuthError) {
        useUiStore.getState().setIsAuthModalOpen(true, tr('Войдите, чтобы открыть свои колоды и прогресс'));
      } else {
        const decksNow = useDeckStore.getState().decks;
        if (!decksNow || decksNow.length === 0) {
          showToast(tr("Ошибка загрузки данных."));
        }
      }
    } finally {
      useDeckStore.setState({ isFetchingDecks: false });
      useUiStore.setState({ loading: false, hasInitialized: true });
    }
  };

  const syncProfile = async (currentProfile) => {
    const languageAtStart = getInterfaceLanguage();
    try {
      const { useLanguageStore } = await import('../store/useLanguageStore');
      const langState = useLanguageStore.getState();

      const validFirstName = (currentProfile.first_name && currentProfile.first_name !== 'Пользователь') 
        ? currentProfile.first_name 
        : undefined;

      const syncPayload = {
        first_name: validFirstName,
        last_name: currentProfile.last_name || undefined,
        username: currentProfile.username || undefined,
        photo_url: currentProfile.photo_url || undefined,
        is_guest: Boolean(currentProfile.is_guest),
        active_language: langState.activeLanguage,
        native_language: localStorage.getItem('native_language') || 'uk'
      };
      if (langState.hasSelectedLanguage) {
        syncPayload.has_selected_language = true;
      }

      // Always perform sync to ensure backend has the latest Telegram profile info
      const res = await requestWithRetry(() => api.post('/auth/sync', syncPayload), 'Profile sync');

      if (res.data.status === 'ok' && res.data.user) {
        const serverUser = res.data.user;
        const validLocalName = currentProfile.first_name && currentProfile.first_name !== 'Пользователь' ? currentProfile.first_name : null;
        const validServerName = serverUser.first_name && serverUser.first_name !== 'Пользователь' ? serverUser.first_name : null;
        const fallbackName = validServerName || validLocalName || serverUser.username || currentProfile.username || null;

        const hasIdentifyingInfo = Boolean(fallbackName || serverUser.username || currentProfile.username);
        const mergedProfile = {
          ...currentProfile,
          ...serverUser,
          first_name: fallbackName,
          photo_url: serverUser.photo_url || currentProfile.photo_url || null,
          is_guest: hasIdentifyingInfo ? Boolean(serverUser.is_guest) : true
        };

        setUserProfile(mergedProfile);
        storage.set('lerne_user_profile', JSON.stringify(mergedProfile));

        if (mergedProfile.has_selected_language || mergedProfile.active_language) {
          langState.syncLanguageFromExternal(
            mergedProfile.active_language || 'de',
            Boolean(mergedProfile.has_selected_language)
          );
        }

        if (mergedProfile.native_language && getInterfaceLanguage() === languageAtStart) {
          setInterfaceLanguage(mergedProfile.native_language);
          localStorage.setItem('native_language_selected', 'true');
          cloudStorage.set('lerne_native_language', normalizeInterfaceLanguage(mergedProfile.native_language) || 'uk');
          cloudStorage.set('lerne_native_language_selected', 'true');
        }
      }
    } catch (err) {
      console.error("Profile sync error:", err);
    }
  };
};
