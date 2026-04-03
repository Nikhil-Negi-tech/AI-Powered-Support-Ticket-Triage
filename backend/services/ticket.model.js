// services/ticket.model.js

const mongoose = require("mongoose");

const ticketSchema = new mongoose.Schema(
  {
    message: { type: String, required: true },
    category: String,
    priority: String,
    isUrgent: Boolean,
    keywords: [String],
    confidence: Number
  },
  { timestamps: true }
);

module.exports = mongoose.model("Ticket", ticketSchema);
