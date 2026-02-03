import asyncio
import os
import json
import logging
import chess
import chess.pgn
import datetime
import time
import io
from typing import List, Dict, Any, Optional

# Add project root to path
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from layer1_chatbot.model_call_google import GoogleModelCall
from layer1_chatbot.model_call_openai import OpenAIModelCall
from layer1_chatbot.model_call_xai import XAIModelCall
from chess_tools import get_legal_moves, evaluate_position, web_search_tool, analyze_move
from stockfish_engine import get_analyzer
from utils.config_loader import load_config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
GEMINI_MODEL = "gemini-3-pro-preview"
OPENAI_MODEL = "gpt-5" # Fallback to standard GPT-5
MAX_RETRIES = 3
MAX_MOVES = 100 # Full game limit

# Move Schema
MOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string", "description": "Explanation for the move"},
        "move": {"type": "string", "description": "The move in UCI or SAN format"}
    },
    "required": ["reasoning", "move"],
    "additionalProperties": False
}

class ChessOrchestrator:
    def __init__(self, game_config: Optional[Dict[str, Any]] = None):
        self.game_config = game_config or {}
        self.pgn_parse_error = None  # Track PGN parsing errors for UI feedback
        
        # Initialize board - will be updated if PGN is provided
        self.board = chess.Board()
        
        self.config = load_config()
        logger.info(f"Loaded config keys: {self.config.keys()}")
        self.gemini_client = GoogleModelCall()
        self.openai_client = OpenAIModelCall()
        self.xai_client = XAIModelCall()
        self.game_log = []  # List of {"turn": str, "move": str, "fen": str, "time_seconds": float}
        self.pgn_game = chess.pgn.Game()
        self.pgn_game.headers["Event"] = "LLM Chess Arena"
        self.pgn_game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
        
        # Set player names based on config (always override imported PGN names)
        self.white_model_name = self.game_config.get("white_model", OPENAI_MODEL)
        self.black_model_name = self.game_config.get("black_model", GEMINI_MODEL)
        self.commentary_model_name = self.game_config.get("commentary_model", "gpt-5-mini")
        
        self.pgn_game.headers["White"] = self.white_model_name
        self.pgn_game.headers["Black"] = self.black_model_name
        
        self.node = self.pgn_game
        
        # State for UI
        self.last_move_info = {}
        self.last_commentary = ""
        self.current_eval = 0.0 # Centipawns, positive for white
        self.stop_requested = False
        self.forced_game_over = False
        
        # Reasoning history for each player (memory of their strategic thinking)
        self.white_reasoning_history = []  # List of {"move": str, "reasoning": str}
        self.black_reasoning_history = []  # List of {"move": str, "reasoning": str}
        
        # Commentary history (memory for the commentator)
        self.commentary_history = []  # List of {"player": str, "move": str, "commentary": str}
        
        # Move timing
        self.white_total_time = 0.0  # Total seconds spent by white
        self.black_total_time = 0.0  # Total seconds spent by black
        
        # Import PGN if provided
        start_pgn = self.game_config.get("start_pgn", "").strip()
        if start_pgn:
            self._import_pgn(start_pgn)
        
        # Initialize the shared Stockfish analyzer with starting position
        self._init_analyzer()
    
    def _import_pgn(self, pgn_string: str):
        """Import a PGN string to continue from that position"""
        try:
            pgn_io = io.StringIO(pgn_string)
            imported_game = chess.pgn.read_game(pgn_io)
            
            if not imported_game:
                self.pgn_parse_error = "Could not parse PGN. Starting fresh game."
                logger.error(self.pgn_parse_error)
                return
            
            # Replay all moves to build board state and history
            self.board = imported_game.board()  # Start from the game's starting position
            move_num = 0
            
            for node in imported_game.mainline():
                move = node.move
                turn = "White" if self.board.turn == chess.WHITE else "Black"
                san_move = self.board.san(move)
                
                # Add to our PGN game
                self.node = self.node.add_variation(move)
                
                # Copy any time comments from imported PGN
                if node.comment:
                    self.node.comment = node.comment
                
                # Build reasoning history with placeholder for imported moves
                if self.board.turn == chess.WHITE:
                    self.white_reasoning_history.append({
                        "move": san_move,
                        "reasoning": "(imported move)"
                    })
                else:
                    self.black_reasoning_history.append({
                        "move": san_move,
                        "reasoning": "(imported move)"
                    })
                
                # Add to game log (no timing for imported moves)
                self.game_log.append({
                    "turn": turn,
                    "move": san_move,
                    "fen": self.board.fen(),
                    "time_seconds": 0.0  # Unknown for imported moves
                })
                
                # Apply move
                self.board.push(move)
                move_num += 1
            
            logger.info(f"Imported PGN with {move_num} moves. Current position: {self.board.fen()}")
            
        except Exception as e:
            self.pgn_parse_error = f"Error parsing PGN: {str(e)}. Starting fresh game."
            logger.error(self.pgn_parse_error)
            # Reset to fresh game
            self.board = chess.Board()
            self.pgn_game = chess.pgn.Game()
            self.pgn_game.headers["Event"] = "LLM Chess Arena"
            self.pgn_game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
            self.pgn_game.headers["White"] = self.white_model_name
            self.pgn_game.headers["Black"] = self.black_model_name
            self.node = self.pgn_game
            self.game_log = []
            self.white_reasoning_history = []
            self.black_reasoning_history = []

    def _init_analyzer(self):
        """Initialize the shared Stockfish analyzer with current position"""
        try:
            analyzer = get_analyzer()
            analyzer.set_position(self.board.fen())
            logger.info("Shared Stockfish analyzer initialized")
        except Exception as e:
            logger.error(f"Failed to initialize analyzer: {e}")

    def stop_game(self):
        self.stop_requested = True
        logger.info("Game stop requested.")

    async def run_game(self):
        logger.info("Starting LLM Chess Arena Game")
        logger.info(f"White: {self.pgn_game.headers['White']}")
        logger.info(f"Black: {self.pgn_game.headers['Black']}")

        while not self.board.is_game_over() and self.board.fullmove_number <= MAX_MOVES:
            if self.stop_requested:
                logger.info("Game stopped by user.")
                self.forced_game_over = True
                break
                
            fen = self.board.fen()
            turn = "White" if self.board.turn == chess.WHITE else "Black"
            logger.info(f"\n--- Turn: {turn} (Move {self.board.fullmove_number}) ---")
            logger.info(f"FEN: {fen}")
            
            # Select player
            if self.board.turn == chess.WHITE:
                player_name = "White (" + self.white_model_name + ")"
                model_name = self.white_model_name
            else:
                player_name = "Black (" + self.black_model_name + ")"
                model_name = self.black_model_name
                
            # Determine client based on model name
            if "grok" in model_name.lower() or model_name.lower().startswith("xai"):
                model_call = self.xai_client
            elif "gpt" in model_name.lower() or "o1" in model_name.lower():
                model_call = self.openai_client
            elif "gemini" in model_name.lower():
                model_call = self.gemini_client
            else:
                # Default fallback
                model_call = self.openai_client

            # Get move with timing
            start_time = time.time()
            move_san = await self.get_move_from_player(player_name, model_call, model_name)
            elapsed_seconds = time.time() - start_time
            
            if move_san:
                # Track time for each player
                if self.board.turn == chess.WHITE:
                    self.white_total_time += elapsed_seconds
                else:
                    self.black_total_time += elapsed_seconds
                
                # Apply move
                move = self.board.parse_san(move_san)
                self.board.push(move)
                self.node = self.node.add_variation(move)
                
                # Add time annotation to PGN node (clock format H:MM:SS)
                hours = int(elapsed_seconds // 3600)
                minutes = int((elapsed_seconds % 3600) // 60)
                seconds = int(elapsed_seconds % 60)
                self.node.comment = f"[%emt {hours}:{minutes:02d}:{seconds:02d}]"
                
                self.game_log.append({
                    "turn": turn,
                    "move": move_san,
                    "fen": self.board.fen(),
                    "time_seconds": elapsed_seconds
                })
                
                logger.info(f"Move took {elapsed_seconds:.1f}s")
                
                # Generate Commentary
                reasoning = self.last_move_info.get("reasoning", "")
                await self.generate_commentary(player_name, move_san, reasoning)
                
                # Notify the shared analyzer of the new position
                # The analyzer will start deep analysis automatically
                # and the SSE endpoint will stream updates to the UI
                try:
                    analyzer = get_analyzer()
                    analyzer.set_position(self.board.fen())
                    
                    # Also update current_eval from cached result if available
                    cached = analyzer.get_cached_result()
                    if cached and cached.fen == self.board.fen():
                        self.current_eval = cached.score
                        logger.info(f"Eval from cache: {self.current_eval} (depth {cached.depth})")
                    else:
                        # Get quick evaluation for state endpoint
                        eval_result = evaluate_position(self.board.fen(), depth=10)
                        if "score" in eval_result:
                            self.current_eval = eval_result["score"]
                            logger.info(f"Eval: {self.current_eval}")
                except Exception as e:
                    logger.error(f"Eval failed: {e}")

                logger.info(f"Move applied: {move_san}")
            else:
                logger.error(f"Player {player_name} failed to provide a valid move. Game aborted.")
                break

        logger.info("\n--- Game Over ---")
        logger.info(f"Result: {self.board.result()}")
        self.pgn_game.headers["Result"] = self.board.result()
        
        # Save PGN
        with open("game.pgn", "w") as f:
            exporter = chess.pgn.FileExporter(f)
            self.pgn_game.accept(exporter)
        logger.info("Game saved to game.pgn")

    async def get_move_from_player(self, player_name, model_call, model_name):
        retries = 0
        error_msg = ""
        
        while retries < MAX_RETRIES:
            prompt = self.construct_prompt(player_name, error_msg)
            
            # Define tools
            all_tools = [
                {
                    "name": "get_legal_moves",
                    "description": "Get a list of legal moves for the current position.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fen": {"type": "string", "description": "The FEN string of the position."}
                        },
                        "required": ["fen"]
                    }
                },
                {
                    "name": "evaluate_position",
                    "description": "Evaluate the current position using Stockfish. Returns score (in pawns, + is White advantage) and the BEST 3 candidate moves (in random order to avoid giving away the top move).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "description": "The FEN string of the position."},
                        "depth": {"type": "integer", "description": "Search depth (default 10)."}
                    },
                    "required": ["fen"]
                }
            },
            {
                "name": "analyze_move",
                "description": "Evaluate a specific move to see how it changes the position's score (in pawns).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "description": "The FEN string of the position."},
                        "move_san": {"type": "string", "description": "The move to analyze in SAN format (e.g. 'e4', 'Nf3')."}
                    },
                    "required": ["fen", "move_san"]
                }
            },
            {
                "name": "web_search",
                "description": "Search the web for chess openings or strategy.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."}
                    },
                    "required": ["query"]
                }
            }
            ]
            
            # Filter tools based on config
            enabled_tools = self.game_config.get("tools", ["get_legal_moves", "evaluate_position", "analyze_move", "web_search"])
            tools = [t for t in all_tools if t["name"] in enabled_tools]
            
            if self.stop_requested:
                return None

            try:
                # Call model
                logger.info(f"Requesting move from {player_name}...")
                
                # Prepare args
                kwargs = {
                    "prompt": prompt,
                    "api_key": model_call._get_api_key(self.config),
                    "model": model_name,
                    "stream": False,
                    "tools": tools,
                    "text": {"format": {"type": "json_schema", "schema": MOVE_SCHEMA}}
                }
                
                # Handle model specific args
                if "grok" in model_name.lower() or "xai" in model_name.lower():
                    # xAI/Grok models use text_format with json_schema
                    # Per xAI docs, structured outputs need type: json_schema with json_schema wrapper
                    import copy
                    clean_schema = copy.deepcopy(MOVE_SCHEMA)
                    # xAI doesn't support additionalProperties in schema
                    if "additionalProperties" in clean_schema:
                        del clean_schema["additionalProperties"]
                    kwargs["text_format"] = {
                        "type": "json_schema",
                        "name": "chess_move_schema",
                        "schema": clean_schema
                    }
                    del kwargs["text"]
                elif "gpt" in model_name.lower():
                     # OpenAI Responses API requires 'name' for the format when using json_schema?
                     # Or maybe the schema wrapper needs to be different.
                     # Trying to add 'name' to the format dict itself.
                     kwargs["text_format"] = {
                         "type": "json_schema", 
                         "name": "chess_move_schema",
                         "schema": MOVE_SCHEMA,
                         "strict": True
                     }
                     del kwargs["text"]
                     
                     # Set reasoning effort for GPT-5.1 models (all variants)
                     # This applies to gpt-5.1, gpt-5.1-mini, gpt-5.1-nano, etc.
                     if "5.1" in model_name.lower() or "5-1" in model_name.lower():
                         kwargs["reasoning"] = {"effort": "medium"}
                elif "gemini" in model_name or "google" in model_name:
                    # Google doesn't like additionalProperties
                    import copy
                    clean_schema = copy.deepcopy(MOVE_SCHEMA)
                    if "additionalProperties" in clean_schema:
                        del clean_schema["additionalProperties"]
                    kwargs["text"] = {"format": {"type": "json_schema", "schema": clean_schema}}

                    # Set thinking level for Gemini 3
                    if "gemini-3" in model_name.lower():
                        kwargs["thinking_config"] = {
                            "thinking_level": "high",
                            "include_thoughts": True
                        }

                response = await model_call._make_api_call(**kwargs)
                
                if self.stop_requested:
                    return None

                # Handle tool calls and response
                # Loop to handle multiple rounds of tool calls (e.g., model chains tools)
                # Increased to 20 to allow models to perform thorough chess analysis
                MAX_TOOL_ROUNDS = 20
                tool_round = 0
                
                while tool_round < MAX_TOOL_ROUNDS:
                    # Check for tool calls
                    tool_calls = self.extract_tool_calls(response, model_name)
                    
                    if not tool_calls:
                        # No more tool calls, we should have the final response
                        break
                    
                    logger.info(f"Tool calls detected (round {tool_round + 1}): {len(tool_calls)}")
                    tool_results = await self.execute_tool_calls(tool_calls)
                    
                    # Prepare for next round with tool results
                    kwargs["tool_results"] = tool_results
                    kwargs["google_history"] = [] # TODO: Construct proper history if needed
                    
                    # Pass previous response context for all providers
                    kwargs["google_prev_response"] = response
                    kwargs["xai_prev_response"] = response
                    
                    # For OpenAI and xAI, pass the previous response ID if available
                    if hasattr(response, "id"):
                        kwargs["previous_response_id"] = response.id
                    elif isinstance(response, dict) and "id" in response:
                        kwargs["previous_response_id"] = response["id"]
                    
                    logger.info("Sending tool results back to model...")
                    response = await model_call._make_api_call(**kwargs)
                    
                    if self.stop_requested:
                        return None
                    
                    tool_round += 1
                
                if tool_round >= MAX_TOOL_ROUNDS:
                    logger.warning(f"Reached max tool rounds ({MAX_TOOL_ROUNDS}), stopping tool loop")
                
                # Extract move from response
                move_data = self.extract_move_data(response, model_name)
                
                if move_data:
                    move_str = move_data.get("move")
                    reasoning = move_data.get("reasoning")
                    
                    # Store for UI
                    self.last_move_info = {
                        "move": move_str,
                        "reasoning": reasoning,
                        "player": player_name
                    }
                    
                    logger.info(f"Reasoning: {reasoning}")
                    
                    # Validate move
                    try:
                        move = self.board.parse_san(move_str)
                        # Save reasoning to history for this player
                        if "White" in player_name:
                            self.white_reasoning_history.append({"move": move_str, "reasoning": reasoning or ""})
                        else:
                            self.black_reasoning_history.append({"move": move_str, "reasoning": reasoning or ""})
                        return move_str
                    except ValueError:
                        # Try parsing as UCI
                        try:
                            move = self.board.parse_uci(move_str)
                            san_move = self.board.san(move)
                            # Save reasoning to history for this player
                            if "White" in player_name:
                                self.white_reasoning_history.append({"move": san_move, "reasoning": reasoning or ""})
                            else:
                                self.black_reasoning_history.append({"move": san_move, "reasoning": reasoning or ""})
                            return san_move
                        except ValueError:
                            error_msg = f"Invalid move '{move_str}'. It is illegal or malformed. Legal moves: {self.get_legal_moves_str()}"
                            logger.warning(error_msg)
                else:
                    error_msg = "Failed to parse structured output."
                    logger.warning(error_msg)

            except Exception as e:
                logger.error(f"Error calling {player_name}: {e}")
                error_msg = f"System error: {e}"

            retries += 1
            logger.info(f"Retry {retries}/{MAX_RETRIES}")

        return None

    def construct_prompt(self, player_name, error_msg=""):
        fen = self.board.fen()
        legal_moves = self.get_legal_moves_str()
        
        # Get this player's reasoning history
        if "White" in player_name:
            reasoning_history = self.white_reasoning_history
        else:
            reasoning_history = self.black_reasoning_history
        
        # Build reasoning history string (last 10 moves to avoid context bloat)
        history_str = ""
        if reasoning_history:
            recent_history = reasoning_history[-10:]  # Keep last 10 moves
            history_lines = []
            for i, entry in enumerate(recent_history, 1):
                move_num = len(reasoning_history) - len(recent_history) + i
                history_lines.append(f"  {move_num}. {entry['move']}: {entry['reasoning'][:200]}{'...' if len(entry['reasoning']) > 200 else ''}")
            history_str = "\n".join(history_lines)
        
        # Get optional custom additions to the prompt
        custom_addition = ""
        if "White" in player_name:
            custom_addition = self.game_config.get("white_prompt", "")
        else:
            custom_addition = self.game_config.get("black_prompt", "")
        
        # Build prompt - base instructions are ALWAYS included
        prompt = f"""
You are playing a game of Chess. You are {player_name}.
Current FEN: {fen}
Legal Moves: {legal_moves}
"""
        # Add reasoning history if available
        if history_str:
            prompt += f"""
Your previous moves and reasoning (use this to maintain strategic continuity):
{history_str}
"""
        
        prompt += """
Your goal is to win the game.

You may or may not have tools available to help you analyze the position. If tools are available, use them strategically:
- get_legal_moves: Verify legal moves if uncertain
- evaluate_position: Get Stockfish evaluation and top 3 candidate moves (returned in random order)
- analyze_move: Test how a specific move affects the position score
- web_search: Look up opening theory or strategic ideas

Use whatever resources are available to you — your own chess knowledge, any provided tools, and your memory of previous reasoning — to make the best move.
Consider your previous reasoning to maintain strategic plans across moves.
"""
        
        # Append user's custom additions if provided
        if custom_addition:
            prompt += f"\n\nAdditional instructions:\n{custom_addition}\n"
        if error_msg:
            prompt += f"\n\nPREVIOUS ERROR: {error_msg}\nPlease fix this error and provide a valid move."
            
        return prompt

    def get_legal_moves_str(self):
        return ", ".join([self.board.san(m) for m in self.board.legal_moves])

    def extract_tool_calls(self, response, model_name):
        calls = []
        if "gemini" in model_name or "google" in model_name:
            # Google response handling
            try:
                if hasattr(response, "candidates") and response.candidates:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            calls.append({
                                "name": part.function_call.name,
                                "args": dict(part.function_call.args),
                                "id": "google_call" # Google doesn't use IDs in the same way
                            })
            except Exception:
                pass
        elif "grok" in model_name.lower() or "xai" in model_name.lower():
            # xAI/Grok response handling - uses XAIResponse wrapper
            try:
                # XAIResponse has tool_calls attribute directly
                if hasattr(response, "tool_calls") and response.tool_calls:
                    for tool_call in response.tool_calls:
                        if isinstance(tool_call, dict):
                            func = tool_call.get("function", {})
                            name = func.get("name") if isinstance(func, dict) else None
                            arguments = func.get("arguments") if isinstance(func, dict) else None
                            call_id = tool_call.get("id", "xai_call")
                        else:
                            # Handle object-style access
                            func_obj = getattr(tool_call, "function", None)
                            name = getattr(func_obj, "name", None)
                            arguments = getattr(func_obj, "arguments", None)
                            call_id = getattr(tool_call, "id", "xai_call")
                        
                        if name:
                            # Parse arguments string to dict
                            args = {}
                            if arguments:
                                try:
                                    args = json.loads(arguments) if isinstance(arguments, str) else arguments
                                except json.JSONDecodeError:
                                    args = {}
                            calls.append({
                                "name": name,
                                "args": args,
                                "id": call_id
                            })
            except Exception as e:
                logger.debug(f"Error extracting xAI tool calls: {e}")
        else:
            # OpenAI response handling
            try:
                # The OpenAIModelCall returns a Response object
                # We need to check output_items or similar
                # Based on model_call_openai.py, it returns a Response object from client.responses.create
                # We need to inspect it.
                # Assuming it has 'output' which is a list of items
                if hasattr(response, "output"):
                    for item in response.output:
                        if item.type == "function_call":
                            calls.append({
                                "name": item.name,
                                "args": json.loads(item.arguments),
                                "id": item.call_id
                            })
            except Exception:
                pass
        return calls

    async def execute_tool_calls(self, calls):
        results = []
        for call in calls:
            name = call["name"]
            args = call["args"]
            call_id = call.get("id")
            
            output = ""
            try:
                if name == "get_legal_moves":
                    output = str(get_legal_moves(args["fen"]))
                elif name == "evaluate_position":
                    output = str(evaluate_position(args["fen"], args.get("depth", 10)))
                elif name == "analyze_move":
                    output = str(analyze_move(args["fen"], args["move_san"]))
                elif name == "web_search":
                    output = await web_search_tool(args["query"])
                else:
                    output = f"Unknown tool: {name}"
            except Exception as e:
                output = f"Error executing tool {name}: {e}"
            
            results.append({
                "tool_call_id": call_id, # For OpenAI
                "output": output,
                "name": name # For Google
            })
        return results

    def extract_move_data(self, response, model_name):
        try:
            text = ""
            if "gemini" in model_name or "google" in model_name:
                if hasattr(response, "candidates") and response.candidates:
                    for part in response.candidates[0].content.parts:
                        # Check if this is a thought part (skip it)
                        is_thought = getattr(part, "thought", False)
                        if not is_thought:
                            # Double check dict representation if attribute missing
                            try:
                                if hasattr(part, "to_dict"):
                                    d = part.to_dict()
                                    if d.get("thought"):
                                        is_thought = True
                            except Exception:
                                pass
                        
                        if not is_thought and hasattr(part, "text") and part.text:
                            text += part.text
            elif "grok" in model_name.lower() or "xai" in model_name.lower():
                # xAI/Grok response handling - uses XAIResponse wrapper
                # XAIResponse has .text property that extracts content properly
                if hasattr(response, "text"):
                    text = response.text or ""
                elif hasattr(response, "content"):
                    # Fallback to content attribute
                    content = response.content
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        # Handle list of content parts
                        for part in content:
                            if isinstance(part, str):
                                text += part
                            elif isinstance(part, dict) and "text" in part:
                                text += part["text"]
            else:
                # OpenAI
                if hasattr(response, "output_text"):
                    text = response.output_text
                elif hasattr(response, "output"):
                    for item in response.output:
                        if item.type == "message" and item.role == "assistant":
                            for content in item.content:
                                if content.type == "text":
                                    text += content.text
            
            # Clean markdown json
            text = text.strip()
            
            # Early exit if no text (likely tool calls only)
            if not text:
                logger.warning("No text found in response, possibly tool calls only")
                logger.debug(f"Response object: {response}")
                return None
            
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            
            text = text.strip()
            
            logger.info(f"Extracted JSON text from {model_name}: {text[:100]}...")
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to extract JSON from response: {e}")
            logger.error(f"Text that failed to parse: '{text}'")
            return None
        except Exception as e:
            logger.error(f"Failed to extract move data from response: {e}")
            logger.debug(f"Response object: {response}")
            return None

    async def generate_commentary(self, player_name, move, reasoning):
        """
        Generates a hype commentary for the move.
        """
        try:
            # Build commentary history string (last 5 for context)
            history_str = ""
            if self.commentary_history:
                recent = self.commentary_history[-5:]
                history_lines = [f"  - {entry['player']} played {entry['move']}: \"{entry['commentary']}\"" for entry in recent]
                history_str = "\n".join(history_lines)
                logger.info(f"Commentary history has {len(self.commentary_history)} entries, using last {len(recent)}")
            else:
                logger.info("No commentary history yet (first move)")
            
            # Base prompt - just the job, no personality (user defines that)
            prompt = f"""You are the commentator for an AI chess match.

Match: {self.white_model_name} (White) vs {self.black_model_name} (Black)
Move {self.board.fullmove_number}: {player_name} just played {move}.
Their reasoning: "{reasoning}"
"""
            
            # Add commentary history if available
            if history_str:
                prompt += f"""
Your recent commentary (maintain narrative continuity):
{history_str}
"""
            
            prompt += "\nProvide your commentary for this move (1-2 sentences).\n"
            
            # Append user's personality/style instructions if provided
            custom_addition = self.game_config.get("commentary_prompt", "")
            if custom_addition:
                prompt += f"\nYour personality and style:\n{custom_addition}\n"
            
            logger.debug(f"Commentary prompt:\n{prompt}")
        
            model_name = self.commentary_model_name
            
            # Determine client based on model name
            if "grok" in model_name.lower() or model_name.lower().startswith("xai"):
                client = self.xai_client
            elif "gpt" in model_name.lower() or "o1" in model_name.lower():
                client = self.openai_client
            elif "gemini" in model_name.lower():
                client = self.gemini_client
            else:
                client = self.openai_client

            kwargs = {
                "prompt": prompt,
                "api_key": client._get_api_key(self.config),
                "model": model_name, 
                "stream": False
            }
            
            response = await client._make_api_call(**kwargs)
            
            commentary = ""
            if hasattr(response, "text") and isinstance(response.text, str) and response.text:
                # xAI/Grok responses use .text property
                commentary = response.text
            elif hasattr(response, "output_text"):
                # OpenAI responses - output_text could be a string or a config object
                output_text = response.output_text
                if isinstance(output_text, str):
                    commentary = output_text
            
            # Fallback: iterate through output items for OpenAI
            if not commentary and hasattr(response, "output"):
                for item in response.output:
                    if hasattr(item, "type") and item.type == "message" and hasattr(item, "role") and item.role == "assistant":
                        if hasattr(item, "content"):
                            for content in item.content:
                                if hasattr(content, "type") and content.type == "text" and hasattr(content, "text"):
                                    commentary += content.text
            
            # Fallback: dict response (e.g. from xAI or some OpenAI paths)
            if not commentary and isinstance(response, dict):
                if "choices" in response:
                    commentary = response["choices"][0]["message"]["content"]
                elif "output_text" in response:
                    commentary = response["output_text"]
                                    
            self.last_commentary = commentary.strip()
            
            # Save to commentary history for memory
            self.commentary_history.append({
                "player": player_name,
                "move": move,
                "commentary": self.last_commentary
            })
            logger.info(f"Saved commentary to history (now {len(self.commentary_history)} entries)")
            
            logger.info(f"Commentary: {self.last_commentary}")
            
        except Exception as e:
            logger.error(f"Failed to generate commentary: {e}")
            self.last_commentary = f"What a move by {player_name}!"

    def get_ui_state(self):
        """
        Returns the current state of the game for the UI.
        """
        # Calculate captured pieces
        # This is a bit manual with python-chess, we compare current board to starting board
        # Or just count material on board vs full set
        
        piece_values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
        white_material = 0
        black_material = 0
        
        # Simple material count
        for piece_type in piece_values:
            white_material += len(self.board.pieces(piece_type, chess.WHITE)) * piece_values[piece_type]
            black_material += len(self.board.pieces(piece_type, chess.BLACK)) * piece_values[piece_type]
        
        game_over = self.board.is_game_over() or self.forced_game_over
        result = self.board.result()
        if self.forced_game_over:
            result = "Stopped"
            
        # Get last move squares
        last_move_from = None
        last_move_to = None
        if self.board.move_stack:
            last_move = self.board.peek()
            last_move_from = chess.square_name(last_move.from_square)
            last_move_to = chess.square_name(last_move.to_square)
            
        return {
            "fen": self.board.fen(),
            "last_move": self.last_move_info.get("move"),
            "last_move_from": last_move_from,
            "last_move_to": last_move_to,
            "reasoning": self.last_move_info.get("reasoning"),
            "player": self.last_move_info.get("player"),
            "commentary": self.last_commentary,
            "turn": "White" if self.board.turn == chess.WHITE else "Black",
            "history": [m.uci() for m in self.board.move_stack],
            "game_over": game_over,
            "result": result,
            "material": {
                "white": white_material,
                "black": black_material
            },
            "move_number": self.board.fullmove_number,
            "eval": self.current_eval,
            "white_model": self.white_model_name,
            "black_model": self.black_model_name,
            "config": self.game_config,
            "game_log": self.game_log,  # Include move times
            "white_total_time": self.white_total_time,
            "black_total_time": self.black_total_time,
            "pgn_parse_error": self.pgn_parse_error
        }
    
    def get_pgn_string(self) -> str:
        """Generate PGN string with time annotations"""
        pgn_io = io.StringIO()
        exporter = chess.pgn.FileExporter(pgn_io)
        self.pgn_game.accept(exporter)
        return pgn_io.getvalue()

async def main():
    orchestrator = ChessOrchestrator()
    await orchestrator.run_game()

if __name__ == "__main__":
    asyncio.run(main())
