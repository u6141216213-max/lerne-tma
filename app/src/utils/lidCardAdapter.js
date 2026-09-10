import { stripMarkdown } from './text.js';
import lidTranslations from '../data/lidTranslations.json' with { type: 'json' };
import { shuffleFY } from './shuffle.js';

const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();

/**
 * Parses a Russian back translation text into clean question and option translations.
 * Format on card back:
 * [Question in Russian]
 * 
 * 1. [Option 1]
 * 2. ✅ [Option 2]
 * 3. [Option 3]
 * 4. [Option 4]
 */
export const parseBackTranslation = (backText) => {
  if (!backText) {
    return { question: '', optionsRu: [] };
  }

  const parts = backText.trim().split(/\n\s*\n/);
  const question = parts[0] ? stripMarkdown(parts[0].trim()) : '';
  const optionsBlock = parts.slice(1).join('\n').trim();

  const lines = optionsBlock.split('\n').map(l => l.trim()).filter(Boolean);
  const optionsRu = lines.map(line => {
    // Strip prefixes like "1. ", "2) ", "✅ ", "1. ✅ "
    return line
      .replace(/^[0-9]{1,2}[.)]\s*/, '')
      .replace(/^(?:✅|\*|▫️|-)\s*/u, '')
      .replace(/^(?:✅|\*|▫️|-)\s*/u, '')
      .trim();
  });

  return { question, optionsRu };
};

/**
 * Transforms a real database / Dexie card into a structured question object for the LiD exam ticket.
 * Automatically couples German options with their exact Russian translations and shuffles choices
 * so users cannot rely on muscle / positional memory.
 */
export const transformCardToExamQuestion = (card, examIndex = 1, { shuffle = true } = {}) => {
  if (!card) return null;

  const rawFront = (card.front || card.front_text || '').trim();
  const rawBack = (card.back || card.back_text || '').trim();
  const rawContext = card.context || '';
  const audioUrl = card.audio_url || card.audio_path || '';
  const rawMedia = card.media_url || card.image_path || card.image_url || card.image || '';

  // Resolve media image URL
  let resolvedImage = null;
  if (rawMedia) {
    if (rawMedia.startsWith('http') || rawMedia.startsWith('/')) {
      resolvedImage = rawMedia;
    } else {
      resolvedImage = `/lid_images/${rawMedia}`;
    }
  }

  // 1. Parse German question & options from front_text
  let questionDe = '';
  const optionLines = [];

  if (rawFront.includes('\n\n')) {
    const parts = rawFront.split(/\n\s*\n/);
    questionDe = parts[0].trim();
    const optsRemaining = parts.slice(1).join('\n\n').trim();
    optionLines.push(...optsRemaining.split('\n').map(l => l.trim()).filter(Boolean));
  } else {
    const allLines = rawFront.split('\n').map(l => l.trim()).filter(Boolean);
    questionDe = allLines[0] || '';
    optionLines.push(...allLines.slice(1));
  }

  const OPT_LETTERS = ['a', 'b', 'c', 'd', 'e', 'f'];

  // 2. Resolve verified Russian translation data
  const normQ = norm(questionDe);
  const localTrans = lidTranslations[normQ] || lidTranslations[normQ.slice(0, 30)] || null;
  const apiTrans = card.translationRu || card.translation_ru || null;
  const { question: parsedRuQ, optionsRu: parsedOptionsRu } = parseBackTranslation(rawBack);

  const questionRu = apiTrans?.question || localTrans?.question || parsedRuQ || '';
  const contextRu = apiTrans?.context || localTrans?.context || '';

  // 3. Form tightly-coupled pairs: { textDe, textRu, isCorrect }
  const optionPairs = optionLines.map((line, idx) => {
    const origLetter = OPT_LETTERS[idx] || String(idx + 1);
    const isCorrect = line.startsWith('*') || line.endsWith('*');
    const cleanText = line.replace(/^\*|\*$/g, '').trim();

    const optRu = (apiTrans && apiTrans[origLetter]) ||
                  (localTrans && localTrans[origLetter]) ||
                  parsedOptionsRu[idx] ||
                  '';

    return {
      text: cleanText,
      textRu: optRu,
      isCorrect,
      origLetter
    };
  });

  // 4. Randomly shuffle options if requested (default true)
  const finalPairs = shuffle ? shuffleFY(optionPairs) : [...optionPairs];

  const options = [];
  let correctOption = 'a';
  const translationRu = {
    question: questionRu,
    context: contextRu
  };

  finalPairs.forEach((pair, idx) => {
    const optId = OPT_LETTERS[idx] || String(idx + 1);
    if (pair.isCorrect) {
      correctOption = optId;
    }

    options.push({
      id: optId,
      text: pair.text,
      translationRu: pair.textRu,
      isCorrect: pair.isCorrect,
      origId: pair.origLetter
    });

    if (pair.textRu) {
      translationRu[optId] = pair.textRu;
    }
  });

  // 5. Official BAMF catalog question number:
  // 1..100 (Politik), 101..200 (Geschichte), 201..300 (Mensch), 1..10 (Bundesland)
  let bamfNumber = card.bamf_num || null;
  if (!bamfNumber) {
    const qNorm = norm(questionDe);
    const trData = card.translationRu || lidTranslations[qNorm] || lidTranslations[qNorm.slice(0, 25)];
    if (trData && trData.num) {
      bamfNumber = trData.num;
    }
  }
  if (!bamfNumber) {
    const qAlpha = questionDe.replace(/[^a-zA-Z0-9\u00C0-\u017F]/g, '').toLowerCase();
    for (const [key, trVal] of Object.entries(lidTranslations)) {
      if (trVal?.num) {
        const kAlpha = key.replace(/[^a-zA-Z0-9\u00C0-\u017F]/g, '').toLowerCase();
        if (kAlpha === qAlpha || (kAlpha.length > 15 && (kAlpha.includes(qAlpha) || qAlpha.includes(kAlpha)))) {
          bamfNumber = trVal.num;
          break;
        }
      }
    }
  }
  if (!bamfNumber) {
    const pos = card.position || 0;
    const deckName = (card.deck_name || '').toLowerCase();
    if (pos > 0) {
      if (deckName.includes('1.') || deckName.includes('politik')) {
        bamfNumber = pos;
      } else if (deckName.includes('2.') || deckName.includes('geschichte')) {
        bamfNumber = pos > 100 ? pos : 100 + pos;
      } else if (deckName.includes('3.') || deckName.includes('mensch')) {
        bamfNumber = pos > 100 ? pos : 200 + pos;
      } else {
        bamfNumber = pos; // 1..10
      }
    }
  }

  return {
    id: card.id,
    examIndex,
    category: card.deck_name || 'Leben in Deutschland',
    question: questionDe,
    options,
    correctOption,
    cardBack: rawBack,
    cardContext: rawContext,
    context: rawContext,
    audioUrl,
    image: resolvedImage,
    translationRu,
    bamfNumber,
    rawCard: card
  };
};


