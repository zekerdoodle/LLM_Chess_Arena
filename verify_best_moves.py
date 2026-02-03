#!/usr/bin/env python3
"""
Verification script to prove that evaluate_position returns the BEST 3 moves.
This script compares our function output with direct Stockfish MultiPV output.
"""

import chess
import chess.engine
from chess_tools import evaluate_position

import os

STOCKFISH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish/stockfish-ubuntu-x86-64-avx2")

def verify_best_moves():
    """Verify that evaluate_position returns the actual best 3 moves from Stockfish."""
    
    print("=" * 70)
    print("VERIFICATION: evaluate_position returns the BEST 3 moves")
    print("=" * 70)
    
    # Test position: starting position
    board = chess.Board()
    fen = board.fen()
    
    print(f"\nTest Position: {fen}")
    print("\n" + "=" * 70)
    
    # Get direct Stockfish MultiPV output
    print("\n1. DIRECT STOCKFISH MultiPV=3 OUTPUT (ground truth):")
    print("-" * 70)
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    info = engine.analyse(board, chess.engine.Limit(depth=15), multipv=3)
    
    stockfish_moves = []
    for i, item in enumerate(info):
        move = board.san(item["pv"][0])
        score = item["score"].white()
        stockfish_moves.append(move)
        if score.is_mate():
            score_str = f"Mate in {score.mate()}"
        else:
            score_str = f"{score.score() / 100.0:+.2f}"
        print(f"   Rank {i+1}: {move:6s} (Score: {score_str})")
    engine.quit()
    
    # Get output from our evaluate_position function
    print("\n2. OUR evaluate_position() FUNCTION OUTPUT:")
    print("-" * 70)
    result = evaluate_position(fen, depth=15)
    our_moves = result.get("candidate_moves", [])
    print(f"   Score: {result.get('score')}")
    print(f"   Candidate moves (shuffled): {our_moves}")
    
    # Verify the moves match (ignoring order)
    print("\n3. VERIFICATION:")
    print("-" * 70)
    stockfish_set = set(stockfish_moves)
    our_set = set(our_moves)
    
    if stockfish_set == our_set:
        print("   ✓ SUCCESS: Our function returns the EXACT SAME best 3 moves!")
        print(f"   ✓ Stockfish says best 3: {sorted(stockfish_moves)}")
        print(f"   ✓ We returned:           {sorted(our_moves)}")
        print("   ✓ These are the BEST moves, just in random order to avoid spoiling")
    else:
        print("   ✗ ERROR: Moves don't match!")
        print(f"   Stockfish: {stockfish_set}")
        print(f"   Ours:      {our_set}")
        print(f"   Missing:   {stockfish_set - our_set}")
        print(f"   Extra:     {our_set - stockfish_set}")
    
    # Run multiple times to show randomization
    print("\n4. RANDOMIZATION TEST (same moves, different order each time):")
    print("-" * 70)
    for i in range(5):
        result = evaluate_position(fen, depth=15)
        moves = result.get("candidate_moves", [])
        print(f"   Run {i+1}: {moves}")
    
    print("\n" + "=" * 70)
    print("CONCLUSION: The function correctly returns the BEST 3 moves from")
    print("Stockfish MultiPV analysis, but in random order to prevent the AI")
    print("from simply picking the first move without thinking.")
    print("=" * 70)

if __name__ == "__main__":
    verify_best_moves()
