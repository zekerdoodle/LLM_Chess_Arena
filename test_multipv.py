import chess
import chess.engine

import os

STOCKFISH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish/stockfish-ubuntu-x86-64-avx2")

def test_multipv():
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    board = chess.Board()
    
    print("Testing multipv=3...")
    info = engine.analyse(board, chess.engine.Limit(depth=10), multipv=3)
    
    print(f"Type of info: {type(info)}")
    if isinstance(info, list):
        print(f"Length of info: {len(info)}")
        for i, item in enumerate(info):
            print(f"Item {i}: {item.get('pv')[0] if 'pv' in item else 'No PV'} - Score: {item.get('score')}")
    else:
        print("Info is not a list.")
        print(info)

    engine.quit()

if __name__ == "__main__":
    test_multipv()
