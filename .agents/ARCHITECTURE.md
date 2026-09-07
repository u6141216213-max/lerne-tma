# Lerne TMA: Архитектурная карта (Architecture & Feature Topology)

> **Назначение**: Быстрая локализация кода для агента и разработчика. Перед поиском или внесением изменений определите фичу по таблице ниже и сразу переходите к целевым модулям.

---

## 1. Сквозная матрица фич (Intent-to-Code Matrix)

Каждая строка связывает бизнес-фичу со всей цепочкой файлов от пользовательского интерфейса до базы данных:

| Подсистема / Фича | Frontend UI (`app/src/components/`) | Client State & Storage (`app/src/store/`, `services/`) | Backend Router (`api/routers/`) | Backend Service / Logic (`api/services/`) | DB Model (`api/models.py`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Колоды (Decks)** | `deckgrid/DeckGrid.jsx`, `deckgrid/DeckCard.jsx` | `useDeckStore.js` (`createDeckSlice.js`), `offlineApi.js`, `localDb.js` | `decks.py` | `decks.py` | `TMA_Deck` |
| **Папки (Folders)** | `deckgrid/FolderCard.jsx`, `modals/FolderModal.jsx` | `useDeckStore.js` (`createFolderSlice.js`), `offlineApi.js` | `folders.py` | `folders.py` | `TMA_Folder` |
| **Карточки (Cards)** | `CardList.jsx`, `modals/CardEditModal.jsx` | `useDeckStore.js` (`createDeckSlice.js`), `offlineApi.js` | `cards.py` | `cards.py` | `TMA_Card` |
| **Обучение & SRS** | `study/StudyView.jsx`, `study/CardView.jsx` | `useSessionStore.js`, `offlineApi.js` | `study.py` | `study.py`, `srs.py` | `TMAProgress`, `TMAReviewHistory` |
| **Offline-First & Синхронизация** | `common/SyncIndicator.jsx`, `offlineUi.js` | `localDb.js` (Dexie), `offlineApi.js`, `syncService.js` | `sync.py` | `sync_service.py`, `offline_sync.py` | `TMAOfflineBatch` |
| **AI-генерация & Промпты** | `modals/AiGenerateModal.jsx`, `study/AiExplainer.jsx` | `useSessionStore.js` | `ai.py` | `ai_service.py`, `prompt_builders.py`, `ai_clients.py` | `TMAUserPrompt`, `TMACustomPrompt` |
| **Озвучка & Медиа (TTS)** | `utils/audio.js`, `mediaCache.js` | `mediaCache.js` | `media.py` | `media.py` (edge-tts / кэш) | `TMAMedia` |
| **LID (Языковой классификатор)** | `lid/LidClassifier.jsx`, `LanguageSelectionModal.jsx` | `useLidStore.js`, `useLanguageStore.js`, `lidFolderManager.js` | `lid.py` | `classifier/`, `language_config.py` | `TMA_Folder.target_language` |
| **Шеринг колод & Импорт** | `modals/ShareModal.jsx`, `modals/ImportModal.jsx` | `useDeckStore.js` (`createShareSlice.js`) | `share.py` | `sharing_service.py` | `TMA_Deck.share_id`, `TMA_Folder.share_id` |
| **Коллаборация (Co-op)** | `collaborative/CollaborativeHub.jsx` | `useCollaborativeStore.js` | `collaborative.py` | `collaborative_service.py` | `TMA_Collaborator` |
| **Корзина (Trash)** | `TrashManager.jsx` | `useDeckStore.js` (`createTrashSlice.js`) | `trash.py` | `trash.py` | `is_deleted=True` (Soft delete) |
| **Авторизация v2: вход и привязки** | `modals/AuthRequiredModal.jsx`, `settings/ProfileTab.jsx` | `useAuthStore.js`, `utils/auth.js`, `services/api.js`; Bearer | `auth_v2.py`, `bot.py`, строгая `dependencies/auth.py`; старый кодовый вход закрыт | `api/auth/providers.py`, `service.py`, `transactions.py` | `TMAAuthAccount`, `TMAAuthIdentity`, `TMAAuthSession`, `TMAAuthToken`, `TMAAuthProof`, `TMAAuthChallenge`; миграции 76–77 |
| **Email/пароль и восстановление без писем** | Предложение Telegram после регистрации; восстановление через вход привязанным провайдером и профиль | `useAuthStore.js`: настройки пароля отдельно от полей профиля | `auth_v2.py`: `email-password/register`, `login`, `link`, `set`, `password-settings` | `api/auth/service.py`: Argon2id, throttling, свежая social-сессия, отзыв остальных сессий | `TMAAuthPassword`, `TMAAuthPasswordThrottle`; миграция 78; provenance сессии — 79 |
| **Настройки & Профиль** | `settings/SettingsView.jsx` | `useSettingsStore.js` | `settings.py` | `reminder_service.py` | `TMASetting` |
| **Сквозной поиск (Search)** | `common/SearchBar.jsx`, `deckgrid/DeckGrid.jsx` | `search.js`, `offlineApi.js` | `cards.py` (`/search`) | `cards.py` (`search_all_in_scope`) | `TMA_Card`, `TMA_Deck`, `TMA_Folder` |

---

## 2. Архитектурные инварианты (Architecture Rules & Data Flow)

### Клиентский поток данных (Frontend)
1. **Offline-First по умолчанию**: Все операции создания, изменения и удаления данных **сначала** фиксируются в Dexie (IndexedDB) через `offlineApi.js`, и только потом отправляются/планируются на сервер через `syncService.js`.
2. **Слайсы Zustand (`useDeckStore.js`)**: Не раздувайте `useDeckStore.js` напрямую. Стейт разделен на срезы (`slices/createDeckSlice.js`, `createFolderSlice.js`, `createLibrarySlice.js`, `createShareSlice.js`, `createTrashSlice.js`).
3. **Платформенные функции**: Действующие адаптеры Telegram/Capacitor находятся в `app/src/utils/platform.js`, CloudStorage — в `app/src/utils/auth.js`. Файла `services/telegram.js` сейчас нет. `initDataUnsafe` не является серверным доказательством личности.

### Серверный поток данных (Backend)
1. **Тонкие роутеры (`api/routers/`)**: Роутеры только принимают HTTP-запрос, валидируют входные Pydantic-схемы, вызывают соответствующий метод из `api/services/` и возвращают результат.
2. **Бизнес-логика в сервисах (`api/services/`)**: Все вычисления SRS, парсинг карточек, сборка батчей синхронизации изолированы в сервисах.
3. **ORM & База данных**: Используется Peewee ORM (`api/models.py`). Миграции схемы описываются явно в `api/migrations.py` с проверкой существования колонок/индексов.
4. **Auth v2**: `TMAUser.user_id` — ID аккаунта, provider identities хранятся отдельно; совпадение email не объединяет аккаунты. Миграции 76–79 запускаются только явным `scripts/migrate_auth.py --apply` с отдельной конфигурацией цели, не при старте API. Сервисы владеют транзакцией/соединением. Восстановление требует Telegram/Google-сессию моложе 5 минут; старые сессии с NULL provenance не подходят. Адрес напоминания берётся из проверенной Telegram identity. Выпуск требует E2E Android, проверки PostgreSQL, refresh-транспорта и изоляции офлайн-данных при смене аккаунта; см. `api/auth/DEPLOYMENT.md`.

---

## 3. Дерево решений: Где искать проблему (Diagnostic Guide)

| Симптом / Проблема | Шаг 1: Проверить на клиенте | Шаг 2: Проверить на сервере | Корневой источник истины |
| :--- | :--- | :--- | :--- |
| **Изменения пропали после перезагрузки** | `localDb.js` (IndexedDB tables) $\to$ `offlineApi.js` | Проверить, прошел ли запрос в `api/routers/sync.py` | `sync_service.py` / конфликт версий |
| **Кнопка/модалка не реагирует или ломает верстку** | `app/src/components/modals/` $\to$ `useUiStore.js` | — | Локальный стейт модалки / Telegram Viewport resize |
| **Ошибка при пересчете интервала SRS (SuperMemo/Leitner)** | `app/src/components/study/StudyView.jsx` | `api/routers/study.py` | `api/srs.py` (алгоритм интервалов) |
| **Карточки создаются на сервере, но не видны в UI** | `createDeckSlice.js` (селектор фильтрации/папок) | `api/services/cards.py` | Флаги `is_deleted` или несоответствие `folder_id` |
| **Ошибка генерации карточек нейросетью** | Логи сетевого запроса к `/api/ai/...` | `api/routers/ai.py` $\to$ `api/ai_service.py` | Промпты в `prompt_builders.py` или API-ключи провайдера |
| **Не воспроизводится аудио карточки** | `app/src/utils/audio.js` $\to$ `mediaCache.js` | `api/routers/media.py` | `api/services/media.py` (генерация edge-tts) |
| **Сбой авторизации в Telegram/Android** | `app/src/store/useAuthStore.js`, `utils/auth.js`, `utils/platform.js` | `api/routers/auth_v2.py`, `bot.py`, `dependencies/auth.py` | `api/auth/providers.py`, TTL/challenge и сессии в `service.py`; deployment-проверки в `api/auth/DEPLOYMENT.md` |
