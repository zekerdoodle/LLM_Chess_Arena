import threading
import time
import os
import logging
import json
import chess.pgn
from flask import Flask, render_template, jsonify, request, Response
from flask_cors import CORS
from orchestrator import ChessOrchestrator
from stockfish_engine import get_analyzer, AnalysisResult
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Suppress Flask/Werkzeug HTTP request logs (reduce console noise)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app = Flask(__name__)
CORS(app)

# Global state
orchestrator = None
game_thread = None
game_running = False

def run_orchestrator_loop():
    global orchestrator, game_running
    asyncio.run(orchestrator.run_game())
    game_running = False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/start', methods=['POST'])
def start_game():
    global orchestrator, game_thread, game_running
    
    if game_running:
        return jsonify({"status": "error", "message": "Game already running"}), 400
        
    game_config = request.json or {}
    logger.info(f"Starting game with config: {game_config}")
        
    orchestrator = ChessOrchestrator(game_config)
    game_running = True
    
    # Check for PGN parse error
    pgn_parse_error = orchestrator.pgn_parse_error
    
    game_thread = threading.Thread(target=run_orchestrator_loop)
    game_thread.start()
    
    response = {"status": "success", "message": "Game started"}
    if pgn_parse_error:
        response["pgn_parse_error"] = pgn_parse_error
    
    return jsonify(response)

@app.route('/api/stop', methods=['POST'])
def stop_game_endpoint():
    global orchestrator, game_running
    
    if orchestrator:
        orchestrator.stop_game()
        game_running = False
        return jsonify({"status": "success", "message": "Game stopping..."})
    
    return jsonify({"status": "error", "message": "No game running"}), 400

@app.route('/api/state')
def get_state():
    global orchestrator
    if not orchestrator:
        return jsonify({
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "last_move": None,
            "reasoning": None,
            "commentary": "Waiting to start...",
            "turn": "White",
            "history": [],
            "game_over": False,
            "result": None,
            "material": {"white": 0, "black": 0},
            "move_number": 0
        })
    
    # We need to expose state from orchestrator
    # This requires modifying orchestrator.py to have a 'get_state()' method
    # or accessing its attributes directly if thread-safe enough for reading
    
    # Use the new get_ui_state method
    state = orchestrator.get_ui_state()
    return jsonify(state)


@app.route('/api/pgn')
def get_pgn():
    """Get the current game as PGN string with time annotations"""
    global orchestrator
    if not orchestrator:
        return jsonify({"pgn": ""})
    
    # Use the orchestrator's method to get PGN with time annotations
    pgn_string = orchestrator.get_pgn_string()
    
    return jsonify({"pgn": pgn_string})


@app.route('/api/eval-stream')
def eval_stream():
    """
    Server-Sent Events endpoint for live evaluation updates.
    Streams evaluation data from the persistent StockfishAnalyzer.
    
    The analyzer runs continuously at high depth (28) with moderate resources,
    so the UI always shows accurate evaluation.
    """
    def generate():
        try:
            analyzer = get_analyzer()
        except Exception as e:
            logger.error(f"Failed to get analyzer: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return
            
        last_data = None
        last_fen = None
        
        # Send initial heartbeat
        yield f"data: {json.dumps({'status': 'connected'})}\n\n"
        
        while True:
            try:
                # Get current position from orchestrator or use analyzer's current
                current_fen = None
                if orchestrator and hasattr(orchestrator, 'board'):
                    current_fen = orchestrator.board.fen()
                
                # Update analyzer position if changed
                if current_fen and current_fen != last_fen:
                    analyzer.set_position(current_fen)
                    last_fen = current_fen
                    # Clear last_data to force sending new result
                    last_data = None
                
                # Get current cached result from background analysis
                result = analyzer.get_cached_result()
                
                # Only use result if it matches the current position
                if result and result.fen == analyzer.current_fen:
                    # Format score for frontend (centipawns)
                    if isinstance(result.score, str):
                        # Mate score
                        if "Mate" in result.score:
                            mate_num = result.score.replace("Mate in ", "")
                            try:
                                mate_val = int(mate_num)
                                score_val = 10000 if mate_val > 0 else -10000
                            except:
                                score_val = 10000 if "-" not in result.score else -10000
                        else:
                            score_val = 0
                        score_display = result.score
                    else:
                        score_val = result.score * 100  # Convert pawns to centipawns
                        score_display = f"{result.score:+.2f}"
                    
                    data = {
                        "score": score_val,
                        "score_display": score_display,
                        "depth": result.depth,
                        "best_move": result.best_move,
                        "pv": result.pv,
                        "fen": result.fen
                    }
                    
                    # Only send if data changed
                    data_str = json.dumps(data)
                    if data_str != last_data:
                        last_data = data_str
                        yield f"data: {data_str}\n\n"
                else:
                    # No result yet, send status
                    yield f"data: {json.dumps({'status': 'analyzing', 'depth': 0})}\n\n"
                
                time.sleep(0.2)  # 5 updates per second
            except GeneratorExit:
                # Client disconnected
                break
            except Exception as e:
                logger.error(f"SSE error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                time.sleep(1)
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'  # Disable nginx buffering
        }
    )


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
