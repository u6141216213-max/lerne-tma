// Execute the real store with synthetic API/storage/UI boundaries. No network.
// Run: node scripts/tests/auth_store_regression.mjs (no child process required).
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../../app/node_modules/zustand/esm/vanilla.mjs';

const source = readFileSync(new URL('../../app/src/store/useAuthStore.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace('export const useAuthStore', 'const useAuthStore');
function fixture({ methods = ['email_password'], email = 'fixture@example.test' } = {}) {
  const pending = new Map(), timers = new Map(), calls = [];
  let nextTimer = 0;
  const ui = { setIsAuthModalOpen: value => { ui.isAuthModalOpen = value; }, showToast() {},
    openSettings: tab => { ui.settingsTab = tab; } };
  const api = {
    async get(path) {
      if (path === '/auth/v2/me') return { data: { user_id: -123, is_guest: false } };
      if (path === '/auth/v2/identities') return { data: { providers: methods } };
      if (path === '/auth/v2/password-settings') return { data: { email, can_reset_password: false } };
      throw new Error(`Unexpected GET ${path}`);
    },
    async post(path, body) {
      calls.push({ path, body });
      if (path === '/auth/v2/challenges') return { data: { challenge_id: 'fixture', secret: 'synthetic',
        expires_at: new Date(Date.now() + 300000).toISOString(), authorization_url: 'https://example.invalid' } };
      return { data: { access_token: 'synthetic', refresh_token: 'synthetic' } };
    },
  };
  let deckStoreResetCount = 0;
  const closedDbUserIds = [];
  let resetAllDatabasesCount = 0;
  let currentUserId = null;
  const context = { create: createStore, tr: value => value, api,
    getUserProfile: () => (currentUserId ? { user_id: currentUserId } : null),
    saveUserProfile(p) { currentUserId = p?.user_id; },
    saveAuthSession() {}, clearAuthSession() { currentUserId = null; },
    getUserId: () => currentUserId,
    closeLocalDb: (id) => { closedDbUserIds.push(id); },
    resetAllDatabases: () => { resetAllDatabasesCount++; },
    storage: { remove() {} }, openExternalLink() {},
    useUiStore: { getState: () => ui, setState: value => Object.assign(ui, value) },
    useDeckStore: { getState: () => ({
      async fetchDecks() {},
      async fetchFolders() {},
      resetDeckStore: () => { deckStoreResetCount++; },
    }) },
    sessionStorage: { getItem: key => pending.get(key), setItem: (key, value) => pending.set(key, value),
      removeItem: key => pending.delete(key) },
    setInterval: callback => { timers.set(++nextTimer, callback); return nextTimer; },
    clearInterval: id => timers.delete(id), window: {},
  };
  vm.runInNewContext(`${source}\nglobalThis.subject = useAuthStore;`, context);
  return { store: context.subject, api, ui, pending, timers, calls,
    get deckStoreResetCount() { return deckStoreResetCount; },
    get closedDbUserIds() { return closedDbUserIds; },
    get resetAllDatabasesCount() { return resetAllDatabasesCount; },
    set currentUserId(id) { currentUserId = id; },
  };
}

test('registration offers linking without opening a second login', async () => {
  const f = fixture();
  await f.store.getState().loginWithEmailPassword('fixture@example.test', 'synthetic password', true);
  assert.equal(f.store.getState().showTelegramPrompt, true);
  assert.equal(f.ui.isAuthModalOpen, true);
  await f.store.getState().linkProvider('telegram');
  assert.equal(f.calls.at(-1).body.purpose, 'link');
});

test('normal login closes modal; linked Telegram does not prompt again', async () => {
  const f = fixture({ methods: ['email_password', 'telegram'] });
  await f.store.getState().loginWithEmailPassword('fixture@example.test', 'synthetic password', true);
  assert.equal(f.store.getState().showTelegramPrompt, false);
  assert.equal(f.ui.isAuthModalOpen, false);
});

test('recovery survives provider round trip and opens profile', async () => {
  const f = fixture();
  await f.store.getState().startPasswordRecovery('telegram');
  assert.equal(f.calls[0].body.purpose, 'login');
  await f.store.getState().checkPendingSession();
  assert.equal(f.ui.settingsTab, 'profile');
  assert.equal(f.timers.size, 0);
});

test('polling exchanges a one-time challenge only once at a time', async () => {
  const f = fixture();
  await f.store.getState().linkProvider('telegram');
  let resolve;
  let exchanges = 0;
  f.api.post = () => { exchanges++; return new Promise(done => { resolve = done; }); };
  const first = f.store.getState().checkPendingSession();
  assert.equal(await f.store.getState().checkPendingSession(), false);
  assert.equal(exchanges, 1);
  resolve({ data: { linked: true } });
  await first;
  assert.equal(f.timers.size, 0);
  assert.equal(f.store.getState().isPolling, false);
});

test('cancelling a pending exchange does not sign in afterwards', async () => {
  const f = fixture();
  await f.store.getState().startPasswordRecovery();
  let resolve;
  f.api.post = () => new Promise(done => { resolve = done; });
  const exchange = f.store.getState().checkPendingSession();
  f.store.getState().cancelPendingAuth();
  resolve({ data: { access_token: 'synthetic' } });
  assert.equal(await exchange, false);
  assert.equal(f.store.getState().userProfile, null);
  assert.equal(f.timers.size, 0);
});

test('adding email and resetting password use distinct endpoints', async () => {
  const f = fixture({ email: null });
  await f.store.getState().refreshAuthMethods();
  await f.store.getState().linkEmailPassword('fixture@example.test', 'synthetic password');
  assert.equal(f.calls.at(-1).path, '/auth/v2/email-password/link');
  f.store.setState({ passwordSettings: { email: 'fixture@example.test', can_reset_password: true } });
  await f.store.getState().linkEmailPassword('fixture@example.test', 'synthetic password');
  assert.equal(f.calls.at(-1).path, '/auth/v2/email-password/set');
});

test('expired and rejected challenges stop polling and allow retry', async () => {
  const f = fixture();
  await f.store.getState().startPasswordRecovery();
  const [key, value] = [...f.pending][0];
  f.pending.set(key, JSON.stringify({ ...JSON.parse(value), expiresAt: '2000-01-01T00:00:00Z' }));
  assert.equal(await f.store.getState().checkPendingSession(), false);
  assert.equal(f.timers.size, 0);
  await f.store.getState().startPasswordRecovery();
  f.api.post = async () => { throw { response: { status: 401, data: { detail: 'reauthentication_required' } } }; };
  assert.equal(await f.store.getState().checkPendingSession(), false);
  assert.equal(f.timers.size, 0);
  assert.equal(f.store.getState().isPolling, false);
});

test('logout isolates state: resets deck store, closes local database, and resets all databases', () => {
  const f = fixture();
  f.currentUserId = -999;
  f.store.getState().logout();
  assert.equal(f.deckStoreResetCount, 1);
  assert.deepEqual(f.closedDbUserIds, [-999]);
  assert.equal(f.resetAllDatabasesCount, 1);
  assert.equal(f.store.getState().userProfile, null);
});

test('finishLogin isolates state when switching user accounts', async () => {
  const f = fixture();
  f.currentUserId = -999;
  await f.store.getState().finishLogin({ access_token: 'tok', refresh_token: 'ref' });
  // f.api.get('/auth/v2/me') returns user_id: -123 != -999
  assert.equal(f.deckStoreResetCount, 1);
  assert.deepEqual(f.closedDbUserIds, [-999]);
  assert.equal(f.store.getState().userProfile.user_id, -123);
});

