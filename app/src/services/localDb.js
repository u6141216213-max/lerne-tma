import Dexie from 'dexie';
import { Capacitor } from '@capacitor/core';
import { getUserId } from '../utils/auth';

const databases = new Map();
const preparations = new Map();

export const getLocalDb = () => {
  const userId = String(getUserId());
  if (databases.has(userId)) return databases.get(userId);
  const database = new Dexie(`LerneLocalDB_${userId}`);
  configureDatabase(database);
  databases.set(userId, database);
  return database;
};

export const closeLocalDb = (userId) => {
  const targetId = userId != null ? String(userId) : String(getUserId());
  if (databases.has(targetId)) {
    const database = databases.get(targetId);
    try {
      database.close();
    } catch { /* ignore */ }
    databases.delete(targetId);
    preparations.delete(database.name);
  }
};

export const resetAllDatabases = () => {
  for (const [, database] of databases.entries()) {
    try {
      database.close();
    } catch { /* ignore */ }
  }
  databases.clear();
  preparations.clear();
};

export async function prepareLocalDb() {
  const database = getLocalDb();
  if (!preparations.has(database.name)) {
    const userId = getUserId();
    preparations.set(database.name, migrateLegacy(database, userId).catch(error => {
      preparations.delete(database.name);
      throw error;
    }));
  }
  await preparations.get(database.name);
  return database;
}

async function migrateLegacy(database, userId) {
  if (await database.syncState.get('legacyImported')) return;
  if (await Dexie.exists('LerneLocalDB')) {
    const legacy = new Dexie('LerneLocalDB');
    try {
      await legacy.open();
      const owner = localStorage.getItem('lerne_db_owner') || localStorage.getItem('lerne_last_sync_user_id');
      const knownOwner = owner === String(userId);
      const rows = {};
      for (const name of ['folders', 'decks', 'cards', 'progress']) {
        rows[name] = legacy.tables.some(t => t.name === name) ? await legacy.table(name).toArray() : [];
      }
      if (!knownOwner) {
        rows.folders = rows.folders.filter(f => String(f.user_id) === String(userId));
        rows.decks = rows.decks.filter(d => String(d.user_id) === String(userId));
        const deckIds = new Set(rows.decks.map(d => d.id));
        rows.cards = rows.cards.filter(c => deckIds.has(c.deck_id));
        rows.progress = rows.progress.filter(p => String(p.user_id) === String(userId));
      }
      await database.transaction('rw', database.folders, database.decks, database.cards, database.progress, async () => {
        for (const [name, items] of Object.entries(rows)) {
          for (const item of items) {
            const key = name === 'progress' ? [item.card_id, item.user_id] : item.id;
            if (!await database[name].get(key)) await database[name].put(item);
          }
        }
      });
    } finally {
      legacy.close();
    }
  }
  await database.syncState.put({ key: 'legacyImported' });
}

// Existing consumers keep the same API; each account retains its unsent changes.
export const db = new Proxy({}, {
  get(_target, key) {
    const database = getLocalDb();
    const value = database[key];
    return typeof value === 'function' ? value.bind(database) : value;
  },
});

function configureDatabase(db) {

// Define database schema
// Note: We only index fields we intend to query on (filter / sort).
db.version(1).stores({
  decks: 'id, user_id, name, is_deleted, is_dirty',
  cards: 'id, deck_id, front_text, is_deleted, is_dirty',
  progress: '[card_id+user_id], user_id, card_id, next_review, is_dirty'
});

db.version(2).stores({
  folders: 'id, user_id, name, is_deleted, is_dirty',
  decks: 'id, user_id, folder_id, name, is_deleted, is_dirty',
  cards: 'id, deck_id, front_text, is_deleted, is_dirty',
  progress: '[card_id+user_id], user_id, card_id, next_review, is_dirty'
});

db.version(3).stores({
  syncState: 'key',
  media: 'url',
});
}

// Helper to generate temporary negative IDs
export const getNextTempId = () => {
  const current = parseInt(localStorage.getItem('lerne_temp_id_counter') || '-1', 10);
  const next = current - 1;
  localStorage.setItem('lerne_temp_id_counter', next.toString());
  return current;
};

// Check if app is in offline-first mode
export const isOfflineMode = () => {
  return Capacitor.isNativePlatform() || localStorage.getItem('offline_mode') === 'true'
    || (import.meta.env.DEV && new URLSearchParams(window.location.search).get('offline') === '1');
};

export async function resolveLocalRequest(url, body) {
  const mappings = (await getLocalDb().syncState.get('aliases'))?.mappings || {};
  const id = (kind, value) => mappings[kind]?.[String(value)] ?? value;
  const resolvedUrl = url
    .replace(/\/(decks|folders|cards)\/(-\d+)(?=\/|\?|$)/g, (_, kind, value) => `/${kind}/${id(kind, Number(value))}`)
    .replace(/\/study\/card\/(-\d+)/, (_, value) => `/study/card/${id('cards', Number(value))}`)
    .replace(/\/trash\/(deck|card)\/(-\d+)/, (_, kind, value) => `/trash/${kind}/${id(`${kind}s`, Number(value))}`);
  if (!body || typeof body !== 'object' || body instanceof FormData) return { url: resolvedUrl, body };
  const copy = { ...body };
  for (const [field, kind] of Object.entries({ card_id: 'cards', deck_id: 'decks', folder_id: 'folders', parent_id: 'folders', after_card_id: 'cards' })) {
    if (field in copy) copy[field] = id(kind, copy[field]);
  }
  return { url: resolvedUrl, body: copy };
}
