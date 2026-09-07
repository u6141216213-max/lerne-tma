"""Pydantic schemas for Lerne TMA Admin Panel."""
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class CreateDeckRequest(BaseModel):
    user_id: int
    name: str
    target_language: str = "de"
    level: Optional[str] = None
    topic: Optional[str] = None
    is_default: bool = False


class AssignDeckRequest(BaseModel):
    user_ids: List[int]
    mode: str = "copy"  # "copy", "collaborate", "library", "default_all"


class SetDefaultDeckRequest(BaseModel):
    is_default: bool
    copy_to_existing: bool = True


class AssignFolderRequest(BaseModel):
    user_ids: List[int]
    mode: str = "copy"  # "copy" or "default_all"


class SetDefaultFolderRequest(BaseModel):
    is_default: bool
    copy_to_existing: bool = True


class RegenerateDeckRequest(BaseModel):
    dry_run: bool = False
    only_empty: bool = False
    only_no_context: bool = False
    only_missing_audio: bool = False
    no_audio: bool = False
    skip_completed: bool = False
    sync_copies: bool = False
    voice: Optional[str] = "de-DE-KatjaNeural"
    rate: Optional[str] = "+0%"
    limit: Optional[int] = None
    delay: float = 1.5
    prompt_id: Optional[str] = "preset_b1"
    native_lang: Optional[str] = "uk"
    target_lang: Optional[str] = "de"
    exclude_card_ids: Optional[List[int]] = None
    target_card_ids: Optional[List[int]] = None
    exclude_range_str: Optional[str] = None
    start_card_idx: Optional[int] = None


class RegenerateAudioRequest(BaseModel):
    voice: Optional[str] = "de-DE-KatjaNeural"
    rate: Optional[str] = "+0%"
    only_missing_audio: bool = False
    skip_completed: bool = False
    sync_copies: bool = True
    delay: float = 0.3
    limit: Optional[int] = None
    exclude_card_ids: Optional[List[int]] = None
    target_card_ids: Optional[List[int]] = None
    exclude_range_str: Optional[str] = None
    start_card_idx: Optional[int] = None


class BatchRegenerateDeckRequest(BaseModel):
    deck_ids: List[str]
    dry_run: bool = False
    only_empty: bool = False
    only_no_context: bool = False
    only_missing_audio: bool = False
    no_audio: bool = False
    skip_completed: bool = False
    sync_copies: bool = False
    voice: Optional[str] = "de-DE-KatjaNeural"
    rate: Optional[str] = "+0%"
    delay: float = 1.5
    prompt_id: Optional[str] = "preset_b1"
    native_lang: Optional[str] = "uk"
    target_lang: Optional[str] = "de"
    cards_per_deck_limit: Optional[int] = None
    start_deck_idx: Optional[int] = None
    start_card_idx: Optional[int] = None
    exclude_card_ids: Optional[List[int]] = None
    target_card_ids: Optional[List[int]] = None


class RetryFailedCardsRequest(BaseModel):
    task_id: Optional[str] = None
    deck_ids: Optional[List[str]] = None
    card_ids: Optional[List[int]] = None
    voice: Optional[str] = "de-DE-SeraphinaMultilingualNeural"
    sync_copies: Optional[bool] = True
    native_lang: Optional[str] = "ru"
    target_lang: Optional[str] = "de"
    prompt_id: Optional[str] = "preset_exam"
    delay: float = 1.0


class BatchRegenerateAudioRequest(BaseModel):
    deck_ids: List[str]
    voice: Optional[str] = "de-DE-KatjaNeural"
    rate: Optional[str] = "+0%"
    only_missing_audio: bool = False
    skip_completed: bool = False
    sync_copies: bool = True
    delay: float = 0.3
    cards_per_deck_limit: Optional[int] = None
    start_deck_idx: Optional[int] = None
    start_card_idx: Optional[int] = None
    exclude_card_ids: Optional[List[int]] = None


class BulkCreateCardsRequest(BaseModel):
    deck_id: Optional[str] = None
    new_deck_name: Optional[str] = None
    target_language: str = "de"
    level: Optional[str] = None
    topic: Optional[str] = None
    user_id: Optional[int] = 0
    is_default: bool = False
    is_library: bool = False
    phrases: List[str]
    voice: Optional[str] = "de-DE-KatjaNeural"
    rate: Optional[str] = "+0%"
    generate_ai: bool = True
    generate_audio: bool = True
    sync_copies: bool = False
    delay: float = 1.0
    native_lang: Optional[str] = "uk"
    prompt_id: Optional[str] = "preset_b1"
    start_card_idx: Optional[int] = None


class SuggestWordsRequest(BaseModel):
    topic: str
    level: Optional[str] = "A1"
    count: Optional[int] = 20
    target_lang: Optional[str] = "de"
    native_lang: Optional[str] = "uk"


class ResumeTaskRequest(BaseModel):
    task_id: Optional[str] = None
    start_deck_idx: Optional[int] = None
    start_card_idx: Optional[int] = None


class UpdateCardRequest(BaseModel):
    front_text: Optional[str] = None
    back_text: Optional[str] = None
    context: Optional[str] = None
    tags: Optional[str] = None


class BatchDeleteDecksRequest(BaseModel):
    deck_ids: List[str]


class BatchDeleteFoldersRequest(BaseModel):
    folder_ids: List[int]


class BatchSummaryRequest(BaseModel):
    deck_ids: List[str]


class BatchControlRequest(BaseModel):
    action: str  # "pause", "resume", "stop", "commit_dry_run"
    card_ids: Optional[List[int]] = None


class BatchDeleteUsersRequest(BaseModel):
    user_ids: List[int]


class BackupItem(BaseModel):
    filename: str
    folder: Optional[str] = None


class BatchDeleteBackupsRequest(BaseModel):
    backups: List[BackupItem]


class ClassificationRequest(BaseModel):
    mode: str = "audit"  # "audit", "dry_run", "run"
    lang: str = "de"
    vocab_profile: str = "medium"
    overwrite: bool = True
    clear_uncertain_local: bool = False
    include_library: bool = False
    limit: Optional[int] = None
    delay: float = 1.0


class BackupSettingsRequest(BaseModel):
    custom_dir: str


class ControlRegenRequest(BaseModel):
    action: str  # "pause", "resume", "stop", "commit_dry_run"
    card_ids: Optional[List[int]] = None


VALID_CEFR_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}
CEFR_TAG_RE = re.compile(r"\b(A1|A2|B1|B2|C1|C2)\b", re.IGNORECASE)
