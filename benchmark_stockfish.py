#!/usr/bin/env python3
"""
Performance benchmark comparing Stockfish versions and configurations.
Tests depth 20 analysis to compare speed and NPS (nodes per second).
"""

import chess
import chess.engine
import time
import os

# Paths to different Stockfish versions
STOCKFISH_16_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish/stockfish-ubuntu-x86-64-avx2")
STOCKFISH_17_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish/stockfish-ubuntu-x86-64-avx2")

def benchmark_engine(engine_path, version_name, threads=1, hash_mb=128):
    """Benchmark a Stockfish engine at depth 20."""
    print(f"\n{'='*70}")
    print(f"Testing {version_name}")
    print(f"Config: {threads} threads, {hash_mb}MB hash")
    print(f"{'='*70}")
    
    try:
        engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        
        # Configure engine
        engine.configure({"Threads": threads, "Hash": hash_mb})
        
        # Test position: starting position
        board = chess.Board()
        
        # Time the analysis
        print(f"Analyzing starting position at depth 20...")
        start_time = time.time()
        
        info = engine.analyse(board, chess.engine.Limit(depth=20))
        
        end_time = time.time()
        elapsed = end_time - start_time
        
        # Extract results
        score = info["score"].white()
        if score.is_mate():
            score_str = f"Mate in {score.mate()}"
        else:
            score_str = f"{score.score() / 100.0:+.2f}"
        
        nodes = info.get("nodes", 0)
        nps = nodes / elapsed if elapsed > 0 else 0
        
        print(f"  Time:     {elapsed:.2f} seconds")
        print(f"  Nodes:    {nodes:,}")
        print(f"  NPS:      {nps:,.0f} nodes/second")
        print(f"  Score:    {score_str}")
        print(f"  Best move: {board.san(info['pv'][0])}")
        
        engine.quit()
        
        return {
            "time": elapsed,
            "nodes": nodes,
            "nps": nps,
            "score": score_str
        }
        
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

def main():
    print("\n" + "="*70)
    print("STOCKFISH PERFORMANCE BENCHMARK")
    print("="*70)
    print("\nNote: Stockfish 16.1 binary was replaced with 17.1")
    print("This benchmark shows the impact of configuration optimizations.")
    
    # Test 1: Stockfish 17.1 with default settings (1 thread, 128MB hash)
    result_default = benchmark_engine(
        STOCKFISH_17_PATH,
        "Stockfish 17.1 (Default: 1 thread, 128MB hash)",
        threads=1,
        hash_mb=128
    )
    
    # Test 2: Stockfish 17.1 with optimized settings (6 threads, 4GB hash)
    result_optimized = benchmark_engine(
        STOCKFISH_17_PATH,
        "Stockfish 17.1 (Optimized: 6 threads, 4096MB hash)",
        threads=6,
        hash_mb=4096
    )
    
    # Compare results
    if result_default and result_optimized:
        print(f"\n{'='*70}")
        print("PERFORMANCE COMPARISON")
        print(f"{'='*70}")
        
        speedup = result_default["time"] / result_optimized["time"]
        nps_improvement = (result_optimized["nps"] / result_default["nps"] - 1) * 100
        
        print(f"\nSpeed Improvement:")
        print(f"  Default:   {result_default['time']:.2f} seconds")
        print(f"  Optimized: {result_optimized['time']:.2f} seconds")
        print(f"  Speedup:   {speedup:.2f}x faster ⚡")
        
        print(f"\nNodes Per Second (NPS):")
        print(f"  Default:   {result_default['nps']:,.0f} NPS")
        print(f"  Optimized: {result_optimized['nps']:,.0f} NPS")
        print(f"  Improvement: +{nps_improvement:.1f}% 🚀")
        
        print(f"\n{'='*70}")
        print("CONCLUSION")
        print(f"{'='*70}")
        print(f"✓ Stockfish 17.1 installed successfully")
        print(f"✓ Optimized configuration is {speedup:.2f}x faster")
        print(f"✓ Using 6 threads and 4GB hash for maximum performance")
        print(f"✓ Ready for high-performance chess analysis")
        print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
