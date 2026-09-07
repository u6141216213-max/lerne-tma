// Test silent token refresh response interceptor logic
import test from 'node:test';
import assert from 'node:assert/strict';

test('silent refresh interceptor queues concurrent requests and updates bearer token', async () => {
  let refreshCalls = 0;
  let savedSession = null;
  let clearedSession = false;

  let authSession = { access_token: 'expired_access', refresh_token: 'valid_refresh' };
  const getAuthSession = () => authSession;
  const saveAuthSession = (s) => { savedSession = s; authSession = s; };
  const clearAuthSession = () => { clearedSession = true; authSession = null; };

  let isRefreshing = false;
  let failedQueue = [];

  const processQueue = (error, token = null) => {
    failedQueue.forEach((prom) => {
      if (error) prom.reject(error);
      else prom.resolve(token);
    });
    failedQueue = [];
  };

  // Synthetic axiosInstance and mock backend
  const networkPost = async (url, data) => {
    if (url.includes('/auth/v2/refresh')) {
      refreshCalls++;
      if (data.refresh_token === 'valid_refresh') {
        return { data: { access_token: 'fresh_access', refresh_token: 'fresh_refresh' } };
      }
      throw { response: { status: 401, data: { detail: 'invalid_token' } } };
    }
    throw new Error(`Unexpected POST ${url}`);
  };

  const executeRequest = async (config) => {
    // Simulate server response: if token is expired, return 401
    const authHeader = config.headers?.Authorization;
    if (authHeader === 'Bearer fresh_access') {
      return { status: 200, data: { success: true, url: config.url }, config };
    }
    const error = new Error('Request failed with status code 401');
    error.response = { status: 401 };
    error.config = config;
    return handleResponseError(error);
  };

  const handleResponseError = async (error) => {
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
          originalRequest.headers = originalRequest.headers || {};
          originalRequest.headers.Authorization = `Bearer ${newToken}`;
          return executeRequest(originalRequest);
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
      const response = await networkPost('/auth/v2/refresh', { refresh_token: refreshToken });
      const newSession = response.data;
      saveAuthSession(newSession);
      const newToken = newSession.access_token;

      originalRequest.headers = originalRequest.headers || {};
      originalRequest.headers.Authorization = `Bearer ${newToken}`;

      processQueue(null, newToken);
      return executeRequest(originalRequest);
    } catch (refreshErr) {
      processQueue(refreshErr, null);
      clearAuthSession();
      return Promise.reject(refreshErr);
    } finally {
      isRefreshing = false;
    }
  };

  // Launch 3 concurrent requests that encounter 401
  const req1 = executeRequest({ url: '/decks', headers: { Authorization: 'Bearer expired_access' } });
  const req2 = executeRequest({ url: '/folders', headers: { Authorization: 'Bearer expired_access' } });
  const req3 = executeRequest({ url: '/cards', headers: { Authorization: 'Bearer expired_access' } });

  const [res1, res2, res3] = await Promise.all([req1, req2, req3]);

  // Assertions:
  assert.equal(refreshCalls, 1, 'Only 1 refresh call was made for 3 concurrent requests');
  assert.equal(res1.status, 200);
  assert.equal(res2.status, 200);
  assert.equal(res3.status, 200);
  assert.equal(savedSession.access_token, 'fresh_access');
  assert.equal(savedSession.refresh_token, 'fresh_refresh');
  assert.equal(clearedSession, false);
});

test('silent refresh fails and clears session when refresh token is invalid', async () => {
  let refreshCalls = 0;
  let clearedSession = false;

  let authSession = { access_token: 'expired_access', refresh_token: 'bad_refresh' };
  const getAuthSession = () => authSession;
  const saveAuthSession = (s) => { authSession = s; };
  const clearAuthSession = () => { clearedSession = true; authSession = null; };

  let isRefreshing = false;
  let failedQueue = [];

  const processQueue = (error, token = null) => {
    failedQueue.forEach((prom) => {
      if (error) prom.reject(error);
      else prom.resolve(token);
    });
    failedQueue = [];
  };

  const networkPost = async (url) => {
    if (url.includes('/auth/v2/refresh')) {
      refreshCalls++;
      throw { response: { status: 401, data: { detail: 'invalid_refresh_token' } } };
    }
  };

  const handleResponseError = async (error) => {
    const originalRequest = error?.config;
    if (!originalRequest) return Promise.reject(error);

    const is401 = error.response && error.response.status === 401;
    const url = originalRequest.url || '';
    const isAuthEndpoint = url.includes('/auth/v2/refresh');

    if (!is401 || isAuthEndpoint || originalRequest._retry) {
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      });
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
      await networkPost('/auth/v2/refresh', { refresh_token: refreshToken });
    } catch (refreshErr) {
      processQueue(refreshErr, null);
      clearAuthSession();
      return Promise.reject(refreshErr);
    } finally {
      isRefreshing = false;
    }
  };

  const error = new Error('Unauthorized');
  error.response = { status: 401 };
  error.config = { url: '/decks', headers: { Authorization: 'Bearer expired_access' } };

  await assert.rejects(async () => {
    await handleResponseError(error);
  });

  assert.equal(refreshCalls, 1);
  assert.equal(clearedSession, true, 'Session was cleared upon refresh failure');
});
