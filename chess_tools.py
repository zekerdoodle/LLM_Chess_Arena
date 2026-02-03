import chess
import chess.engine
import chess.pgn
import os
import random
import threading
from typing import List, Optional, Dict, Any
from layer4_tools.web_search_tools import web_search

# Path to Stockfish 17.1 binary (optimized AVX2 build)
STOCKFISH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish/stockfish-ubuntu-x86-64-avx2")

# Engine settings for quick analysis (model tools)
# Doubled resources for faster tool responses
QUICK_ENGINE_THREADS = 2
QUICK_ENGINE_HASH_MB = 256
QUICK_ANALYSIS_DEPTH = 15

def get_legal_moves(fen: str) -> List[str]:
    """
    Returns a list of legal moves in SAN format for the given FEN.
    """
    board = chess.Board(fen)
    moves = [board.san(move) for move in board.legal_moves]
    random.shuffle(moves)
    return moves


# Reusable engine for model tool calls
_tool_engine: Optional[chess.engine.SimpleEngine] = None
_tool_engine_lock = threading.Lock()


def _get_tool_engine() -> Optional[chess.engine.SimpleEngine]:
    """Get or create a lightweight engine for model tool calls"""
    global _tool_engine
    with _tool_engine_lock:
        if _tool_engine is None:
            try:
                _tool_engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
                _tool_engine.configure({
                    "Threads": QUICK_ENGINE_THREADS, 
                    "Hash": QUICK_ENGINE_HASH_MB
                })
            except Exception as e:
                return None
        return _tool_engine


def evaluate_position(fen: str, depth: int = 15) -> Dict[str, Any]:
    """
    Evaluates the position using a lightweight Stockfish instance.
    
    This is designed for model tool calls - quick analysis that doesn't
    interfere with the background analyzer running for the UI eval bar.
    
    Returns a dictionary with score, depth, and candidate moves.
    """
    engine = _get_tool_engine()
    
    if engine is None:
        # Fallback: spawn fresh engine
        return _evaluate_position_fallback(fen, depth)
    
    board = chess.Board(fen)
    
    try:
        with _tool_engine_lock:
            # Use MultiPV to get top 3 moves
            info = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=3)
            
            if isinstance(info, list):
                best_info = info[0]
                top_moves = [board.san(item["pv"][0]) for item in info if "pv" in item and item["pv"]]
            else:
                best_info = info
                top_moves = [board.san(best_info["pv"][0])] if best_info.get("pv") else []

            score = best_info["score"].white()
            
            if score.is_mate():
                eval_score = f"Mate in {score.mate()}"
            else:
                eval_score = score.score() / 100.0
                
            random.shuffle(top_moves)

            return {
                "score": eval_score,
                "depth": best_info.get("depth", depth),
                "candidate_moves": top_moves
            }
    except chess.engine.EngineTerminatedError:
        # Engine died, reset it
        global _tool_engine
        with _tool_engine_lock:
            _tool_engine = None
        return _evaluate_position_fallback(fen, depth)
    except Exception as e:
        return {"error": str(e)}


def _evaluate_position_fallback(fen: str, depth: int = 15) -> Dict[str, Any]:
    """
    Fallback evaluation using a fresh Stockfish instance.
    Used when the reusable engine is unavailable or fails.
    """
    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        engine.configure({"Threads": QUICK_ENGINE_THREADS, "Hash": QUICK_ENGINE_HASH_MB})
    except FileNotFoundError:
        return {"error": "Stockfish engine not found at " + STOCKFISH_PATH}
    except Exception as e:
        return {"error": f"Failed to initialize Stockfish: {e}"}

    board = chess.Board(fen)
    try:
        # Use MultiPV to get top 3 moves
        info = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=3)
        
        if isinstance(info, list):
            best_info = info[0]
            top_moves = [board.san(item["pv"][0]) for item in info if "pv" in item]
        else:
            best_info = info
            top_moves = [board.san(best_info["pv"][0])] if "pv" in best_info else []

        score = best_info["score"].white()
        
        if score.is_mate():
            eval_score = f"Mate in {score.mate()}"
        else:
            eval_score = score.score() / 100.0
            
        random.shuffle(top_moves)

        return {
            "score": eval_score,
            "depth": depth,
            "candidate_moves": top_moves
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        engine.quit()

def analyze_move(fen: str, move_san: str) -> Dict[str, Any]:
    """
    Analyzes a specific move for the given position.
    Returns the evaluation score after the move.
    
    Uses the shared tool engine for quick analysis.
    """
    board = chess.Board(fen)
    try:
        move = board.parse_san(move_san)
        if move not in board.legal_moves:
            return {"error": f"Illegal move: {move_san}"}
            
        board.push(move)
        
        # Use the tool engine for analysis
        engine = _get_tool_engine()
        
        if engine is None:
            return _analyze_move_fallback(board, move_san)
        
        try:
            with _tool_engine_lock:
                info = engine.analyse(board, chess.engine.Limit(depth=QUICK_ANALYSIS_DEPTH))
                score = info["score"].white()
                
                if score.is_mate():
                    eval_score = f"Mate in {score.mate()}"
                else:
                    eval_score = score.score() / 100.0
                    
                return {
                    "move": move_san,
                    "score_after": eval_score,
                    "depth": info.get("depth", QUICK_ANALYSIS_DEPTH)
                }
        except chess.engine.EngineTerminatedError:
            global _tool_engine
            with _tool_engine_lock:
                _tool_engine = None
            return _analyze_move_fallback(board, move_san)
        
    except ValueError as e:
        return {"error": f"Invalid move '{move_san}': {e}"}
    except Exception as e:
        return {"error": str(e)}


def _analyze_move_fallback(board: chess.Board, move_san: str) -> Dict[str, Any]:
    """Fallback: spawn a fresh engine for one-off analysis"""
    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        engine.configure({"Threads": QUICK_ENGINE_THREADS, "Hash": QUICK_ENGINE_HASH_MB})
    except Exception as e:
        return {"error": f"Failed to initialize Stockfish: {e}"}

    try:
        info = engine.analyse(board, chess.engine.Limit(depth=QUICK_ANALYSIS_DEPTH))
        score = info["score"].white()
        
        if score.is_mate():
            eval_score = f"Mate in {score.mate()}"
        else:
            eval_score = score.score() / 100.0
            
        return {
            "move": move_san,
            "score_after": eval_score,
            "depth": info.get("depth", QUICK_ANALYSIS_DEPTH)
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        engine.quit()


async def web_search_tool(query: str) -> str:
    """
    Performs a web search using the existing tool.
    """
    return await web_search(query=query)
