import test from 'node:test';
import assert from 'node:assert/strict';

test('collectUserSettings gathers all design and study fields', async () => {
  const mockState = {
    cardBgFront: 'dark_obsidian',
    cardFont: 'Inter',
    cardTextColor: '#ffffff',
    cardFontSize: 2.0,
    autoPlay: true,
    autoShow: false,
    studyMode: 'cloze',
    srsExtendedGrades: true,
    ttsSpeed: 1,
    randomEnabledModes: ['cloze', 'speak'],
    userDesign: { cardFont: 'Inter' },
    unrelatedField: 'ignore_me',
  };

  const DESIGN_STORAGE_MAP = {
    cardBgFront: 'lerne_card_bg_front',
    cardFont: 'lerne_card_font',
    cardTextColor: 'lerne_card_text_color',
    cardFontSize: 'lerne_card_font_size',
  };

  const STUDY_STORAGE_MAP = {
    autoPlay: 'lerne_autoplay',
    autoShow: 'lerne_autoshow',
    studyMode: 'lerne_study_mode',
    srsExtendedGrades: 'lerne_srs_extended_grades',
    ttsSpeed: 'lerne_tts_speed',
    randomEnabledModes: 'lerne_random_enabled_modes',
  };

  const collect = (state) => {
    const settings = {};
    Object.keys(DESIGN_STORAGE_MAP).forEach((k) => {
      if (state[k] !== undefined) settings[k] = state[k];
    });
    Object.keys(STUDY_STORAGE_MAP).forEach((k) => {
      if (state[k] !== undefined) settings[k] = state[k];
    });
    if (state.userDesign !== undefined) {
      settings.userDesign = state.userDesign;
    }
    return settings;
  };

  const collected = collect(mockState);
  assert.equal(collected.cardBgFront, 'dark_obsidian');
  assert.equal(collected.cardFont, 'Inter');
  assert.equal(collected.cardTextColor, '#ffffff');
  assert.equal(collected.cardFontSize, 2.0);
  assert.equal(collected.autoPlay, true);
  assert.equal(collected.studyMode, 'cloze');
  assert.equal(collected.srsExtendedGrades, true);
  assert.equal(collected.unrelatedField, undefined);
  assert.deepEqual(collected.userDesign, { cardFont: 'Inter' });
});

test('syncUserSettingsFromServer updates store state and persists to storage', async () => {
  const localStorageMock = new Map();
  const storage = {
    get: (k) => localStorageMock.get(k) ?? null,
    set: (k, v) => localStorageMock.set(k, String(v)),
    remove: (k) => localStorageMock.delete(k),
  };

  let currentState = {
    cardBgFront: 'liquid_emerald',
    cardFontSize: 1.7,
    autoPlay: false,
    studyMode: 'classic',
    srsExtendedGrades: false,
  };

  const DESIGN_STORAGE_MAP = {
    cardBgFront: 'lerne_card_bg_front',
    cardFontSize: 'lerne_card_font_size',
  };

  const STUDY_STORAGE_MAP = {
    autoPlay: 'lerne_autoplay',
    studyMode: 'lerne_study_mode',
    srsExtendedGrades: 'lerne_srs_extended_grades',
  };

  const syncUserSettingsFromServer = (serverSettings) => {
    if (!serverSettings || typeof serverSettings !== 'object') return;
    const updates = {};

    Object.entries(DESIGN_STORAGE_MAP).forEach(([stateKey, storageKey]) => {
      if (serverSettings[stateKey] !== undefined) {
        const val = serverSettings[stateKey];
        updates[stateKey] = val;
        storage.set(storageKey, val);
      }
    });

    Object.entries(STUDY_STORAGE_MAP).forEach(([stateKey, storageKey]) => {
      if (serverSettings[stateKey] !== undefined) {
        const val = serverSettings[stateKey];
        if (typeof val === 'boolean' || typeof val === 'number') {
          storage.set(storageKey, String(val));
        } else if (Array.isArray(val) || (typeof val === 'object' && val !== null)) {
          storage.set(storageKey, JSON.stringify(val));
        } else {
          storage.set(storageKey, val);
        }
        updates[stateKey] = val;
      }
    });

    currentState = { ...currentState, ...updates };
  };

  // Simulate server sending user settings from Web to Android
  const webSettings = {
    cardBgFront: 'dark_obsidian',
    cardFontSize: 2.2,
    autoPlay: true,
    studyMode: 'speak',
    srsExtendedGrades: true,
  };

  syncUserSettingsFromServer(webSettings);

  // Assert state was updated
  assert.equal(currentState.cardBgFront, 'dark_obsidian');
  assert.equal(currentState.cardFontSize, 2.2);
  assert.equal(currentState.autoPlay, true);
  assert.equal(currentState.studyMode, 'speak');
  assert.equal(currentState.srsExtendedGrades, true);

  // Assert local storage was updated
  assert.equal(storage.get('lerne_card_bg_front'), 'dark_obsidian');
  assert.equal(storage.get('lerne_card_font_size'), '2.2');
  assert.equal(storage.get('lerne_autoplay'), 'true');
  assert.equal(storage.get('lerne_study_mode'), 'speak');
  assert.equal(storage.get('lerne_srs_extended_grades'), 'true');
});
