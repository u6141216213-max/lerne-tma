import axios from 'axios';
import { getAccessToken, getAuthSession, saveAuthSession, clearAuthSession } from '../utils/auth';
import { isOfflineMode, resolveLocalRequest, prepareLocalDb } from './localDb';
import { offlineApi } from './offlineApi';
import { API_BASE_URL } from './apiConfig';

const baseURL = API_BASE_URL;

const axiosInstance = axios.create({
  baseURL: baseURL,
  timeout: 15000,
});

// The server derives the account only from a short-lived bearer token.
axiosInstance.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token && !config.headers?.Authorization) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  if (config.method && config.method.toLowerCase() === 'get') {
    if (/_t=\d+/.test(config.url)) {
      config.url = config.url.replace(/_t=\d+/, `_t=${Date.now()}`);
    } else {
      const separator = config.url.includes('?') ? '&' : '?';
      config.url = `${config.url}${separator}_t=${Date.now()}`;
    }
  }
  return config;
});

let isRefreshing = false;
let failedQueue = [];

const processQueue = (error, token = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });
  failedQueue = [];
};

axiosInstance.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error?.config;
    if (!originalRequest) return Promise.reject(error);

    const is401 = error.response && error.response.status === 401;
    const url = originalRequest.url || '';
    const isAuthEndpoint = 
      url.includes('/auth/v2/refresh') ||
      url.includes('/auth/v2/email-password/login') ||
      url.includes('/auth/v2/email-password/register') ||
      url.includes('/auth/v2/challenges');

    if (!is401 || isAuthEndpoint || originalRequest._retry) {
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      })
        .then((newToken) => {
          if (typeof originalRequest.headers?.set === 'function') {
            originalRequest.headers.set('Authorization', `Bearer ${newToken}`);
          } else {
            originalRequest.headers = originalRequest.headers || {};
            originalRequest.headers.Authorization = `Bearer ${newToken}`;
          }
          return axiosInstance(originalRequest);
        })
        .catch((err) => Promise.reject(err));
    }

    originalRequest._retry = true;
    isRefreshing = true;

    const session = getAuthSession();
    const refreshToken = session?.refresh_token;

    if (!refreshToken) {
      isRefreshing = false;
      clearAuthSession();
      processQueue(error, null);
      return Promise.reject(error);
    }

    try {
      const response = await axios.post(`${baseURL}/auth/v2/refresh`, {
        refresh_token: refreshToken,
      }, {
        headers: { 'Content-Type': 'application/json' },
        timeout: 10000,
      });

      const newSession = response.data;
      saveAuthSession(newSession);
      const newToken = newSession.access_token;

      if (typeof originalRequest.headers?.set === 'function') {
        originalRequest.headers.set('Authorization', `Bearer ${newToken}`);
      } else {
        originalRequest.headers = originalRequest.headers || {};
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
      }

      processQueue(null, newToken);
      return axiosInstance(originalRequest);
    } catch (refreshErr) {
      processQueue(refreshErr, null);
      clearAuthSession();
      try {
        const { useUiStore } = await import('../store/useUiStore');
        useUiStore.getState().setIsAuthModalOpen(true);
      } catch { /* ignore */ }
      return Promise.reject(refreshErr);
    } finally {
      isRefreshing = false;
    }
  }
);

// Проксируем методы Axios для поддержки офлайн-режима и автоматического фоллбека
const api = new Proxy(axiosInstance, {
  get(target, propKey, receiver) {
    if (['get', 'post', 'put', 'delete', 'patch'].includes(propKey)) {
      return async (url, ...args) => {
        if (isOfflineMode()) {
          await prepareLocalDb();
          const resolved = await resolveLocalRequest(url, args[0]);
          url = resolved.url;
          if (args.length) args[0] = resolved.body;
        }
        const isOfflineEndpoint = 
          url.startsWith('/decks') || 
          url.startsWith('/folders') ||
          url.startsWith('/cards') || 
          url.startsWith('/study') || 
          url.startsWith('/trash') ||
          url.startsWith('/init');

        const forceOffline = isOfflineMode() || (typeof navigator !== 'undefined' && !navigator.onLine);

        if (forceOffline && isOfflineEndpoint) {
          try {
            return await offlineApi.handle(propKey, url, ...args);
          } catch (err) {
            if (err.code !== 'OFFLINE_UNSUPPORTED' || !navigator.onLine) throw err;
          }
        }

        // Try online server request first
        try {
          const needsRefresh = isOfflineMode() && isOfflineEndpoint && propKey !== 'get';
          if (needsRefresh) {
            const { syncService } = await import('./syncService');
            const synced = await syncService.sync();
            if (!synced.success) throw new Error(synced.reason);
            const resolved = await resolveLocalRequest(url, args[0]);
            url = resolved.url;
            if (args.length) args[0] = resolved.body;
          }
          const response = await Reflect.get(target, propKey, receiver).call(target, url, ...args);
          if (needsRefresh) {
            const { syncService } = await import('./syncService');
            await syncService.sync();
          }
          return response;
        } catch (networkErr) {
          // Automatic fallback to local Dexie DB when network is disconnected or server unavailable
          const isNetworkFailure = 
            !navigator?.onLine || 
            networkErr.code === 'ERR_NETWORK' || 
            !networkErr.response || 
            networkErr.message?.includes('Network Error');

          if (isOfflineEndpoint && isNetworkFailure) {
            console.log(`[Network Unavailable] Falling back to local offline DB for ${propKey.toUpperCase()} ${url}`);
            try {
              return await offlineApi.handle(propKey, url, ...args);
            } catch (fallbackErr) {
              console.error(`[Offline Fallback Failed] ${propKey.toUpperCase()} ${url}:`, fallbackErr);
            }
          }
          throw networkErr;
        }
      };
    }
    return Reflect.get(target, propKey, receiver);
  },
  
  apply(target, thisArg, argumentsList) {
    return Reflect.apply(target, thisArg, argumentsList);
  }
});

export default api;
export { axiosInstance as networkApi };
