/**
 * Platform Abstraction Adapter for Lerne
 * Handles platform detection and hardware/UI bridges across Telegram Mini App (TMA),
 * Capacitor (Android/iOS Native), and Standalone Web.
 */

export const getPlatform = () => {
  if (typeof window !== 'undefined') {
    if (window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function' && window.Capacitor.isNativePlatform()) {
      return 'capacitor';
    }
    if (window.Telegram?.WebApp && window.Telegram.WebApp.initData) {
      return 'telegram';
    }
  }
  return 'web';
};

export const isTelegram = () => getPlatform() === 'telegram';
export const isCapacitor = () => getPlatform() === 'capacitor';
export const isNative = () => isCapacitor();
export const isWeb = () => getPlatform() === 'web';

/**
 * Trigger platform-appropriate haptic feedback
 * @param {'light'|'medium'|'heavy'|'success'|'error'|'warning'|'selection'} type 
 */
export const triggerHaptic = (type = 'light') => {
  try {
    // 1. Telegram WebApp Haptic
    const tg = window.Telegram?.WebApp?.HapticFeedback;
    if (tg) {
      if (type === 'selection') {
        tg.selectionChanged();
      } else if (['success', 'error', 'warning'].includes(type)) {
        tg.notificationOccurred(type);
      } else {
        tg.impactOccurred(type);
      }
      return;
    }

    // 2. Capacitor Haptics Plugin (Native Android / iOS)
    const capHaptics = window.Capacitor?.Plugins?.Haptics;
    if (capHaptics) {
      if (type === 'selection') {
        capHaptics.selectionChanged();
      } else if (['success', 'error', 'warning'].includes(type)) {
        const notificationTypeMap = { success: 'SUCCESS', warning: 'WARNING', error: 'ERROR' };
        capHaptics.notification({ type: notificationTypeMap[type] || 'SUCCESS' });
      } else {
        const styleMap = { light: 'LIGHT', medium: 'MEDIUM', heavy: 'HEAVY' };
        capHaptics.impact({ style: styleMap[type] || 'LIGHT' });
      }
      return;
    }

    // 3. Web Vibration Fallback
    if (navigator?.vibrate) {
      const patterns = {
        light: 10,
        medium: 25,
        heavy: 50,
        selection: 5,
        success: [15, 30, 15],
        warning: [30, 50, 30],
        error: [50, 50, 50]
      };
      navigator.vibrate(patterns[type] || 15);
    }
  } catch {
    // Ignore haptic errors on unsupported devices
  }
};

export const hapticImpact = (style = 'light') => triggerHaptic(style);
export const hapticNotification = (type = 'success') => triggerHaptic(type);
export const hapticSuccess = () => triggerHaptic('success');
export const hapticError = () => triggerHaptic('error');
export const hapticWarning = () => triggerHaptic('warning');
export const hapticSelection = () => triggerHaptic('selection');

/**
 * Disable swipe/window closing confirmation in Telegram Mini App
 */
export const disableClosingConfirmation = () => {
  try {
    if (window.Telegram?.WebApp?.disableClosingConfirmation) {
      window.Telegram.WebApp.disableClosingConfirmation();
    }
  } catch {
    // Ignore error
  }
};
export const enableClosingConfirmation = disableClosingConfirmation;

/**
 * Close application on native platform / TMA
 */
export const closeApp = () => {
  try {
    if (window.Telegram?.WebApp?.close) {
      window.Telegram.WebApp.close();
      return;
    }
    if (window.Capacitor?.Plugins?.App?.exitApp) {
      window.Capacitor.Plugins.App.exitApp();
      return;
    }
  } catch {
    // Ignore error
  }
};

/**
 * Control Native Back Button (Telegram & Capacitor)
 */
export const setupBackButton = (onClickCallback) => {
  const cleanupFns = [];

  try {
    // Telegram BackButton
    const tg = window.Telegram?.WebApp?.BackButton;
    if (tg) {
      tg.show();
      tg.onClick(onClickCallback);
      cleanupFns.push(() => {
        try { tg.offClick(onClickCallback); } catch { /* ignore */ }
      });
    }

    // Capacitor App BackButton (Android hardware back button)
    const capApp = window.Capacitor?.Plugins?.App;
    if (capApp && typeof capApp.addListener === 'function') {
      const listenerPromise = capApp.addListener('backButton', () => {
        onClickCallback();
      });
      cleanupFns.push(() => {
        listenerPromise.then(l => l?.remove?.()).catch(() => {});
      });
    }
  } catch {
    // Ignore error
  }

  return () => {
    cleanupFns.forEach(fn => fn());
  };
};

export const showBackButton = () => {
  try {
    const tg = window.Telegram?.WebApp?.BackButton;
    if (tg) {
      tg.show();
    }
  } catch {
    // Ignore error
  }
};

export const hideBackButton = () => {
  try {
    const tg = window.Telegram?.WebApp?.BackButton;
    if (tg) {
      tg.hide();
    }
  } catch {
    // Ignore error
  }
};

/**
 * Open external URL in system browser or Telegram target
 */
/**
 * Reserve a browser popup while the click still has user activation.
 * OAuth/Telegram URLs are fetched first, so opening them afterwards can be
 * blocked by the browser's popup protection.
 */
export const prepareExternalLink = () => {
  try {
    if (getPlatform() !== 'web') return null;
    const popup = window.open('', '_blank');
    if (!popup) return null;
    // Write loading content immediately so the browser keeps the popup alive
    // while the async challenge request is in flight. Without this, Edge and
    // Chrome leave about:blank and then block the deferred navigation because
    // user activation has already expired.
    try {
      popup.document.write(
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">' +
        '<title>Lerne — вход\u2026</title>' +
        '<style>body{font-family:system-ui;background:#101827;color:#e9d5ff;' +
        'display:grid;min-height:100vh;place-items:center;margin:0;font-size:1.1rem}</style>' +
        '</head><body><p>Загрузка\u2026</p></body></html>'
      );
      popup.document.close();
    } catch { /* cross-origin guard — cannot happen for a freshly opened blank window */ }
    popup.opener = null;
    return popup;
  } catch {
    return null;
  }
};

export const openExternalLink = (url, preparedWindow = null) => {
  try {
    // Use Telegram's openLink only when genuinely running inside the Telegram
    // client (initData is non-empty). telegram-web-app.js is always loaded by
    // index.html, so window.Telegram.WebApp.openLink exists even in a plain
    // browser, but outside Telegram it does nothing — the preparedWindow would
    // stay on "Загрузка..." forever without this guard.
    if (isTelegram() && window.Telegram?.WebApp?.openLink) {
      window.Telegram.WebApp.openLink(url);
      return;
    }
    if (window.Capacitor?.Plugins?.Browser?.open) {
      window.Capacitor.Plugins.Browser.open({ url });
      return;
    }
  } catch {
    // Fallback to window.open
  }
  try {
    if (preparedWindow && !preparedWindow.closed) {
      preparedWindow.location.href = url;
      return;
    }
    const popup = window.open(url, '_blank', 'noopener,noreferrer');
    // If a popup was blocked, still give the user a working sign-in path.
    if (!popup) window.location.assign(url);
  } catch {
    window.location.assign(url);
  }
};
