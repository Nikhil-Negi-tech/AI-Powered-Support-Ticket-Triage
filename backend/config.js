// config.js - All keyword rules live here

const categories = {
  Billing: ["payment", "charge", "invoice", "refund", "bill", "subscription", "price", "cost", "fee", "overcharged", "discount", "plan"],
  Technical: ["error", "bug", "crash", "broken", "not working", "fails", "issue", "slow", "down", "outage", "500", "timeout", "login", "loading"],
  Account: ["account", "password", "reset", "email", "username", "profile", "access", "locked", "signup", "register", "delete account"],
  "Feature Request": ["feature", "add", "would be nice", "suggestion", "request", "improve", "wish", "option", "support for", "allow"],
  Other: []
};

const urgencyKeywords = ["urgent", "asap", "immediately", "critical", "emergency", "right now", "help me", "broken", "down", "outage", "not working"];

const prioritySignals = {
  P0: ["outage", "down", "critical", "emergency", "data loss", "security breach", "hacked", "not working at all"],
  P1: ["urgent", "asap", "immediately", "broken", "crash", "fails"],
  P2: ["slow", "issue", "error", "bug", "problem"],
  P3: ["suggestion", "feature", "would be nice", "improve"]
};

// Custom rule: refund requests are always escalated to P1
const customRuleKeywords = ["refund", "money back", "charge back", "dispute"];

module.exports = { categories, urgencyKeywords, prioritySignals, customRuleKeywords };
