import { tr } from '../i18n/locale';
import { prepareLocalDb, getNextTempId } from './localDb';
import { networkApi } from './api';
import { getAccessToken, getUserId } from '../utils/auth';

const entities = ['folders', 'decks', 'cards', 'progress'];
const keyFor = (name, item, userId) => name === 'progress' ? [item.card_id, userId] : item.id;
const revision = item => item.local_revision || item.updated_at;
const remap = (map, id) => map?.[String(id)] ?? id;

async function migrateTemporaryCards(db, userId) {
  const legacy = await db.cards.filter(c => typeof c.id === 'string').toArray();
  for (const card of legacy) {
    const id = getNextTempId();
    await db.cards.delete(card.id);
    await db.cards.put({ ...card, id, is_dirty: 1 });
    const progress = await db.progress.get([card.id, userId]);
    if (progress) {
      await db.progress.delete([card.id, userId]);
      await db.progress.put({ ...progress, card_id: id, is_dirty: 1 });
    }
  }
}

async function pendingBatch(db, userId) {
  return db.transaction('rw', [...entities.map(name => db[name]), db.syncState], async () => {
    const pending = await db.syncState.get('pending');
    if (pending) return pending;
    await migrateTemporaryCards(db, userId);
    const batch = { key: 'pending', request_id: crypto.randomUUID(), userId };
    for (const name of entities) batch[name] = await db[name].where('is_dirty').equals(1).toArray();
    if (!entities.some(name => batch[name].length)) return null;
    await db.syncState.put(batch);
    return batch;
  });
}

function payloadFor(batch) {
  const payload = { request_id: batch.request_id };
  for (const name of entities) {
    payload[name] = batch[name].map(item => {
      const copy = { ...item };
      for (const key of ['metadata', 'tags']) {
        if (copy[key] != null && typeof copy[key] !== 'string') copy[key] = JSON.stringify(copy[key]);
      }
      return copy;
    });
  }
  return payload;
}

async function acknowledge(db, batch, mappings) {
  const userId = batch.userId;
  await db.transaction('rw', [...entities.map(name => db[name]), db.syncState], async () => {
    for (const name of entities) {
      for (const sent of batch[name]) {
        const key = keyFor(name, sent, userId);
        const current = await db[name].get(key);
        if (!current) continue;
        const isUnchanged = revision(current) === revision(sent);
        const mapped = name === 'progress'
          ? { ...current, card_id: remap(mappings.cards, current.card_id) }
          : { ...current, id: remap(mappings[name], current.id) };
        if (isUnchanged) mapped.is_dirty = 0;
        await db[name].delete(key);
        await db[name].put(mapped);
      }
    }
    // Include children created while the request was in flight.
    await db.folders.toCollection().modify(f => { f.parent_id = remap(mappings.folders, f.parent_id); });
    await db.decks.toCollection().modify(d => { d.folder_id = remap(mappings.folders, d.folder_id); });
    await db.cards.toCollection().modify(c => { c.deck_id = remap(mappings.decks, c.deck_id); });
    for (const p of await db.progress.toArray()) {
      const id = remap(mappings.cards, p.card_id);
      if (id !== p.card_id) {
        await db.progress.delete([p.card_id, p.user_id]);
        await db.progress.put({ ...p, card_id: id });
      }
    }
    await db.syncState.delete('pending');
    const aliases = (await db.syncState.get('aliases'))?.mappings || {};
    for (const name of ['folders', 'decks', 'cards']) aliases[name] = { ...aliases[name], ...mappings[name] };
    await db.syncState.put({ key: 'aliases', mappings: aliases });
  });
}

async function applySnapshot(db, data, userId) {
  if (data.status !== 'success' || !entities.every(name => Array.isArray(data[name]))) {
    throw new Error(tr("Некорректный ответ синхронизации"));
  }
  await db.transaction('rw', [...entities.map(name => db[name]), db.syncState], async () => {
    for (const name of entities) {
      const incoming = new Set(data[name].map(item => name === 'progress' ? item.card_id : item.id));
      for (const item of data[name]) {
        const key = keyFor(name, item, userId);
        const local = await db[name].get(key);
        if (local?.is_dirty) continue;
        await db[name].put({ ...item, ...(name === 'progress' ? { user_id: userId } : {}), is_dirty: 0 });
      }
      // A full snapshot also carries hard deletions and revoked access.
      for (const item of await db[name].toArray()) {
        const id = name === 'progress' ? item.card_id : item.id;
        if (id > 0 && !item.is_dirty && !incoming.has(id)) await db[name].delete(keyFor(name, item, userId));
      }
    }
    await db.syncState.put({ key: 'lastSync', time: data.server_time });
  });
}

export const syncService = {
  isSyncing: false,
  async sync() {
    if (navigator.locks) {
      return navigator.locks.request('lerne-offline-sync', { ifAvailable: true }, lock =>
        lock ? this.runSync() : { success: false, reason: 'Already syncing' });
    }
    return this.runSync();
  },
  async runSync() {
    if (this.isSyncing) return { success: false, reason: 'Already syncing' };
    if (!navigator.onLine) return { success: false, reason: tr("Нет подключения к интернету") };
    if (!getAccessToken() || !getUserId()) return { success: false, reason: tr("Требуется вход") };
    this.isSyncing = true;
    const userId = getUserId();
    const options = {};
    let mappings = { folders: {}, decks: {}, cards: {} };
    try {
      const db = await prepareLocalDb();
      const batch = await pendingBatch(db, userId);
      if (batch) {
        const response = await networkApi.post('/sync/v2/push', payloadFor(batch), options);
        if (response.data?.status !== 'success' || !response.data.mappings) throw new Error(tr("Сервер не подтвердил сохранение изменений"));
        mappings = response.data.mappings;
        for (const name of ['folders', 'decks', 'cards']) {
          for (const item of batch[name].filter(item => item.id < 0)) {
            if (!(mappings[name]?.[String(item.id)] > 0)) throw new Error(tr("Сервер не вернул идентификатор новой записи"));
          }
        }
        await acknowledge(db, batch, mappings);
        if (getUserId() === userId) window.dispatchEvent(new CustomEvent('lerne:ids-remapped', { detail: { mappings, userId } }));
      }
      const response = await networkApi.get('/sync/v2/pull', options);
      await applySnapshot(db, response.data, userId);
      if (getUserId() === userId) {
        localStorage.setItem('lerne_last_sync_time', response.data.server_time);
        localStorage.setItem('lerne_last_sync_user_id', String(userId));
        window.dispatchEvent(new CustomEvent('lerne:synced', { detail: { userId } }));
      }
      return { success: true, server_time: response.data.server_time };
    } catch (error) {
      const reason = error.response?.status === 404
        ? tr("Сервер ещё не обновлён для офлайн-синхронизации. Данные сохранены на устройстве.")
        : error.response?.data?.detail || error.message;
      console.warn('[Sync]', reason);
      return { success: false, reason };
    } finally {
      this.isSyncing = false;
    }
  },
};
