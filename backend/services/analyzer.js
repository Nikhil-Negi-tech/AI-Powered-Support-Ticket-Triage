// services/analyzer.js 

const { categories, urgencyKeywords, prioritySignals, customRuleKeywords } = require("../config");

function analyzeTicket(message) {
  const text = message.toLowerCase();

  const category = classifyCategory(text);
  const keywords = extractKeywords(text);
  const isUrgent = detectUrgency(text);
  const priority = assignPriority(text, category);
  const confidence = calcConfidence(text, category);

  return { category, priority, isUrgent, keywords, confidence };
}

function classifyCategory(text) {
  let best = "Other";
  let bestCount = 0;

  for (const [cat, words] of Object.entries(categories)) {
    if (cat === "Other") continue;
    const count = words.filter(w => text.includes(w)).length;
    if (count > bestCount) {
      bestCount = count;
      best = cat;
    }
  }

  return best;
}

function extractKeywords(text) {
  const allWords = Object.values(categories).flat();
  const extras = [...urgencyKeywords, ...Object.values(prioritySignals).flat()];
  const pool = [...new Set([...allWords, ...extras])];
  return pool.filter(w => text.includes(w));
}

function detectUrgency(text) {
  return urgencyKeywords.some(w => text.includes(w));
}

function assignPriority(text, category) {
  // Custom rule: refund-related tickets are always at least P1
  const isRefund = customRuleKeywords.some(w => text.includes(w));
  if (isRefund) return "P1";

  for (const [level, words] of Object.entries(prioritySignals)) {
    if (words.some(w => text.includes(w))) return level;
  }

  return "P3";
}

function calcConfidence(text, category) {
  if (category === "Other") return 0.3;
  const words = categories[category] || [];
  const matched = words.filter(w => text.includes(w)).length;
  const score = Math.min(matched / words.length + 0.2, 1.0);
  return Math.round(score * 100) / 100;
}

module.exports = { analyzeTicket };
