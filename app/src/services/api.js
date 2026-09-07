import axios from 'axios';
import { getAccessToken } from '../utils/auth';
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
    config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
  }
  if (config.method && config.method.toLowerCase() === 'get') {
    const separator = config.url.includes('?') ? '&' : '?';
    config.url = `${config.url}${separator}_t=${Date.now()}`;
  }
  return config;
});

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
