import chess.engine
import os

STOCKFISH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish/stockfish-ubuntu-x86-64-avx2")

try:
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    print("Stockfish found and running!")
    engine.quit()
except Exception as e:
    print(f"Error: {e}")
