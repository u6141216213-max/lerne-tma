import React, { useEffect } from 'react';
import { motion } from 'framer-motion';
import './App.css';

// Utils & Services
import { getUserId, storage } from './utils/auth';
import { disableClosingConfirmation, setupBackButton, hideBackButton, showBackButton } from './utils/platform';
import { navigateUp } from './utils/navigation';


// Components
import { Toast } from './components/common/Toast';
import { GlobalLoader } from './components/common/Loader';
import { GuestBanner } from './components/common/UserBadge';
import { DeckGrid, CardList, CardEditor, CardCreator } from './components/deckgrid';
import { StudyView, TrainerView } from './components/study';
import { 
  CardActionModal, DeckModals, SettingsModal, RenameDeckModal, 
  SyncModal, DuplicateManager, TrashManager, AuthRequiredModal, 
  LanguageSelectionModal, ImportModal, CollaboratorsModal, BatchCardModal 
} from './components/modals';
import { TutorialOverlay } from './components/TutorialOverlay';
import { HelpDrawer } from './components/help/HelpDrawer';
import { FullGuideModal } from './components/help/FullGuideModal';
import { LidExamView } from './components/lid/LidExamView';

import { TUTORIAL_STEPS } from './constants/appConstants';

// Stores & Hooks
import { useUiStore } from './store/useUiStore';
import { useDeckStore } from './store/useDeckStore';
import { useSessionStore } from './store/useSessionStore';
import { useLanguageStore } from './store/useLanguageStore';
import { useCardActions } from './hooks/useCardActions';
import { useAutoImport } from './hooks/useAutoImport';
import { useAppInitialization } from './hooks/useAppInitialization';
import { useCardNavigation } from './hooks/useCardNavigation';
import { useCollaborativeSync } from './hooks/useCollaborativeSync';
import { useStudyNavigation } from './hooks/useStudyNavigation';

import { LanguageProvider, useTranslation } from './i18n/i18nContext';
import { getLocalizedTutorialSteps } from './i18n/tutorialSteps';
import LanguageWelcomeModal from './components/modals/LanguageWelcomeModal';

const USER_ID = getUserId();

function AppContent() {
  const { isFirstLaunch, setIsFirstLaunch, nativeLanguage } = useTranslation();

  const view = useUiStore(state => state.view);
  const setView = useUiStore(state => state.setView);
  const isOpeningDeck = useUiStore(state => state.isOpeningDeck);
  const activeTutorial = useUiStore(state => state.activeTutorial);
  const setActiveTutorial = useUiStore(state => state.setActiveTutorial);
  const toast = useUiStore(state => state.toast);
  const userProfile = useUiStore(state => state.userProfile);
  const currentUserId = userProfile?.user_id ?? getUserId();
  const isCardActionModalOpen = useUiStore(state => state.isCardActionModalOpen);
  const setIsCardActionModalOpen = useUiStore(state => state.setIsCardActionModalOpen);
  const actionCard = useUiStore(state => state.actionCard);
  const loading = useUiStore(state => state.loading);

  const decks = useDeckStore(state => state.decks);
  const folders = useDeckStore(state => state.folders);
  const currentDeck = useDeckStore(state => state.currentDeck);
  const deckToSync = useDeckStore(state => state.deckToSync);
  const setSyncModalOpen = useDeckStore(state => state.setSyncModalOpen);
  const syncModalOpen = useDeckStore(state => state.syncModalOpen);
  const handleSyncDeck = useDeckStore(state => state.handleSyncDeck);

  const { isFlipped } = useSessionStore();
  const { handleMoveCard, handleCopyCard, handleDeleteCard, handleToggleLearn, handleShareCard } = useCardActions();
  const { openEditor } = useCardNavigation();
  const { startStudy, startStudyCard } = useStudyNavigation();

  const activeFolderId = useUiStore(state => state.activeFolderId);
  const isSettingsOpen = useUiStore(state => state.isSettingsOpen);
  const isNewDeckModalOpen = useUiStore(state => state.isNewDeckModalOpen);
  const isRenameModalOpen = useUiStore(state => state.isRenameModalOpen);

  // Sync state with history and Telegram BackButton
  const isPopStateRef = React.useRef(false);
  const ignoreNextPopStateRef = React.useRef(false);
  const lastLevelRef = React.useRef(0);
  const lastModalOpenRef = React.useRef(false);
  const lastViewRef = React.useRef(view);
  const lastFolderIdRef = React.useRef(activeFolderId);

  const isAuthModalOpen = useUiStore(state => state.isAuthModalOpen);
  const authModalTitle = useUiStore(state => state.authModalTitle);
  const isBatchModalOpen = useUiStore(state => state.isBatchModalOpen);
  const isCollaboratorsModalOpen = useUiStore(state => state.isCollaboratorsModalOpen);
  const importShareId = useUiStore(state => state.importShareId);
  const isLanguageModalOpen = useLanguageStore(state => state.isLanguageModalOpen);
  const isHelpOpen = useUiStore(state => state.isHelpOpen);
  const isFullGuideOpen = useUiStore(state => state.isFullGuideOpen);

  const anyModalOpen = Boolean(
    isHelpOpen ||
    isFullGuideOpen ||
    isSettingsOpen ||
    isNewDeckModalOpen ||
    isRenameModalOpen ||
    isCardActionModalOpen ||
    syncModalOpen ||
    isAuthModalOpen ||
    isBatchModalOpen ||
    isCollaboratorsModalOpen ||
    isLanguageModalOpen ||
    importShareId
  );

  // 1. Setup popstate listener and native back button onClick callback on mount
  useEffect(() => {
    disableClosingConfirmation();
    
    // Replace initial state with root decks view
    window.history.replaceState({ level: 0, view: 'decks', folderId: null }, '');

    const handlePopState = () => {
      if (ignoreNextPopStateRef.current) {
        ignoreNextPopStateRef.current = false;
        return;
      }
      isPopStateRef.current = true;
      const didNavigate = navigateUp();
      if (!didNavigate) {
        window.history.replaceState({ level: 0, view: 'decks', folderId: null }, '');
      }
      setTimeout(() => {
        isPopStateRef.current = false;
      }, 50);
    };

    window.addEventListener('popstate', handlePopState);
    
    const cleanupBackButton = setupBackButton(() => {
      navigateUp();
    });

    return () => {
      window.removeEventListener('popstate', handlePopState);
      cleanupBackButton();
    };
  }, []);

  // 2. Push history state on hierarchy depth increase and sync BackButton visibility
  useEffect(() => {
    // Helper to calculate folder depth in hierarchy
    let folderDepth = 0;
    if (activeFolderId) {
      let curr = activeFolderId;
      const visited = new Set();
      while (curr && !visited.has(curr)) {
        visited.add(curr);
        folderDepth++;
        const folder = folders?.find(f => f.id === curr);
        curr = folder?.parent_id;
      }
    }

    const isSubView = (view === 'study' || view === 'trainer' || view === 'editor' || view === 'creator');
    const isDeckView = (view === 'cards' || view === 'duplicates' || view === 'trash' || view === 'lid' || view === 'lid_exam');
    const currentLevel = anyModalOpen 
      ? (isSubView ? folderDepth + 3 : isDeckView ? folderDepth + 2 : folderDepth + 1)
      : isSubView 
      ? folderDepth + 2 
      : isDeckView 
      ? folderDepth + 1 
      : folderDepth;

    if (isPopStateRef.current) {
      lastLevelRef.current = currentLevel;
      lastViewRef.current = view;
      lastFolderIdRef.current = activeFolderId;
      lastModalOpenRef.current = anyModalOpen;
      return;
    }

    const prevLevel = lastLevelRef.current;
    if (currentLevel > prevLevel) {
      // Navigated down the hierarchy -> push state
      window.history.pushState({ level: currentLevel, view, folderId: activeFolderId, modalOpen: anyModalOpen }, '');
    } else if (currentLevel < prevLevel) {
      // Navigated up via UI button or BackButton -> sync browser history if needed without re-triggering navigateUp
      if (window.history.state?.level > currentLevel) {
        ignoreNextPopStateRef.current = true;
        window.history.back();
      }
    }

    lastLevelRef.current = currentLevel;
    lastModalOpenRef.current = anyModalOpen;
    lastViewRef.current = view;
    lastFolderIdRef.current = activeFolderId;

    // Sync BackButton visibility: hidden only on root level
    if (currentLevel === 0) {
      hideBackButton();
    } else {
      showBackButton();
    }
  }, [view, activeFolderId, anyModalOpen, folders]);

  // Custom hooks for initialization and import logic
  const { clearImportShareId, checkStartParam } = useAutoImport();
  useAppInitialization(checkStartParam);
  useCollaborativeSync(); // Real-time background sync for collaborative folders
  
  // Scroll to top on view change
  useEffect(() => {
    if (view === 'duplicates' && useDeckStore.getState().lastDuplicateCardId) {
      return; // Let DuplicateManager handle the scroll
    }
    if (view === 'cards') {
      const container = document.getElementById('app-container');
      const savedScroll = useUiStore.getState().cardsScrollTop;
      const lastId = useUiStore.getState().lastSelectedCardId;
      if (container && (savedScroll > 0 || lastId)) {
        if (savedScroll > 0) {
          container.scrollTop = savedScroll;
        }
        return; // Let CardList handle restoring scroll
      }
    }
    const container = document.getElementById('app-container');
    if (container) {
      container.scrollTo({ top: 0, behavior: 'instant' });
    }
  }, [view]);

  const finishTutorial = (context) => {
    storage.set(`lerne_tut_seen_${context}`, 'true');
    setActiveTutorial(null);
  };

  const startTutorial = (context) => {
    storage.remove(`lerne_tut_seen_${context}`);
    setActiveTutorial(null);
    setTimeout(() => {
      setActiveTutorial(context);
    }, 100);
  };

  // View Router
  const renderView = () => {
    switch (view) {
      case 'study':
      case 'trainer':
        return <StudyView />;
      case 'cards':
        return (
          <CardList
            startStudy={startStudy}
            startStudyCard={startStudyCard}
          />
        );
      case 'duplicates':
        return <DuplicateManager />;
      case 'trash':
        return <TrashManager />;
      case 'lid_exam':
        return <LidExamView />;
      case 'decks':
      default:
        return (
          <DeckGrid
            userId={USER_ID}
            startStudy={startStudy}
            startTutorial={startTutorial}
          />
        );
    }
  };

  return (
    <motion.div id="app-container" className="app-container" layoutScroll>
      <GuestBanner />
      
      {/* Active View */}
      {renderView()}

      {/* Overlays and Modals */}
      {view === 'creator' && <CardCreator />}
      {view === 'editor' && <CardEditor />}
      {isBatchModalOpen && <BatchCardModal />}
      {isNewDeckModalOpen && <DeckModals />}
      {isRenameModalOpen && <RenameDeckModal />}
      {isCollaboratorsModalOpen && <CollaboratorsModal />}
      {isSettingsOpen && <SettingsModal userId={currentUserId} />}
      
      {importShareId && (
        <ImportModal
          shareId={importShareId}
          onImportSuccess={() => {
            clearImportShareId();
            setView('decks');
          }}
          onClose={() => clearImportShareId()}
        />
      )}
      
      {syncModalOpen && (
        <SyncModal
          isOpen={syncModalOpen}
          onClose={() => setSyncModalOpen(false)}
          deck={deckToSync}
          onSync={(mode) => handleSyncDeck(deckToSync?.id, mode)}
          loading={loading}
        />
      )}

      {activeTutorial && (
        <TutorialOverlay
          isOpen={!!activeTutorial}
          steps={getLocalizedTutorialSteps(nativeLanguage, activeTutorial)}
          onFinish={() => finishTutorial(activeTutorial)}
          onSkip={() => finishTutorial(activeTutorial)}
          isFlipped={isFlipped}
        />
      )}

      {isCardActionModalOpen && (
        <CardActionModal
          isOpen={isCardActionModalOpen}
          onClose={() => setIsCardActionModalOpen(false)}
          card={actionCard}
          decks={decks}
          folders={folders}
          onMove={handleMoveCard}
          onCopy={handleCopyCard}
          onDelete={(c) => handleDeleteCard(c.id, true)}
          onToggleLearn={handleToggleLearn}
          onShare={handleShareCard}
          onEdit={(c) => openEditor(c.deck_id || currentDeck?.id, c, view)}
          onStartAutoplay={async (c) => {
            if (view === 'study') {
              const { startAutoplayFn } = useSessionStore.getState();
              if (startAutoplayFn) {
                startAutoplayFn();
              }
            } else {
              const targetDeck = decks.find(d => d.id === c?.deck_id) || currentDeck;
              if (targetDeck && c?.id) {
                await startStudyCard(targetDeck, c.id);
                setTimeout(() => {
                  const { startAutoplayFn } = useSessionStore.getState();
                  if (startAutoplayFn) startAutoplayFn();
                }, 400);
              }
            }
          }}
          loading={loading}
        />
      )}

      {isAuthModalOpen && (
        <AuthRequiredModal
          isOpen={isAuthModalOpen}
          onClose={() => useUiStore.getState().setIsAuthModalOpen(false)}
          title={authModalTitle}
        />
      )}

      {isLanguageModalOpen && <LanguageSelectionModal />}
      
      {/* Help System: Contextual Drawer & Full Guide Modal */}
      <HelpDrawer onStartTutorial={startTutorial} />
      <FullGuideModal />

      {isFirstLaunch && (
        <LanguageWelcomeModal 
          isOpen={isFirstLaunch} 
          onComplete={() => {
            setIsFirstLaunch(false);
            storage.set('lerne_welcome_seen', 'true');
            setTimeout(() => {
              setActiveTutorial('welcome');
            }, 350);
          }}
          onClose={() => setIsFirstLaunch(false)} 
        />
      )}

      <Toast toast={toast} />
      <GlobalLoader isVisible={isOpeningDeck} />
    </motion.div>
  );
}

export default function App() {
  return (
    <LanguageProvider>
      <AppContent />
    </LanguageProvider>
  );
}

