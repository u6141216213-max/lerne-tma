import { tr } from '../../i18n/locale';
import { useInterfaceLocale } from '../../i18n/useInterfaceLocale';
import React, { useState, useEffect } from 'react';
import { User, Mail, Send, BarChart2, Sparkles, Link as LinkIcon, Copy, ExternalLink, Trash2, LogOut, KeyRound } from 'lucide-react';
import { useUiStore } from '../../store/useUiStore';
import { useAuthStore } from '../../store/useAuthStore';
import { useDeckStore } from '../../store/useDeckStore';
import api from '../../services/api';
import { isOfflineMode, db } from '../../services/localDb';
import { syncService } from '../../services/syncService';
import { openExternalLink, closeApp } from '../../utils/platform';
import { resetUserSession, getUserId } from '../../utils/auth';

export const ProfileTab = ({ userId }) => {
  useInterfaceLocale();
  const { userProfile, setUserProfile, showToast, setSettingsTab } = useUiStore();
  const validInitialName = (userProfile?.first_name && userProfile.first_name !== 'Пользователь') 
    ? userProfile.first_name 
    : '';
  const [name, setName] = useState(validInitialName);
  const [email, setEmail] = useState(userProfile?.email || '');
  const [phone, setPhone] = useState(userProfile?.phone || '');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginEmail, setLoginEmail] = useState('');
  const [passwordConfirmation, setPasswordConfirmation] = useState('');
  const [savingPassword, setSavingPassword] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (userProfile?.first_name && userProfile.first_name !== 'Пользователь') {
      setName(userProfile.first_name);
    }
    if (userProfile?.email) setEmail(userProfile.email);
    if (userProfile?.phone) setPhone(userProfile.phone);
  }, [userProfile]);

  const personalLink = userId || userProfile?.user_id || getUserId() ? window.location.origin : '';

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await api.post('/auth/sync', {
        first_name: name,
        email: email,
        phone: phone,
        is_guest: userProfile?.is_guest
      });
      
      if (res.data.status === 'ok') {
        const updatedProfile = { ...(userProfile || {}), first_name: name, email: email, phone: phone };
        setUserProfile(updatedProfile);
        localStorage.setItem('lerne_user_profile', JSON.stringify(updatedProfile));
        showToast(tr("Профиль обновлен!"), "success");
      }
    } catch {
      showToast(tr("Ошибка при сохранении"));
    } finally {
      setIsSaving(false);
    }
  };

  const { 
    isPolling,
    startTelegramLinking,
    checkPendingSession,
    linkProvider,
    linkEmailPassword,
    isStarting,
    authMethods,
    passwordSettings,
    refreshAuthMethods,
    startPasswordRecovery,
    cancelPendingAuth
  } = useAuthStore();

  useEffect(() => { if (userProfile && !userProfile.is_guest) refreshAuthMethods(); }, [userProfile, refreshAuthMethods]);
  const credentialEmail = passwordSettings?.email || loginEmail;
  const savePassword = async (event) => {
    event.preventDefault();
    if (savingPassword || loginPassword !== passwordConfirmation) return;
    setSavingPassword(true);
    try {
      const result = await linkEmailPassword(credentialEmail, loginPassword);
      if (result.success) { setLoginPassword(''); setPasswordConfirmation(''); }
    } finally { setSavingPassword(false); }
  };

  return (
    <div className="profile-tab">
      <h3>{tr("Ваш профиль")}</h3>
      <p className="tab-description">
        {userProfile?.is_guest && !userProfile?.first_name 
          ? (isPolling ? tr("Ожидание подтверждения в Telegram...") : tr("Вы используете гостевой режим. Привяжите Telegram для сохранения прогресса."))
          : tr("Ваш профиль настроен.")}
      </p>

      <div className="profile-form">
        <div className="form-group">
          <label><User size={14} />{' '}{tr("Имя")}</label>
          <input 
            type="text" 
            value={name} 
            onChange={(e) => setName(e.target.value)} 
            placeholder={tr("Введите ваше имя")}
          />
        </div>

        <div className="form-group">
          <label><Mail size={14} /> Email</label>
          <input 
            type="email" 
            value={email} 
            onChange={(e) => setEmail(e.target.value)} 
            placeholder="example@mail.com"
          />
        </div>

        <div className="form-group">
          <label>{tr("📱 Телефон")}</label>
          <input 
            type="text" 
            value={phone} 
            onChange={(e) => setPhone(e.target.value)} 
            placeholder="+7 (900) 000-00-00"
          />
        </div>

        {!userProfile?.is_guest && userProfile?.username && (
          <div className="form-group">
            <label><Send size={14} /> Telegram</label>
            <div className="telegram-contact-display">
              <a href={`https://t.me/${userProfile.username}`} target="_blank" rel="noopener noreferrer">
                @{userProfile.username}
              </a>
            </div>
          </div>
        )}

        <button 
          className="btn btn-primary" 
          onClick={handleSave} 
          disabled={isSaving}
        >
          {isSaving ? tr("Сохранение...") : tr("Сохранить изменения")}
        </button>

        {/* SRS Analytics Shortcut */}
        <div className="link-telegram-section glass" style={{ marginTop: '16px', border: '1px solid rgba(168, 85, 247, 0.25)', background: 'linear-gradient(135deg, rgba(168, 85, 247, 0.05), rgba(59, 130, 246, 0.03))' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
            <h4 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.9rem' }}>
              <BarChart2 size={16} color="#a855f7" />{tr("Статистика памяти и SRS")}{' '}</h4>
            <span style={{ fontSize: '0.7rem', padding: '2px 8px', borderRadius: '10px', background: 'rgba(168, 85, 247, 0.15)', color: '#c084fc', fontWeight: 600 }}>{tr("Вкладка SRS")}</span>
          </div>
          <p style={{ fontSize: '0.78rem', color: '#94a3b8', margin: '0 0 10px 0' }}>{tr("Статистика долгосрочной памяти, прогноз повторений и расширенные настройки интервалов доступны в отдельной вкладке.")}{' '}</p>
          <button
            type="button"
            className="btn btn-primary"
            style={{ width: '100%', background: 'linear-gradient(135deg, #a855f7, #6366f1)', border: 'none', padding: '8px 12px', fontSize: '0.82rem' }}
            onClick={() => {
              setSettingsTab('srs');
              try { localStorage.setItem('lerne_last_settings_tab', 'srs'); } catch { /* ignore */ }
            }}
          >
            <Sparkles size={14} style={{ marginRight: '6px' }} />{tr("Перейти в раздел SRS")}{' '}</button>
        </div>

        {/* Personal Web Link Section */}
        {personalLink && (
          <div className="link-telegram-section glass" style={{ marginTop: '16px', border: '1px solid rgba(99, 102, 241, 0.3)', background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.08), rgba(168, 85, 247, 0.04))' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <h4 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                <LinkIcon size={18} color="#818cf8" />{tr("Персональная ссылка")}{' '}</h4>
            </div>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8', margin: '0 0 12px 0' }}>{tr("Ваша уникальная ссылка для доступа к аккаунту и колодам из любого браузера.")}{' '}</p>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(0, 0, 0, 0.25)', padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(255, 255, 255, 0.08)' }}>
              <span style={{ fontSize: '0.82rem', color: '#e2e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, userSelect: 'all' }}>
                {personalLink}
              </span>
              <button
                type="button"
                onClick={async () => {
                  try {
                    if (navigator?.clipboard?.writeText) {
                      await navigator.clipboard.writeText(personalLink);
                    } else {
                      const ta = document.createElement('textarea');
                      ta.value = personalLink;
                      document.body.appendChild(ta);
                      ta.select();
                      document.execCommand('copy');
                      document.body.removeChild(ta);
                    }
                    showToast(tr("Ссылка скопирована!"), "success");
                  } catch {
                    showToast(tr("Не удалось скопировать"), "error");
                  }
                }}
                style={{
                  background: 'rgba(168, 85, 247, 0.2)',
                  border: '1px solid rgba(168, 85, 247, 0.4)',
                  color: '#c084fc',
                  borderRadius: '8px',
                  padding: '6px 10px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  flexShrink: 0
                }}
                title={tr("Копировать ссылку")}
              >
                <Copy size={14} />
                <span>{tr("Копировать")}</span>
              </button>
              <button
                type="button"
                onClick={() => openExternalLink(personalLink)}
                style={{
                  background: 'rgba(255, 255, 255, 0.06)',
                  border: '1px solid rgba(255, 255, 255, 0.12)',
                  color: '#cbd5e1',
                  borderRadius: '8px',
                  padding: '6px 8px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0
                }}
                title={tr("Открыть в браузере")}
              >
                <ExternalLink size={14} />
              </button>
            </div>
          </div>
        )}

        {userProfile?.is_guest && (
          <div className="link-telegram-section glass">
            <h4>{tr("Синхронизация аккаунта")}</h4>
            <p>{tr("Чтобы ваш прогресс и колоды сохранялись навсегда, привяжите Telegram или войдите по коду.")}</p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '12px' }}>
              <button 
                type="button"
                className={`btn btn-telegram ${isPolling ? 'polling' : ''}`}
                onClick={startTelegramLinking}
              >
                <Send size={16} /> {isPolling ? tr("Ожидание подтверждения...") : tr("Привязать через Telegram")}
              </button>
              {isPolling && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => checkPendingSession()}
                  style={{ fontSize: '0.85rem' }}
                >{tr("Я подтвердил в боте")}{' '}</button>
              )}
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => useUiStore.getState().setIsAuthModalOpen(true, tr("Вход в аккаунт"))}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, fontSize: '0.85rem' }}
              >
                <KeyRound size={15} />{' '}{tr("Войти по 6-значному коду")}{' '}</button>
            </div>
          </div>
        )}

        {userProfile && !userProfile.is_guest && (
          <div className="link-telegram-section glass" style={{ marginTop: '15px' }}>
            <h4>{tr("Способы входа")}</h4>
            <p>{tr("Привяжите второй способ сейчас — затем Google и Telegram будут открывать те же колоды и прогресс.")}</p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
              {!authMethods && <button type="button" className="btn btn-secondary" onClick={refreshAuthMethods}>{tr('Загрузить способы входа')}</button>}
              {authMethods?.includes('google') ? <span>Google ✓</span> : <button type="button" disabled={!authMethods || isStarting || isPolling} className="btn btn-secondary" onClick={() => linkProvider('google')}>
                G · {tr("Добавить Google")}
              </button>}
              {authMethods?.includes('telegram') ? <span>Telegram ✓</span> : <button type="button" disabled={!authMethods || isStarting || isPolling} className="btn btn-telegram" onClick={() => linkProvider('telegram')}>
                <Send size={16} /> {tr("Добавить Telegram")}
              </button>}
              <p>{tr('Пароль можно восстановить только через заранее привязанный Telegram или Google. Письма пока не отправляются.')}</p>
              {passwordSettings?.email && !passwordSettings.can_reset_password && <>
                <p>{tr('Войдите заново через привязанный Telegram или Google и повторите в течение 5 минут.')}</p>
                {['telegram', 'google'].filter(provider => authMethods?.includes(provider)).map(provider =>
                  <button key={provider} type="button" className="btn btn-secondary" disabled={isStarting || isPolling} onClick={() => startPasswordRecovery(provider)}>
                    {provider === 'telegram' ? tr('Войти через Telegram') : tr('Войти через Google')}
                  </button>)}
              </>}
              <form onSubmit={savePassword} style={{ display: 'grid', gap: 8 }}>
                <input aria-label={tr("Email для входа")} type="email" autoComplete="username" required readOnly={Boolean(passwordSettings?.email)} value={credentialEmail} onChange={(event) => setLoginEmail(event.target.value)} placeholder="name@example.com" />
                <input aria-label={tr("Новый пароль")} type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} placeholder={tr("Пароль минимум 12 символов")} />
                <input aria-label={tr('Повторите пароль')} type="password" autoComplete="new-password" required value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} placeholder={tr('Повторите пароль')} />
                {passwordConfirmation && passwordConfirmation !== loginPassword && <span role="status">{tr('Пароли не совпадают')}</span>}
                <button type="submit" className="btn btn-secondary" disabled={!passwordSettings || savingPassword || isPolling || isStarting || !credentialEmail || loginPassword.length < 12 || loginPassword !== passwordConfirmation || (Boolean(passwordSettings.email) && !passwordSettings.can_reset_password)}>
                  {passwordSettings?.email ? tr('Сменить пароль и завершить другие сеансы') : tr("Сохранить email и пароль")}
                </button>
              </form>
              {isPolling && <button type="button" className="btn btn-secondary" onClick={checkPendingSession}>
                {tr("Я подтвердил, продолжить")}
              </button>}
              {isPolling && <button type="button" className="btn btn-secondary" onClick={cancelPendingAuth}>{tr('Отмена')}</button>}
            </div>
          </div>
        )}

        {isOfflineMode() && (
          <div className="link-telegram-section glass" style={{ marginTop: '15px' }}>
            <h4>{tr("Локальная база данных")}</h4>
            <p>{tr("Данные сохраняются на вашем устройстве. Синхронизируйте их с сервером при наличии сети.")}</p>
            <button 
              className="btn btn-primary"
              onClick={async () => {
                showToast(tr("Синхронизация..."));
                const res = await syncService.sync();
                if (res.success) {
                  showToast(tr("Синхронизация успешно завершена!"), "success");
                  const { fetchDecks } = useDeckStore.getState();
                  fetchDecks(true);
                } else {
                  showToast(tr("Сбой синхронизации: {{p0}}", { p0: res.reason || tr("нет сети") }));
                }
              }}
            >{tr("Синхронизировать сейчас")}{' '}</button>
          </div>
        )}

        {/* Account Management & Reset */}
        <div className="link-telegram-section glass" style={{ marginTop: '20px', border: '1px solid rgba(239, 68, 68, 0.25)', background: 'rgba(239, 68, 68, 0.04)' }}>
          <h4 style={{ margin: '0 0 6px 0', color: '#f87171', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Trash2 size={16} />{tr("Управление данными и аккаунтом")}{' '}</h4>
          <p style={{ fontSize: '0.8rem', color: '#94a3b8', margin: '0 0 12px 0' }}>{tr("Вы можете сбросить текущую сессию в чистый гостевой режим или полностью удалить все данные с сервера.")}{' '}</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ width: '100%', fontSize: '0.85rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
              onClick={() => {
                if (window.confirm(tr("Сбросить сессию и войти как новый гость без регистрации?"))) {
                  resetUserSession();
                }
              }}
            >
              <LogOut size={15} />{tr("Сбросить сессию / Войти как гость")}{' '}</button>

            <button
              type="button"
              className="btn btn-secondary"
              style={{ width: '100%', fontSize: '0.85rem', color: '#fca5a5', background: 'rgba(239, 68, 68, 0.12)', borderColor: 'rgba(239, 68, 68, 0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
              onClick={async () => {
                if (window.confirm(tr("ВНИМАНИЕ! Это навсегда удалит все ваши колоды, карточки и прогресс с сервера. Вы уверены?"))) {
                  try {
                    await api.delete('/auth/account');
                    
                    // 1. Полная очистка локальной базы данных IndexedDB (Dexie)
                    try {
                      if (db && typeof db.delete === 'function') {
                        await db.delete();
                      }
                    } catch (dbErr) {
                      console.warn("Failed to clear local IndexedDB:", dbErr);
                    }

                    // 2. Полная очистка localStorage и sessionStorage
                    try {
                      localStorage.clear();
                      sessionStorage.clear();
                    } catch (storageErr) {
                      console.warn("Failed to clear storage:", storageErr);
                    }

                    showToast(tr("Аккаунт и все данные удалены. Закрываем..."), "info");
                    
                    // 3. Закрываем приложение
                    setTimeout(() => {
                      closeApp();
                      if (typeof window !== 'undefined') {
                        try {
                          window.close();
                        } catch { /* ignore */ }
                        window.location.href = 'about:blank';
                      }
                    }, 800);

                  } catch (err) {
                    showToast(err?.response?.data?.detail || tr("Ошибка при удалении"), "error");
                  }
                }
              }}
            >
              <Trash2 size={15} />{tr("Удалить аккаунт и все данные")}{' '}</button>
          </div>
        </div>
      </div>
    </div>
  );
};

