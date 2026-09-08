import os
import json
import re
import logging
import random
from fastapi import APIRouter, Depends, HTTPException, Query
from api.dependencies.auth import get_user_id
from api import models

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lid", tags=["lid"])

_LID_DATA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'app', 'src', 'data', 'lidTranslations.json'))
_LID_TRANSLATIONS_LOOKUP = {}

def _load_lid_translations():
    global _LID_TRANSLATIONS_LOOKUP
    if _LID_TRANSLATIONS_LOOKUP:
        return
    norm = lambda s: re.sub(r'\s+', ' ', (s or '').replace('\n', ' ')).strip().lower()
    alphanorm = lambda s: re.sub(r'[^a-zA-Z0-9\u00C0-\u017F]', '', (s or '').lower())
    if os.path.exists(_LID_DATA_PATH):
        try:
            with open(_LID_DATA_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for key, tr in data.items():
                    norm_k = norm(key)
                    _LID_TRANSLATIONS_LOOKUP[norm_k] = tr
                    if len(norm_k) >= 25:
                        _LID_TRANSLATIONS_LOOKUP[norm_k[:25]] = tr
                    _LID_TRANSLATIONS_LOOKUP[alphanorm(key)] = tr
        except Exception as e:
            logger.warning(f"Could not load LiD translations in router: {e}")

_load_lid_translations()

STATE_CODE_TO_NAME = {
    'BW': 'Baden-Württemberg',
    'BY': 'Bayern',
    'BE': 'Berlin',
    'BB': 'Brandenburg',
    'HB': 'Bremen',
    'HH': 'Hamburg',
    'HE': 'Hessen',
    'MV': 'Mecklenburg-Vorpommern',
    'NI': 'Niedersachsen',
    'NW': 'Nordrhein-Westfalen',
    'RP': 'Rheinland-Pfalz',
    'SL': 'Saarland',
    'SN': 'Sachsen',
    'ST': 'Sachsen-Anhalt',
    'SH': 'Schleswig-Holstein',
    'TH': 'Thüringen',
}

MASTER_UID = 642478257

def serialize_card(card, deck_name=""):
    img = getattr(card, 'image_path', '') or ''
    aud = getattr(card, 'audio_path', '') or ''
    pos = getattr(card, 'position', 0) or 0
    d_name = deck_name or (card.deck.name if hasattr(card, 'deck') and card.deck else "")

    norm = lambda s: re.sub(r'\s+', ' ', (s or '').replace('\n', ' ')).strip().lower()
    alphanorm = lambda s: re.sub(r'[^a-zA-Z0-9\u00C0-\u017F]', '', (s or '').lower())
    raw_first = (card.front_text or '').split('\n\n')[0] if '\n\n' in (card.front_text or '') else (card.front_text or '').split('\n')[0]
    first_line = norm(raw_first)
    trans_ru = (
        _LID_TRANSLATIONS_LOOKUP.get(first_line)
        or _LID_TRANSLATIONS_LOOKUP.get(first_line[:25])
        or _LID_TRANSLATIONS_LOOKUP.get(alphanorm(raw_first))
        or None
    )

    # Calculate BAMF catalog question number (1..300 general, or Land code):
    bamf_num = 0
    if trans_ru and trans_ru.get('num'):
        try:
            bamf_num = int(trans_ru['num'])
        except (ValueError, TypeError):
            bamf_num = trans_ru['num']
    elif pos > 0:
        d_lower = d_name.lower()
        if '1.' in d_name or 'politik' in d_lower:
            bamf_num = pos
        elif '2.' in d_name or 'geschichte' in d_lower:
            bamf_num = pos if pos > 100 else 100 + pos
        elif '3.' in d_name or 'mensch' in d_lower:
            bamf_num = pos if pos > 100 else 200 + pos
        else:
            bamf_num = pos

    return {
        "id": card.id,
        "deck_id": card.deck_id,
        "deck_name": d_name,
        "front": card.front_text or "",
        "back": card.back_text or "",
        "context": card.context or "",
        "image_path": img,
        "media_url": img,
        "audio_url": aud,
        "audio_path": aud,
        "card_type": getattr(card, 'card_type', 'quiz') or 'quiz',
        "position": pos,
        "bamf_num": bamf_num,
        "translationRu": trans_ru,
    }

@router.get("/ticket")
def get_exam_ticket(
    state_code: str = Query('BY', description="Two-letter Bundesland code (e.g. BY, BE, NW)"),
    user_id: int = Depends(get_user_id)
):
    """
    Generates a 33-question official BAMF LiD exam ticket from real cards:
    - 10 from Block 1: Politik in der Demokratie
    - 10 from Block 2: Geschichte und Verantwortung
    - 10 from Block 3: Mensch und Gesellschaft
    - 3 from the chosen Bundesland deck
    """
    target_state = STATE_CODE_TO_NAME.get(state_code.upper(), 'Bayern')

    def find_user_decks(uid):
        return list(models.TMA_Deck.select().join(models.TMA_Folder).where(
            models.TMA_Folder.user_id == uid,
            models.TMA_Folder.name.ilike('%Leben in Deutschland%'),
            models.TMA_Folder.is_deleted == False,
            models.TMA_Deck.is_deleted == False
        ))

    user_decks = find_user_decks(user_id)
    b1 = next((d for d in user_decks if d.name.startswith('1.') or 'politik' in d.name.lower()), None)
    b2 = next((d for d in user_decks if d.name.startswith('2.') or 'geschichte' in d.name.lower()), None)
    b3 = next((d for d in user_decks if d.name.startswith('3.') or 'mensch' in d.name.lower()), None)
    st = next((d for d in user_decks if d.name.lower() == target_state.lower()), None)

    def get_cards(deck):
        if not deck:
            return []
        return list(models.TMA_Card.select().where(models.TMA_Card.deck == deck, models.TMA_Card.is_deleted == False))

    c1 = get_cards(b1)
    c2 = get_cards(b2)
    c3 = get_cards(b3)
    cs = get_cards(st)

    # Fallback to master decks if any are missing or empty in this user's folder
    if len(c1) < 10 or len(c2) < 10 or len(c3) < 10 or len(cs) < 3:
        master_decks = find_user_decks(MASTER_UID)
        if len(c1) < 10:
            for d in master_decks:
                if d.name.startswith('1.') or 'politik' in d.name.lower():
                    cards = get_cards(d)
                    if len(cards) >= 10:
                        b1, c1 = d, cards
                        break
        if len(c2) < 10:
            for d in master_decks:
                if d.name.startswith('2.') or 'geschichte' in d.name.lower():
                    cards = get_cards(d)
                    if len(cards) >= 10:
                        b2, c2 = d, cards
                        break
        if len(c3) < 10:
            for d in master_decks:
                if d.name.startswith('3.') or 'mensch' in d.name.lower():
                    cards = get_cards(d)
                    if len(cards) >= 10:
                        b3, c3 = d, cards
                        break
        if len(cs) < 3:
            for d in master_decks:
                if d.name.lower() == target_state.lower():
                    cards = get_cards(d)
                    if len(cards) >= 3:
                        st, cs = d, cards
                        break

    picked_b1 = random.sample(c1, min(len(c1), 10))
    picked_b2 = random.sample(c2, min(len(c2), 10))
    picked_b3 = random.sample(c3, min(len(c3), 10))
    picked_st = random.sample(cs, min(len(cs), 3))

    ticket_cards = []
    for c in picked_b1:
        ticket_cards.append(serialize_card(c, b1.name if b1 else "1. Politik in der Demokratie"))
    for c in picked_b2:
        ticket_cards.append(serialize_card(c, b2.name if b2 else "2. Geschichte und Verantwortung"))
    for c in picked_b3:
        ticket_cards.append(serialize_card(c, b3.name if b3 else "3. Mensch und Gesellschaft"))
    for c in picked_st:
        ticket_cards.append(serialize_card(c, st.name if st else target_state))

    return {
        "status": "success",
        "state_code": state_code.upper(),
        "state_name": target_state,
        "total": len(ticket_cards),
        "cards": ticket_cards
    }
