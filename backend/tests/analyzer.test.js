// tests/analyzer.test.js
// Run with: node tests/analyzer.test.js

const { analyzeTicket } = require("../services/analyzer");

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  PASS  ${name}`);
    passed++;
  } catch (e) {
    console.log(`  FAIL  ${name}: ${e.message}`);
    failed++;
  }
}

function assert(condition, msg) {
  if (!condition) throw new Error(msg || "Assertion failed");
}

console.log("\nRunning Analyzer Tests...\n");

test("classifies billing ticket", () => {
  const r = analyzeTicket("I was overcharged on my invoice last month");
  assert(r.category === "Billing", `Expected Billing, got ${r.category}`);
});

test("classifies technical ticket", () => {
  const r = analyzeTicket("The app keeps crashing when I open it");
  assert(r.category === "Technical", `Expected Technical, got ${r.category}`);
});

test("classifies account ticket", () => {
  const r = analyzeTicket("I can't reset my password");
  assert(r.category === "Account", `Expected Account, got ${r.category}`);
});

test("classifies feature request", () => {
  const r = analyzeTicket("It would be nice to have dark mode as a feature");
  assert(r.category === "Feature Request", `Expected Feature Request, got ${r.category}`);
});

test("detects urgency correctly", () => {
  const r = analyzeTicket("This is urgent, the system is down");
  assert(r.isUrgent === true, "Expected isUrgent to be true");
});

test("assigns P0 for outage", () => {
  const r = analyzeTicket("Complete outage, nothing is working");
  assert(r.priority === "P0", `Expected P0, got ${r.priority}`);
});

test("custom rule: refund is always P1", () => {
  const r = analyzeTicket("I need a refund for my last payment");
  assert(r.priority === "P1", `Expected P1 for refund, got ${r.priority}`);
});

test("confidence is a number between 0 and 1", () => {
  const r = analyzeTicket("My account is locked and I need help");
  assert(r.confidence >= 0 && r.confidence <= 1, `Confidence out of range: ${r.confidence}`);
});

test("keywords are returned as array", () => {
  const r = analyzeTicket("The payment invoice was incorrect");
  assert(Array.isArray(r.keywords), "Keywords should be an array");
});

test("defaults to P3 for low severity", () => {
  const r = analyzeTicket("It would be nice to add more color themes");
  assert(r.priority === "P3", `Expected P3, got ${r.priority}`);
});

console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
if (failed > 0) process.exit(1);
