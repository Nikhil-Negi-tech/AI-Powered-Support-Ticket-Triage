// server.js

const express = require("express");
const cors = require("cors");
const mongoose = require("mongoose");

const ticketRoutes = require("./routes/tickets");

const app = express();
const PORT = process.env.PORT || 5000;
const MONGO_URI = process.env.MONGO_URI || "mongodb+srv://neginikhilsingh6_db_user:BvVj1xeKalteVKDx@cluster0.8szttyy.mongodb.net/?appName=Cluster0";

app.use(cors());
app.use(express.json());

app.use("/tickets", ticketRoutes);

// Health check
app.get("/", (req, res) => res.json({ status: "ok" }));

mongoose
  .connect(MONGO_URI)
  .then(() => {
    console.log("Connected to MongoDB");
    app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
  })
  .catch(err => {
    console.error("MongoDB connection failed:", err.message);
    process.exit(1);
  });
