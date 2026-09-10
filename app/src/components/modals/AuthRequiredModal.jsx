import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Lock, Send, RefreshCw } from 'lucide-react';
import { tr } from '../../i18n/locale';
import { useInterfaceLocale } from '../../i18n/useInterfaceLocale';
import { useAuthStore } from '../../store/useAuthStore';

export const AuthRequiredModal = ({ isOpen, onClose, title = tr('Вход в аккаунт') }) => {
  useInterfaceLocale();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [emailLoading, setEmailLoading] = useState(false);
  const [recovery, setRecovery] = useState(false);
  const dialog = useRef(null);
  const { isPolling, isStarting, authError, authUrl, startTelegramLinking, startGoogleLogin, checkPendingSession,
    loginWithEmailPassword, showTelegramPrompt, linkProvider, startPasswordRecovery, cancelPendingAuth } = useAuthStore();
  const busy = emailLoading || isStarting || isPolling;
  useEffect(() => {
    if (!isOpen) return;
    const previous = document.activeElement;
    dialog.current?.focus();
    return () => { previous?.focus(); };
  }, [isOpen]);
  const close = () => {
    if (emailLoading || isStarting) return;
    cancelPendingAuth();
    useAuthStore.setState({ showTelegramPrompt: false });
    setPassword('');
    setRecovery(false);
    onClose();
  };
  const onKeyDown = (event) => {
    if (event.key === 'Escape') close();
    if (event.key !== 'Tab') return;
    const controls = [...dialog.current.querySelectorAll('button:not(:disabled), input:not(:disabled)')];
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first?.focus();
    }
  };
  if (!isOpen) return null;
  const buttonStyle = { width: '100%', padding: '13px', borderRadius: 14, fontWeight: 650,
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 };
  const emailSubmit = async (register) => {
    setEmailLoading(true);
    const result = await loginWithEmailPassword(email, password, register);
    if (result.success) setPassword('');
    setEmailLoading(false);
  };
  return <AnimatePresence>
    <div className="settings-overlay" onClick={close} style={{ zIndex: 1100, overflowY: 'auto' }}>
      <motion.div initial={{ opacity: 0, scale: .94 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: .94 }}
        ref={dialog} role="dialog" aria-modal="true" aria-labelledby="auth-title" tabIndex={-1} onKeyDown={onKeyDown}
        className="settings-modal" onClick={(event) => event.stopPropagation()} style={{ maxWidth: 380, maxHeight: '90dvh', overflowY: 'auto', margin: 'auto', padding: '24px 20px', textAlign: 'center' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '.8rem', color: '#94a3b8', textTransform: 'uppercase' }}>{tr('Авторизация')}</span>
          <button className="close-btn" onClick={close} disabled={emailLoading || isStarting} aria-label={tr('Закрыть')}><X size={20} /></button>
        </div>
        <div style={{ margin: '14px auto', width: 56, height: 56, borderRadius: 18, display: 'grid', placeItems: 'center', background: 'rgba(168,85,247,.2)' }}><Lock color="#d8b4fe" /></div>
        <h3 id="auth-title" style={{ color: 'white', margin: '0 0 8px' }}>{showTelegramPrompt ? tr('Защитите доступ к аккаунту') : recovery ? tr('Восстановление пароля') : title}</h3>
        <p style={{ color: '#94a3b8', lineHeight: 1.5, fontSize: '.9rem' }}>{showTelegramPrompt
          ? tr('Добавьте Telegram к этому аккаунту: через него можно восстановить пароль и получать напоминания о занятиях. Настройки напоминаний доступны в профиле.')
          : recovery ? tr('Войдите через Telegram или Google, ранее привязанный к этому аккаунту. Затем задайте новый пароль в профиле. Если ничего не привязано, восстановление без писем недоступно.')
          : tr('Выберите способ входа. После привязки Telegram и Google будут вести к одному аккаунту и тем же колодам.')}</p>
        {authError && <p style={{ color: '#f87171', fontSize: '.82rem' }}>{authError}</p>}
        {isPolling && <div style={{ margin: '12px 0', padding: 12, borderRadius: 12, color: '#e9d5ff', background: 'rgba(168,85,247,.12)', fontSize: '.84rem' }}>
          <RefreshCw size={16} className="spin" style={{ verticalAlign: 'middle', marginRight: 8 }} />{tr('Подтвердите вход в открывшемся окне и вернитесь в приложение.')}
        </div>}
        {isPolling && authUrl && <a href={authUrl} target="_blank" rel="noopener noreferrer"
          style={{ display: 'block', marginTop: 6, padding: '10px 14px', borderRadius: 10, textAlign: 'center',
            background: 'rgba(168,85,247,.15)', color: '#d8b4fe', fontSize: '.84rem', textDecoration: 'none' }}>
          {tr('Окно не открылось? Нажмите здесь ↗')}
        </a>}
        <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
          <button className="btn btn-primary" disabled={busy} onClick={() => showTelegramPrompt ? linkProvider('telegram') : recovery ? startPasswordRecovery('telegram') : startTelegramLinking()} style={buttonStyle}><Send size={18} />{showTelegramPrompt ? tr('Добавить Telegram') : tr('Войти через Telegram')}</button>
          {!showTelegramPrompt && <button className="btn btn-secondary" disabled={busy} onClick={() => recovery ? startPasswordRecovery('google') : startGoogleLogin()} style={{ ...buttonStyle, borderColor: 'rgba(255,255,255,.22)' }}><b style={{ fontSize: '1.1rem' }}>G</b>{tr('Войти через Google')}</button>}
          {isPolling && <button className="btn btn-secondary" onClick={checkPendingSession} style={buttonStyle}>{tr('Я подтвердил, продолжить')}</button>}
        </div>
        {!showTelegramPrompt && !recovery && <form onSubmit={(event) => { event.preventDefault(); if (!busy && password.length >= 12) emailSubmit(false); }} style={{ display: 'grid', gap: 8, marginTop: 18, textAlign: 'left' }}>
          <span style={{ color: '#94a3b8', fontSize: '.82rem' }}>{tr('Или войдите по email и паролю')}</span>
          <input aria-label={tr('Email')} type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" style={{ padding: 12, borderRadius: 10 }} />
          <input aria-label={tr('Пароль')} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={tr('Пароль минимум 12 символов')} style={{ padding: 12, borderRadius: 10 }} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" className="btn btn-secondary" disabled={busy || !email || password.length < 12} style={{ flex: 1, minHeight: 44 }}>{tr('Войти')}</button>
            <button type="button" className="btn btn-secondary" disabled={busy || !email || password.length < 12} onClick={() => emailSubmit(true)} style={{ flex: 1, minHeight: 44 }}>{tr('Создать')}</button>
          </div>
          <button type="button" disabled={busy} className="btn btn-secondary" onClick={() => { setRecovery(true); setPassword(''); }}>{tr('Забыли пароль?')}</button>
          <span style={{ color: '#94a3b8', fontSize: '.74rem', lineHeight: 1.4 }}>{tr('Письма пока не отправляются. Проверьте адрес и добавьте резервный способ входа после регистрации.')}</span>
        </form>}
        {recovery && <button className="btn btn-secondary" disabled={busy} onClick={() => setRecovery(false)}>{tr('Назад ко входу')}</button>}
        <button className="btn-secondary btn-full" disabled={emailLoading || isStarting} onClick={close} style={{ marginTop: 14, padding: 10, borderRadius: 12 }}>{tr('Позже')}</button>
      </motion.div>
    </div>
  </AnimatePresence>;
};
