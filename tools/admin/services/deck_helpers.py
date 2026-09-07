"""Deck-level helper utilities for the Admin Panel."""
import datetime
import logging
from typing import Optional, Tuple

from api import models

logger = logging.getLogger(__name__)


def get_deck_and_cards(deck_id_val):
    """Resolves a deck_id string to a (deck, cards, is_library) tuple."""
    s_id = str(deck_id_val).strip()
    if s_id.startswith("lib_"):
        raw_id = int(s_id.replace("lib_", ""))
        deck = models.Deck.get_or_none(models.Deck.id == raw_id)
        if not deck:
            return None, [], True
        cards = list(
            models.Card.select()
            .where((models.Card.deck == deck) & (models.Card.is_deleted == False))
            .order_by(models.Card.position.asc(), models.Card.id.asc())
        )
        return deck, cards, True
    else:
        try:
            d_id = int(s_id)
            deck = models.TMA_Deck.get_or_none(models.TMA_Deck.id == d_id)
            if deck:
                cards = list(
                    models.TMA_Card.select()
                    .where((models.TMA_Card.deck_id == deck.id) & (models.TMA_Card.is_deleted == False))
                    .order_by(models.TMA_Card.position.asc(), models.TMA_Card.id.asc())
                )
                return deck, cards, False
            deck = models.Deck.get_or_none(models.Deck.id == d_id)
            if deck:
                cards = list(
                    models.Card.select()
                    .where((models.Card.deck == deck) & (models.Card.is_deleted == False))
                    .order_by(models.Card.position.asc(), models.Card.id.asc())
                )
                return deck, cards, True
            return None, [], False
        except ValueError:
            return None, [], False


def sync_card_audio_to_matching_decks(source_deck, front_query: str, audio_path: str) -> Tuple[int, int]:
    """Propagates updated card audio_path to all decks with the same name across all users and library templates."""
    clean_name = source_deck.name.replace("⭐ ", "").strip()
    now = datetime.datetime.now()

    matching_tma_decks = list(
        models.TMA_Deck.select().where(
            (
                (models.TMA_Deck.name == source_deck.name) |
                (models.TMA_Deck.name == clean_name) |
                (models.TMA_Deck.name == f"⭐ {clean_name}")
            ) &
            (models.TMA_Deck.id != getattr(source_deck, 'id', 0)) &
            (models.TMA_Deck.is_deleted == False)
        )
    )
    synced_count = 0
    for d in matching_tma_decks:
        cards = list(models.TMA_Card.select().where(
            (models.TMA_Card.deck_id == d.id) &
            (models.TMA_Card.is_deleted == False) &
            (models.TMA_Card.front_text == front_query)
        ))
        for c in cards:
            c.audio_path = audio_path
            c.updated_at = now
            c.save()
            synced_count += 1

    matching_lib_decks = list(
        models.Deck.select().where(
            (
                (models.Deck.name == source_deck.name) |
                (models.Deck.name == clean_name) |
                (models.Deck.name == f"⭐ {clean_name}")
            ) &
            (models.Deck.id != getattr(source_deck, 'id', 0)) &
            (models.Deck.is_deleted == False)
        )
    )
    for ld in matching_lib_decks:
        cards = list(models.Card.select().where(
            (models.Card.deck == ld) &
            (models.Card.is_deleted == False) &
            (models.Card.front_text == front_query)
        ))
        for c in cards:
            c.audio_path = audio_path
            c.updated_at = now
            c.save()
            synced_count += 1

    total_matching_decks = len(matching_tma_decks) + len(matching_lib_decks)
    return total_matching_decks, synced_count


def sync_card_updates_to_matching_decks(
    source_deck,
    front_query: str,
    new_front: str,
    new_back: str,
    new_context: str,
    new_level: Optional[str] = None,
    new_audio_path: Optional[str] = None,
) -> Tuple[int, int]:
    """Propagates updated card content to all decks with the same name across all users and library templates."""
    clean_name = source_deck.name.replace("⭐ ", "").strip()
    now = datetime.datetime.now()

    matching_tma_decks = list(
        models.TMA_Deck.select().where(
            (
                (models.TMA_Deck.name == source_deck.name) |
                (models.TMA_Deck.name == clean_name) |
                (models.TMA_Deck.name == f"⭐ {clean_name}")
            ) &
            (models.TMA_Deck.id != getattr(source_deck, 'id', 0)) &
            (models.TMA_Deck.is_deleted == False)
        )
    )
    synced_cards_count = 0
    for d in matching_tma_decks:
        cards = list(models.TMA_Card.select().where(
            (models.TMA_Card.deck_id == d.id) &
            (models.TMA_Card.is_deleted == False) &
            ((models.TMA_Card.front_text == front_query) | (models.TMA_Card.front_text == new_front))
        ))
        for c in cards:
            c.front_text = new_front
            c.back_text = new_back
            c.context = new_context
            if new_audio_path:
                c.audio_path = new_audio_path
            if new_level:
                curr_tags = c.tags or ""
                cleaned = ",".join([t for t in curr_tags.split(",") if t and t.upper() not in {"A1", "A2", "B1", "B2", "C1", "C2"}])
                c.tags = f"{cleaned},{new_level}".strip(",") if cleaned else new_level
            c.updated_at = now
            c.save()
            synced_cards_count += 1

    matching_lib_decks = list(
        models.Deck.select().where(
            (
                (models.Deck.name == source_deck.name) |
                (models.Deck.name == clean_name) |
                (models.Deck.name == f"⭐ {clean_name}")
            ) &
            (models.Deck.id != getattr(source_deck, 'id', 0)) &
            (models.Deck.is_deleted == False)
        )
    )
    for ld in matching_lib_decks:
        cards = list(models.Card.select().where(
            (models.Card.deck == ld) &
            (models.Card.is_deleted == False) &
            ((models.Card.front_text == front_query) | (models.Card.front_text == new_front))
        ))
        for c in cards:
            c.front_text = new_front
            c.back_text = new_back
            c.context = new_context
            if new_audio_path:
                c.audio_path = new_audio_path
            if new_level:
                curr_tags = c.tags or ""
                cleaned = ",".join([t for t in curr_tags.split(",") if t and t.upper() not in {"A1", "A2", "B1", "B2", "C1", "C2"}])
                c.tags = f"{cleaned},{new_level}".strip(",") if cleaned else new_level
            c.updated_at = now
            c.save()
            synced_cards_count += 1

    total_decks = len(matching_tma_decks) + len(matching_lib_decks)
    return total_decks, synced_cards_count
