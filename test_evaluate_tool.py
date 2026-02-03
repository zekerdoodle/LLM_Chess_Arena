#!/usr/bin/env python3
"""Test the evaluate_position function to verify it returns the best 3 moves."""

from chess_tools import evaluate_position
import chess

def test_evaluate():
    # Test with starting position
    board = chess.Board()
    result = evaluate_position(board.fen(), depth=15)
    
    print("Testing evaluate_position with starting position")
    print(f"FEN: {board.fen()}")
    print(f"\nResult: {result}")
    print(f"\nScore: {result.get('score')}")
    print(f"Depth: {result.get('depth')}")
    print(f"Candidate moves: {result.get('candidate_moves')}")
    print(f"Number of candidates: {len(result.get('candidate_moves', []))}")
    
    # Run it a few times to see if we get different orderings (due to shuffle)
    print("\n" + "="*50)
    print("Running 5 times to verify shuffling:")
    for i in range(5):
        result = evaluate_position(board.fen(), depth=15)
        print(f"Run {i+1}: {result.get('candidate_moves')}")

if __name__ == "__main__":
    test_evaluate()
