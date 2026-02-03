# LLM Chess Arena

A battleground for Large Language Models to play Chess against each other, featuring a live Web UI and AI Commentary.

## Features
- **Players**: GPT-5.1-Thinking (White) vs Gemini-3-Pro (Black).
- **Live UI**: Watch the game unfold on a real-time chessboard.
- **Commentary**: AI-generated hype and analysis for every move.
- **Tools**: Stockfish evaluation and Web Search for opening theory.

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration**:
   - Ensure your `.env` file contains the necessary API keys:
     - `OPENAI_API_KEY`
     - `GOOGLE_API_KEY`
     - `ANTHROPIC_API_KEY` (if used)
   - Check `config.yaml` for model settings.

## Running the Arena

1. **Start the Server**:
   ```bash
   python app.py
   ```

2. **Watch the Game**:
   - Open your browser and navigate to: `http://localhost:5000`
   - Click the **Start Game** button.

## Files
- `app.py`: The Flask web server.
- `orchestrator.py`: The game engine and referee.
- `chess_tools.py`: Tools for the LLMs.
- `templates/index.html`: The frontend UI.
