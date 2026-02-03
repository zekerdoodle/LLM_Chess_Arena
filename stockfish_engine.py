"""
Stockfish Engine - Simple Background Analyzer

This module provides a persistent Stockfish analyzer that runs continuously
in the background at high depth with moderate resources. The evaluation is
always available for the live UI bar.

Design:
- Single persistent Stockfish process
- Continuous background analysis at high depth (24+)
- Thread-safe position updates
- Cached results available immediately
- Low CPU priority to avoid interfering with gameplay
"""

import chess
import chess.engine
import threading
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def _find_stockfish() -> str:
    """Find Stockfish binary in common locations"""
    # Check if stockfish is in PATH
    stockfish_in_path = shutil.which("stockfish")
    if stockfish_in_path:
        return stockfish_in_path

    # Check common installation paths
    common_paths = [
        "/usr/games/stockfish",
        "/usr/local/bin/stockfish",
        "/opt/homebrew/bin/stockfish",  # macOS Homebrew
        os.path.expanduser("~/stockfish/stockfish"),
    ]

    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # Default fallback
    return "stockfish"


STOCKFISH_PATH = _find_stockfish()

# Background analyzer settings - doubled resources for faster analysis
ANALYZER_THREADS = 4
ANALYZER_HASH_MB = 1024


@dataclass
class AnalysisResult:
    """Container for analysis results"""
    fen: str
    score: Any  # float or string for mate (e.g. "Mate in 3")
    depth: int
    best_move: str
    pv: List[str]  # Principal variation
    

class StockfishAnalyzer:
    """
    Persistent Stockfish analyzer for live evaluation.
    
    Runs continuously in the background, always analyzing the current position.
    Results are cached and immediately available for the UI.
    """
    
    def __init__(self):
        self._engine: Optional[chess.engine.SimpleEngine] = None
        self._lock = threading.Lock()
        self._position_lock = threading.Lock()
        
        self.current_fen: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        self._cached_result: Optional[AnalysisResult] = None
        
        self._analysis_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._position_changed = threading.Event()
        
        self._start()
    
    def _start(self):
        """Start the engine and analysis thread"""
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            self._engine.configure({
                "Threads": ANALYZER_THREADS,
                "Hash": ANALYZER_HASH_MB
            })
            logger.info(f"Stockfish analyzer started (threads={ANALYZER_THREADS}, hash={ANALYZER_HASH_MB}MB)")
            
            # Start background analysis thread
            self._stop_event.clear()
            self._analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
            self._analysis_thread.start()
            
        except Exception as e:
            logger.error(f"Failed to start Stockfish analyzer: {e}")
            self._engine = None
    
    def _analysis_loop(self):
        """Background thread that continuously analyzes the current position"""
        while not self._stop_event.is_set():
            try:
                if self._engine is None:
                    self._stop_event.wait(1.0)
                    continue
                
                # Get current position
                with self._position_lock:
                    fen = self.current_fen
                
                board = chess.Board(fen)
                
                if board.is_game_over():
                    # No analysis needed for game-over positions
                    self._stop_event.wait(0.5)
                    continue
                
                # Run analysis at high depth
                # Using infinite analysis with periodic checks
                with self._lock:
                    if self._engine is None:
                        continue
                        
                    try:
                        # Analyze to depth 20 (good accuracy, fast updates)
                        info = self._engine.analyse(
                            board, 
                            chess.engine.Limit(depth=30),
                            multipv=1
                        )
                        
                        # Process result
                        if isinstance(info, list):
                            info = info[0]
                        
                        score = info["score"].white()
                        pv = info.get("pv", [])
                        
                        if score.is_mate():
                            mate_in = score.mate()
                            score_val = f"Mate in {mate_in}"
                        else:
                            score_val = score.score() / 100.0  # Convert centipawns to pawns
                        
                        # Convert PV to SAN safely
                        best_move = ""
                        pv_san = []
                        try:
                            if pv:
                                # Verify moves are legal before converting
                                temp_board = board.copy()
                                for m in pv[:5]:
                                    if m in temp_board.legal_moves:
                                        pv_san.append(temp_board.san(m))
                                        temp_board.push(m)
                                    else:
                                        break
                                best_move = pv_san[0] if pv_san else ""
                        except Exception as e:
                            logger.debug(f"PV conversion error: {e}")
                            # Just use UCI if SAN fails
                            best_move = pv[0].uci() if pv else ""
                        
                        result = AnalysisResult(
                            fen=fen,
                            score=score_val,
                            depth=info.get("depth", 0),
                            best_move=best_move,
                            pv=pv_san
                        )
                        
                        # Only cache if position hasn't changed
                        with self._position_lock:
                            if self.current_fen == fen:
                                self._cached_result = result
                                
                    except chess.engine.EngineTerminatedError:
                        logger.warning("Stockfish engine terminated, restarting...")
                        self._engine = None
                        self._restart_engine()
                        
            except Exception as e:
                logger.error(f"Analysis error: {e}")
                self._stop_event.wait(1.0)
    
    def _restart_engine(self):
        """Restart the engine if it died"""
        try:
            if self._engine:
                try:
                    self._engine.quit()
                except:
                    pass
                self._engine = None
            
            self._engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            self._engine.configure({
                "Threads": ANALYZER_THREADS,
                "Hash": ANALYZER_HASH_MB
            })
            logger.info("Stockfish analyzer restarted")
        except Exception as e:
            logger.error(f"Failed to restart Stockfish: {e}")
            self._engine = None
    
    def set_position(self, fen: str):
        """Update the position to analyze"""
        with self._position_lock:
            if self.current_fen != fen:
                self.current_fen = fen
                # Clear cache since position changed
                self._cached_result = None
                logger.debug(f"Position updated: {fen[:50]}...")
    
    def get_cached_result(self) -> Optional[AnalysisResult]:
        """Get the latest cached analysis result"""
        with self._position_lock:
            return self._cached_result
    
    def get_evaluation(self, fen: str, min_depth: int = 10, timeout: float = 5.0) -> Dict[str, Any]:
        """
        Get evaluation for a position.
        
        If the position matches current analysis, returns cached result.
        Otherwise, runs a quick analysis.
        
        Args:
            fen: Position to evaluate
            min_depth: Minimum acceptable depth
            timeout: Max time to wait for result
            
        Returns:
            Dict with score, depth, and candidate_moves
        """
        # Check if we have a cached result for this position
        with self._position_lock:
            if self._cached_result and self._cached_result.fen == fen:
                if self._cached_result.depth >= min_depth:
                    return {
                        "score": self._cached_result.score,
                        "depth": self._cached_result.depth,
                        "candidate_moves": self._cached_result.pv[:3]
                    }
        
        # Need to do a quick analysis
        return self._quick_eval(fen, min_depth, timeout)
    
    def _quick_eval(self, fen: str, depth: int, timeout: float) -> Dict[str, Any]:
        """Quick evaluation for positions not currently being analyzed"""
        if self._engine is None:
            return {"error": "Engine not available"}
        
        board = chess.Board(fen)
        
        try:
            with self._lock:
                if self._engine is None:
                    return {"error": "Engine not available"}
                    
                info = self._engine.analyse(
                    board,
                    chess.engine.Limit(depth=depth, time=timeout),
                    multipv=3
                )
                
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
                
                return {
                    "score": eval_score,
                    "depth": best_info.get("depth", depth),
                    "candidate_moves": top_moves
                }
                
        except Exception as e:
            return {"error": str(e)}
    
    def stop(self):
        """Stop the analyzer"""
        self._stop_event.set()
        if self._engine:
            try:
                self._engine.quit()
            except:
                pass
            self._engine = None
        logger.info("Stockfish analyzer stopped")


# Singleton instance
_analyzer: Optional[StockfishAnalyzer] = None
_analyzer_lock = threading.Lock()


def get_analyzer() -> StockfishAnalyzer:
    """Get the global Stockfish analyzer instance (singleton)"""
    global _analyzer
    with _analyzer_lock:
        if _analyzer is None:
            _analyzer = StockfishAnalyzer()
        return _analyzer


def stop_analyzer():
    """Stop the global analyzer"""
    global _analyzer
    with _analyzer_lock:
        if _analyzer:
            _analyzer.stop()
            _analyzer = None
