import { tr } from '../../i18n/locale';
import { useInterfaceLocale } from '../../i18n/useInterfaceLocale';
import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Users, UserPlus, Link, Copy, Check, Trash2, Send, Trophy, Sparkles } from 'lucide-react';
import { useUiStore } from '../../store/useUiStore';
import { useDeckStore } from '../../store/useDeckStore';
import { useCollaborativeStore } from '../../store/useCollaborativeStore';
import { executeShare } from '../../utils/share';
import api from '../../services/api';

export const CollaboratorsModal = () => {
  useInterfaceLocale();
  const { isCollaboratorsModalOpen, setIsCollaboratorsModalOpen, collaboratorsTarget, showToast } = useUiStore();
  const { fetchDecks, fetchFolders } = useDeckStore();
  const { fetchCollaborators, addCollaborator, updateCollaboratorRole, removeCollaborator, removeAllCollaborators, fetchGroupProgress } = useCollaborativeStore();

  const [activeTab, setActiveTab] = useState('members'); // 'members' | 'leaderboard'
  const [collaborators, setCollaborators] = useState([]);
  const [userRole, setUserRole] = useState('viewer');
  const [loading, setLoading] = useState(false);
  const [userIdentifier, setUserIdentifier] = useState('');
  const [addingUser, setAddingUser] = useState(false);
  const [copiedLink, setCopiedLink] = useState(false);
  const [shareId, setShareId] = useState(null);
  const [groupProgress, setGroupProgress] = useState(null);
  const [confirmTarget, setConfirmTarget] = useState(null); // { action: 'remove_user' | 'close_all', userId, userName }

  const targetType = collaboratorsTarget?.type || 'folder';
  const targetId = collaboratorsTarget?.id;
  const targetName = collaboratorsTarget?.name || '';

  const isOwner = userRole === 'owner';

  useEffect(() => {
    if (isCollaboratorsModalOpen && targetId) {
      loadCollaboratorsData();
      getShareId();
    } else {
      setConfirmTarget(null);
    }
  }, [isCollaboratorsModalOpen, targetId, targetType]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadCollaboratorsData = async () => {
    setLoading(true);
    try {
      const data = await fetchCollaborators(targetType, targetId);
      setCollaborators(data.collaborators || []);
      setUserRole(data.user_role || 'viewer');
    } catch (err) {
      console.error("Error loading collaborators:", err);
      showToast(tr("Ошибка загрузки соавторов"));
    } finally {
      setLoading(false);
    }

    if (targetType === 'folder') {
      try {
        const prog = await fetchGroupProgress(targetId);
        setGroupProgress(prog);
      } catch (err) {
        console.warn("Group progress unavailable:", err);
        // Non-critical: don't show error toast for missing progress
      }
    }
  };

  const getShareId = async () => {
    try {
      const res = await api.post(`/share/generate/${targetType}/${targetId}`);
      if (res.data && res.data.share_id) {
        setShareId(res.data.share_id);
      }
    } catch (err) {
      console.error("Error generating share_id:", err);
    }
  };

  if (!isCollaboratorsModalOpen || !targetId) return null;

  const inviteLink = shareId 
    ? `https://t.me/LerneDeutsch287_bot?startapp=collab_${shareId}` 
    : '';

  const handleSendTelegram = async () => {
    if (!inviteLink) return;
    try {
      await executeShare({
        title: tr("Совместный доступ: «{{p0}}»", { p0: targetName }),
        text: tr("Присоединяйся к совместному обучению в Lerne!", {  }),
        link: inviteLink
      });
      showToast(tr("Открываем окно отправки Telegram..."), "info");
    } catch (err) {
      console.error("Error sending share link:", err);
    }
  };

  const handleCopyLink = () => {
    if (!inviteLink) return;
    navigator.clipboard.writeText(inviteLink);
    setCopiedLink(true);
    showToast(tr("Ссылка приглашения скопирована!"), "success");
    setTimeout(() => setCopiedLink(false), 3000);
  };

  const handleAddUser = async (e) => {
    e.preventDefault();
    if (!userIdentifier.trim()) return;
    setAddingUser(true);
    try {
      await addCollaborator(targetType, targetId, userIdentifier.trim(), 'viewer');
      showToast(tr("Участник добавлен (Только чтение)"), "success");
      setUserIdentifier('');
      await loadCollaboratorsData();
      await fetchDecks(true);
      await fetchFolders();
    } catch (err) {
      const msg = err.response?.data?.detail || tr("Не удалось добавить пользователя");
      showToast(msg, "error");
    } finally {
      setAddingUser(false);
    }
  };

  const handleRoleChange = async (collaboratorUserId, newRole, canEditAudio = false) => {
    try {
      await updateCollaboratorRole(targetType, targetId, collaboratorUserId, newRole, canEditAudio);
      showToast(tr("Роль участника обновлена"), "success");
      await loadCollaboratorsData();
      await fetchDecks(true);
      await fetchFolders();
    } catch (err) {
      console.error("Error updating role:", err);
      showToast(tr("Ошибка при изменении роли"), "error");
    }
  };

  const promptRemove = (collaborator) => {
    setConfirmTarget({
      action: 'remove_user',
      userId: collaborator.user_id,
      userName: collaborator.first_name || collaborator.username || tr("Пользователь #{{p0}}", { p0: collaborator.user_id })
    });
  };

  const promptCloseAll = () => {
    setConfirmTarget({ action: 'close_all' });
  };

  const executeConfirm = async () => {
    if (!confirmTarget) return;
    const { action, userId } = confirmTarget;
    setConfirmTarget(null);

    if (action === 'remove_user') {
      try {
        await removeCollaborator(targetType, targetId, userId);
        showToast(tr("Участник удален"), "success");
        await loadCollaboratorsData();
        await fetchDecks(true);
        await fetchFolders();
      } catch (err) {
        console.error("Error removing collaborator:", err);
        showToast(tr("Ошибка при удалении"), "error");
      }
    } else if (action === 'close_all') {
      try {
        await removeAllCollaborators(targetType, targetId);
        showToast(tr("Совместный доступ закрыт"), "info");
        await fetchDecks(true);
        await fetchFolders();
        setIsCollaboratorsModalOpen(false);
      } catch (err) {
        console.error("Error closing sharing:", err);
        showToast(tr("Ошибка при закрытии доступа"), "error");
      }
    }
  };

  return (
    <AnimatePresence>
      <div 
        className="modal-overlay" 
        onClick={() => setIsCollaboratorsModalOpen(false)}
        style={{
          position: 'fixed',
          top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0, 0, 0, 0.75)',
          backdropFilter: 'blur(8px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
          padding: '16px',
          overflowY: 'auto',
          overscrollBehavior: 'contain',
          WebkitOverflowScrolling: 'touch'
        }}
      >
        <motion.div 
          initial={{ scale: 0.9, opacity: 0, y: 20 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          exit={{ scale: 0.9, opacity: 0, y: 20 }}
          transition={{ type: 'spring', damping: 25, stiffness: 300 }}
          className="modal-content glass"
          onClick={(e) => e.stopPropagation()}
          style={{
            width: '100%',
            maxWidth: '480px',
            maxHeight: 'calc(100dvh - 32px)',
            margin: 'auto',
            borderRadius: '24px',
            background: 'linear-gradient(145deg, rgba(26, 26, 46, 0.95), rgba(15, 15, 30, 0.98))',
            border: '1px solid rgba(139, 92, 246, 0.3)',
            boxShadow: '0 20px 50px rgba(0,0,0,0.6), 0 0 30px rgba(139, 92, 246, 0.2)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            position: 'relative',
            boxSizing: 'border-box'
          }}
        >
          {/* Custom In-Modal Confirm Overlay */}
          {confirmTarget && (
            <div style={{
              position: 'absolute',
              top: 0, left: 0, right: 0, bottom: 0,
              background: 'rgba(15, 15, 30, 0.96)',
              backdropFilter: 'blur(10px)',
              zIndex: 100,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '24px',
              textAlign: 'center'
            }}>
              <div style={{
                width: '56px', height: '56px', borderRadius: '50%',
                background: 'rgba(239, 68, 68, 0.15)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: '#f87171', marginBottom: '16px'
              }}>
                <Trash2 size={28} />
              </div>
              <h4 style={{ margin: '0 0 8px', color: '#fff', fontSize: '1.1rem', fontWeight: 700 }}>
                {confirmTarget.action === 'close_all' 
                  ? tr("Закрыть совместный доступ?") 
                  : tr("Удалить {{p0}}?", { p0: confirmTarget.userName })}
              </h4>
              <p style={{ margin: '0 0 20px', color: 'rgba(255,255,255,0.6)', fontSize: '0.88rem', lineHeight: 1.4, maxWidth: '280px' }}>
                {confirmTarget.action === 'close_all'
                  ? tr("Все соавторы потеряют доступ к этой папке/колоде.")
                  : tr("Пользователь больше не сможет просматривать или редактировать этот элемент.")}
              </p>
              <div style={{ display: 'flex', gap: '12px', width: '100%', maxWidth: '280px' }}>
                <button
                  onClick={() => setConfirmTarget(null)}
                  style={{
                    flex: 1,
                    background: 'rgba(255,255,255,0.08)',
                    border: '1px solid rgba(255,255,255,0.15)',
                    borderRadius: '12px',
                    padding: '10px',
                    color: '#fff',
                    fontWeight: 600,
                    fontSize: '0.88rem',
                    cursor: 'pointer'
                  }}
                >{tr("Отмена")}{' '}</button>
                <button
                  onClick={executeConfirm}
                  style={{
                    flex: 1,
                    background: 'linear-gradient(135deg, #ef4444, #dc2626)',
                    border: 'none',
                    borderRadius: '12px',
                    padding: '10px',
                    color: '#fff',
                    fontWeight: 600,
                    fontSize: '0.88rem',
                    cursor: 'pointer',
                    boxShadow: '0 4px 15px rgba(239, 68, 68, 0.3)'
                  }}
                >
                  {confirmTarget.action === 'close_all' ? tr("Да, закрыть") : tr("Да, удалить")}
                </button>
              </div>
            </div>
          )}

          {/* Header */}
          <div style={{
            padding: '20px 24px 16px',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexShrink: 0
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div style={{
                width: '42px',
                height: '42px',
                borderRadius: '12px',
                background: 'linear-gradient(135deg, rgba(139, 92, 246, 0.3), rgba(99, 102, 241, 0.2))',
                border: '1px solid rgba(139, 92, 246, 0.4)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#a78bfa'
              }}>
                <Users size={22} />
              </div>
              <div>
                <h3 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>{tr("Совместный доступ")}{' '}</h3>
                <p style={{ margin: '2px 0 0', fontSize: '0.82rem', color: 'rgba(255, 255, 255, 0.5)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '260px' }}>
                  {targetName}
                </p>
              </div>
            </div>

            <button 
              onClick={() => setIsCollaboratorsModalOpen(false)}
              style={{
                background: 'rgba(255,255,255,0.06)',
                border: 'none',
                color: 'rgba(255,255,255,0.6)',
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer'
              }}
            >
              <X size={18} />
            </button>
          </div>

          {/* Navigation Tabs (if folder) */}
          {targetType === 'folder' && (
            <div style={{
              display: 'flex',
              padding: '8px 24px 0',
              gap: '12px',
              borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
              flexShrink: 0
            }}>
              <button 
                onClick={() => setActiveTab('members')}
                style={{
                  padding: '8px 16px',
                  background: 'none',
                  border: 'none',
                  borderBottom: activeTab === 'members' ? '2px solid #a78bfa' : '2px solid transparent',
                  color: activeTab === 'members' ? '#a78bfa' : 'rgba(255,255,255,0.5)',
                  fontWeight: activeTab === 'members' ? 700 : 500,
                  cursor: 'pointer',
                  fontSize: '0.9rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}
              >
                <Users size={16} />
                <span>{tr("Участники (")}{collaborators.length})</span>
              </button>

              <button 
                onClick={() => setActiveTab('leaderboard')}
                style={{
                  padding: '8px 16px',
                  background: 'none',
                  border: 'none',
                  borderBottom: activeTab === 'leaderboard' ? '2px solid #a78bfa' : '2px solid transparent',
                  color: activeTab === 'leaderboard' ? '#a78bfa' : 'rgba(255,255,255,0.5)',
                  fontWeight: activeTab === 'leaderboard' ? 700 : 500,
                  cursor: 'pointer',
                  fontSize: '0.9rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}
              >
                <Trophy size={16} />
                <span>{tr("Прогресс группы")}</span>
              </button>
            </div>
          )}

          {/* Body Content */}
          <div style={{ padding: '20px 24px', overflowY: 'auto', flex: 1, minHeight: 0 }}>
            {activeTab === 'members' ? (
              <>
                {/* Invite Link Banner */}
                <div style={{
                  background: 'rgba(139, 92, 246, 0.12)',
                  border: '1px solid rgba(139, 92, 246, 0.25)',
                  borderRadius: '16px',
                  padding: '14px 16px',
                  marginBottom: '20px'
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', color: '#c4b5fd', fontSize: '0.88rem', fontWeight: 600 }}>
                    <Link size={16} />
                    <span>{tr("Ссылка для присоединения")}</span>
                  </div>

                  {/* Primary Send in Telegram Button */}
                  <button 
                    onClick={handleSendTelegram}
                    disabled={!inviteLink}
                    style={{
                      width: '100%',
                      background: 'linear-gradient(135deg, #0088cc, #00a0ee)',
                      border: 'none',
                      borderRadius: '12px',
                      padding: '10px 16px',
                      color: '#fff',
                      fontWeight: 600,
                      fontSize: '0.88rem',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '8px',
                      marginBottom: '10px',
                      boxShadow: '0 4px 15px rgba(0, 136, 204, 0.3)'
                    }}
                  >
                    <Send size={16} />
                    <span>{tr("Отправить в Telegram")}</span>
                  </button>

                  {/* Copy Link Row */}
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <input 
                      type="text" 
                      readOnly 
                      value={inviteLink || tr("Генерация ссылки...")} 
                      style={{
                        flex: 1,
                        background: 'rgba(0,0,0,0.3)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: '10px',
                        padding: '8px 12px',
                        color: 'rgba(255,255,255,0.8)',
                        fontSize: '0.82rem',
                        outline: 'none'
                      }}
                    />
                    <button 
                      onClick={handleCopyLink}
                      disabled={!inviteLink}
                      style={{
                        background: copiedLink ? '#22c55e' : 'rgba(255,255,255,0.1)',
                        border: '1px solid rgba(255,255,255,0.2)',
                        borderRadius: '10px',
                        padding: '8px 14px',
                        color: '#fff',
                        fontWeight: 600,
                        fontSize: '0.85rem',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        transition: 'all 0.2s'
                      }}
                    >
                      {copiedLink ? <Check size={16} /> : <Copy size={16} />}
                      <span>{copiedLink ? tr("Скопировано") : tr("Скопировать")}</span>
                    </button>
                  </div>
                </div>

                {/* Direct User Invite Form (Only for Owner) */}
                {isOwner && (
                  <form onSubmit={handleAddUser} style={{ marginBottom: '24px' }}>
                    <label style={{ display: 'block', fontSize: '0.85rem', color: 'rgba(255,255,255,0.7)', marginBottom: '8px', fontWeight: 500 }}>{tr("Добавить участника по имя пользователя:")}{' '}</label>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <input 
                        type="text" 
                        placeholder={tr("@username или Telegram ID")}
                        value={userIdentifier}
                        onChange={(e) => setUserIdentifier(e.target.value)}
                        disabled={addingUser}
                        style={{
                          flex: 1,
                          background: 'rgba(255,255,255,0.06)',
                          border: '1px solid rgba(255,255,255,0.15)',
                          borderRadius: '12px',
                          padding: '10px 14px',
                          color: '#fff',
                          fontSize: '0.9rem',
                          outline: 'none'
                        }}
                      />
                      <button 
                        type="submit"
                        disabled={addingUser || !userIdentifier.trim()}
                        style={{
                          background: 'rgba(139, 92, 246, 0.25)',
                          border: '1px solid rgba(139, 92, 246, 0.4)',
                          borderRadius: '12px',
                          padding: '10px 16px',
                          color: '#c4b5fd',
                          fontWeight: 600,
                          fontSize: '0.88rem',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px'
                        }}
                      >
                        <UserPlus size={16} />
                        <span>{tr("Добавить")}</span>
                      </button>
                    </div>
                  </form>
                )}

                {/* Members List */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{ fontSize: '0.85rem', color: 'rgba(255,255,255,0.5)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>{tr("Список участников")}{' '}</div>

                  {loading ? (
                    <div style={{ padding: '20px', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>{tr("Загрузка...")}{' '}</div>
                  ) : collaborators.length === 0 ? (
                    <div style={{ padding: '20px', textAlign: 'center', color: 'rgba(255,255,255,0.4)', fontSize: '0.9rem' }}>{tr("Нет соавторов")}{' '}</div>
                  ) : (
                    collaborators.map((c) => (
                      <div 
                        key={c.user_id}
                        style={{
                          background: 'rgba(255, 255, 255, 0.04)',
                          border: '1px solid rgba(255, 255, 255, 0.08)',
                          borderRadius: '14px',
                          padding: '12px 16px',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: '12px'
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                          {c.photo_url ? (
                            <img src={c.photo_url} alt="avatar" style={{ width: 36, height: 36, borderRadius: '50%' }} />
                          ) : (
                            <div style={{
                              width: 36, height: 36, borderRadius: '50%',
                              background: 'linear-gradient(135deg, #6366f1, #a855f7)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center',
                              fontWeight: 700, color: '#fff', fontSize: '0.9rem'
                            }}>
                              {(c.first_name || c.username || 'U').charAt(0).toUpperCase()}
                            </div>
                          )}
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 600, color: '#fff', fontSize: '0.92rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {c.first_name || c.username || tr("Пользователь #{{p0}}", { p0: c.user_id })}
                            </div>
                            {c.username && (
                              <div style={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.4)' }}>
                                @{c.username}
                              </div>
                            )}
                          </div>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          {c.is_owner ? (
                            <span style={{
                              background: 'rgba(245, 158, 11, 0.2)',
                              color: '#fbbf24',
                              border: '1px solid rgba(245, 158, 11, 0.3)',
                              padding: '4px 10px',
                              borderRadius: '8px',
                              fontSize: '0.78rem',
                              fontWeight: 600,
                              display: 'flex',
                              alignItems: 'center',
                              gap: '4px'
                            }}>{tr("👑 Владелец")}{' '}</span>
                          ) : isOwner ? (
                            <select
                              value={c.role}
                              onChange={(e) => handleRoleChange(c.user_id, e.target.value, c.can_edit_audio)}
                              style={{
                                background: 'rgba(255, 255, 255, 0.08)',
                                border: '1px solid rgba(255, 255, 255, 0.15)',
                                color: '#fff',
                                borderRadius: '8px',
                                padding: '4px 8px',
                                fontSize: '0.8rem',
                                outline: 'none',
                                cursor: 'pointer'
                              }}
                            >
                              <option value="viewer" style={{ background: '#1e1e2e' }}>{tr("👁️ Слушатель")}</option>
                              <option value="editor" style={{ background: '#1e1e2e' }}>{tr("✏️ Редактор")}</option>
                            </select>
                          ) : (
                            <span style={{
                              background: c.role === 'editor' ? 'rgba(99, 102, 241, 0.2)' : 'rgba(255, 255, 255, 0.1)',
                              color: c.role === 'editor' ? '#818cf8' : 'rgba(255, 255, 255, 0.6)',
                              padding: '4px 10px',
                              borderRadius: '8px',
                              fontSize: '0.78rem',
                              fontWeight: 600
                            }}>
                              {c.role === 'editor' ? tr("✏️ Редактор") : tr("👁️ Слушатель")}
                            </span>
                          )}

                          {isOwner && !c.is_owner && (
                            <label style={{ display: 'flex', alignItems: 'center', gap: '4px', color: 'rgba(255,255,255,0.65)', fontSize: '0.72rem', whiteSpace: 'nowrap' }}>
                              <input
                                type="checkbox"
                                checked={Boolean(c.can_edit_audio)}
                                onChange={(e) => handleRoleChange(c.user_id, c.role, e.target.checked)}
                                aria-label={tr("Разрешить изменять аудио")}
                              />
                              {tr("Изменять аудио")}
                            </label>
                          )}

                          {isOwner && !c.is_owner && (
                            <button
                              onClick={() => promptRemove(c)}
                              style={{
                                background: 'rgba(239, 68, 68, 0.15)',
                                border: '1px solid rgba(239, 68, 68, 0.3)',
                                color: '#f87171',
                                cursor: 'pointer',
                                padding: '6px 8px',
                                borderRadius: '8px',
                                display: 'flex',
                                alignItems: 'center'
                              }}
                              title={tr("Удалить участника")}
                            >
                              <Trash2 size={15} />
                            </button>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>

                {/* Close All Collaborative Access Button */}
                {isOwner && collaborators.length > 0 && (
                  <button
                    onClick={promptCloseAll}
                    style={{
                      marginTop: '24px',
                      width: '100%',
                      background: 'rgba(239, 68, 68, 0.1)',
                      border: '1px solid rgba(239, 68, 68, 0.3)',
                      borderRadius: '14px',
                      padding: '12px 16px',
                      color: '#f87171',
                      fontWeight: 600,
                      fontSize: '0.88rem',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '8px'
                    }}
                  >
                    <Trash2 size={16} />
                    <span>{tr("Закрыть совместный доступ (удалить всех соавторов)")}</span>
                  </button>
                )}
              </>
            ) : (
              /* Group Progress Leaderboard */
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {!groupProgress ? (
                  <div style={{ padding: '20px', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>{tr("Загрузка прогресса...")}{' '}</div>
                ) : (
                  <>
                    <div style={{
                      background: 'rgba(255,255,255,0.03)',
                      borderRadius: '16px',
                      padding: '16px',
                      border: '1px solid rgba(255,255,255,0.08)',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center'
                    }}>
                      <div>
                        <div style={{ fontSize: '0.8rem', color: 'rgba(255,255,255,0.5)' }}>{tr("Всего карточек в папке")}</div>
                        <div style={{ fontSize: '1.4rem', fontWeight: 700, color: '#fff' }}>{groupProgress.total_cards}</div>
                      </div>
                      <Sparkles size={28} color="#a78bfa" />
                    </div>

                    <div style={{ fontSize: '0.85rem', color: 'rgba(255,255,255,0.5)', fontWeight: 600, marginTop: '8px' }}>{tr("🏆 Рейтинг участников")}{' '}</div>

                    {groupProgress.members?.map((m, idx) => (
                      <div 
                        key={m.user_id}
                        style={{
                          background: 'rgba(255,255,255,0.04)',
                          borderRadius: '14px',
                          padding: '12px 16px',
                          border: '1px solid rgba(255,255,255,0.08)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between'
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                          <span style={{
                            width: '24px',
                            fontWeight: 700,
                            color: idx === 0 ? '#ffd043' : idx === 1 ? '#cbd5e1' : idx === 2 ? '#b45309' : 'rgba(255,255,255,0.4)',
                            fontSize: '1rem'
                          }}>
                            #{idx + 1}
                          </span>
                          <div>
                            <div style={{ fontWeight: 600, color: '#fff', fontSize: '0.9rem' }}>
                              {m.first_name || m.username}
                            </div>
                            <div style={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.4)' }}>{tr("Выучено")}{' '}{m.mastered_cards}{' '}{tr("из")}{' '}{groupProgress.total_cards} • {m.reviews_today}{' '}{tr("сегодня")}{' '}</div>
                          </div>
                        </div>

                        <div style={{ textAlign: 'right' }}>
                          <span style={{ fontWeight: 700, color: '#a78bfa', fontSize: '1rem' }}>
                            {m.progress_percent}%
                          </span>
                        </div>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};
