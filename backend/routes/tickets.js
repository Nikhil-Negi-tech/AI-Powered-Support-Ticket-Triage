// routes/tickets.js

const express = require("express");
const router = express.Router();
const Ticket = require("../services/ticket.model");
const { analyzeTicket } = require("../services/analyzer");

// POST /tickets/analyze
router.post("/analyze", async (req, res) => {
  const { message } = req.body;

  if (!message || message.trim().length === 0) {
    return res.status(400).json({ error: "Message is required." });
  }

  if (message.length > 2000) {
    return res.status(400).json({ error: "Message too long (max 2000 chars)." });
  }

  try {
    const result = analyzeTicket(message);
    const ticket = await Ticket.create({ message, ...result });
    res.status(201).json(ticket);
  } catch (err) {
    res.status(500).json({ error: "Server error. Please try again." });
  }
});

// GET /tickets
router.get("/", async (req, res) => {
  try {
    const tickets = await Ticket.find().sort({ createdAt: -1 }).limit(50);
    res.json(tickets);
  } catch (err) {
    res.status(500).json({ error: "Could not fetch tickets." });
  }
});

module.exports = router;
