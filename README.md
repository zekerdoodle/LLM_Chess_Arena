# LLM Chess Arena

**Watch AI models battle it out on the 64 squares.**

A live chess arena where Large Language Models play against each other while an AI commentator provides real-time analysis and entertainment. Built as a fun exploration of LLM reasoning capabilities through the lens of chess.

![Chess Battle](https://img.shields.io/badge/LLMs-Playing%20Chess-blue?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11+-green?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

## What is this?

Ever wondered how GPT-4o would fare against Gemini in a chess match? Or whether Claude could find that brilliant sacrifice? This project pits frontier AI models against each other in real-time chess games, complete with:

- **Live Chessboard UI** - A slick, glassmorphism-styled interface that updates in real-time
- **AI Commentary** - A separate model provides hype, analysis, and trash talk for every move
- **Stockfish Evaluation** - Real-time position evaluation bar shows who's winning
- **Multiple Providers** - Support for OpenAI, Google (Gemini), Anthropic, and xAI models
- **Tool Use** - Models can use Stockfish evaluation to inform their moves

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Stockfish

The arena uses Stockfish for position evaluation. Install it for your platform:

**Ubuntu/Debian:**
```bash
sudo apt install stockfish
```

**macOS:**
```bash
brew install stockfish
```

**Windows:**
Download from [stockfishchess.org](https://stockfishchess.org/download/)

### 3. Configure API Keys

```bash
cp .env.example .env
```

Edit `.env` and add your API keys:
```
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
```

### 4. Start the Arena

```bash
python app.py
```

Open `http://localhost:5000` and click **Start Game**!

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                    LLM Chess Arena                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌─────────┐         ┌───────────────┐      ┌─────────┐   │
│   │ White   │ ──────> │  Orchestrator │ <─── │  Black  │   │
│   │ (GPT-4) │         │   (Referee)   │      │(Gemini) │   │
│   └─────────┘         └───────────────┘      └─────────┘   │
│        │                     │                     │        │
│        ▼                     ▼                     ▼        │
│   ┌─────────┐         ┌───────────────┐      ┌─────────┐   │
│   │Stockfish│         │  Commentator  │      │Stockfish│   │
│   │  Tool   │         │   (GPT-4o)    │      │  Tool   │   │
│   └─────────┘         └───────────────┘      └─────────┘   │
│                              │                              │
│                              ▼                              │
│                    ┌─────────────────┐                      │
│                    │    Web UI       │                      │
│                    │  (Real-time)    │                      │
│                    └─────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

The **Orchestrator** manages the game flow:
1. Sends the current position to the active player (White or Black)
2. Player responds with a move and reasoning
3. Commentary model provides analysis
4. UI updates with the move, reasoning, and commentary
5. Repeat until checkmate, stalemate, or draw

Models have access to:
- **Legal Moves** - List of all legal moves in the position
- **Position Evaluation** - Stockfish analysis with candidate moves
- **Move Analysis** - Evaluate specific moves before playing them

## Configuration

Edit `config.yaml` to customize:

```yaml
# Default models (can also be set in the UI)
primary_model: gpt-4o
fallback_model: gemini-2.0-flash
```

The UI allows selecting different models for White, Black, and Commentary before starting a game.

## Project Structure

```
LLM_Chess_Arena/
├── app.py              # Flask web server
├── orchestrator.py     # Game logic and turn management
├── chess_tools.py      # Tools available to the LLMs
├── stockfish_engine.py # Stockfish integration
├── templates/
│   └── index.html      # The beautiful UI
├── layer1_chatbot/     # LLM provider abstraction
└── utils/              # Config, logging utilities
```

## Fun Facts

- Models often develop distinct "personalities" - GPT tends toward solid positional play while Gemini can be more tactical
- The commentary model occasionally gets way too excited about pawn moves
- Sometimes models refuse to resign even in completely lost positions
- Tool use is optional - some models play better when they trust their own evaluation

## License

MIT - Do whatever you want with it. If you make something cool, let me know!

---

*Built by Zeke Moon | A fun side project exploring AI capabilities*
