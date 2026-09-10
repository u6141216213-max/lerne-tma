/**
 * Fisher-Yates shuffle — uniform, unbiased in-place shuffle.
 * @param {Array} array - Input array (not mutated).
 * @returns {Array} New shuffled array.
 */
export const shuffleFY = (array) => {
  const a = [...array];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
};

/**
 * Pick N random items from an array without replacement using Fisher-Yates.
 * @param {Array} array - Source array.
 * @param {number} count - Number of items to pick.
 * @returns {Array} Array of `count` unique random items.
 */
export const pickRandom = (array, count) => {
  if (!array || array.length <= count) return [...(array || [])];
  return shuffleFY(array).slice(0, count);
};
