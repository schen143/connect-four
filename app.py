import logging
import os
import threading
import uuid

from flask import Flask, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

ROWS = 6
COLS = 7
PLAYERS = ("red", "yellow")

lock = threading.Lock()


def new_game(seats=None):
    return {
        "board": [[None] * COLS for _ in range(ROWS)],
        "turn": "red",
        "winner": None,  # "red", "yellow", "draw", or None
        "winning_cells": [],
        "moves": [],  # every turn, in order: {"player", "row", "col"}
        "seats": seats or {"red": None, "yellow": None},  # player -> browser id
        "version": 0,
    }


game = new_game()


def browser_id():
    if "id" not in session:
        session["id"] = uuid.uuid4().hex
    return session["id"]


def player_for(bid):
    for color, seat in game["seats"].items():
        if seat == bid:
            return color
    return None


def find_win(board, row, col):
    color = board[row][col]
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        cells = [(row, col)]
        for sign in (1, -1):
            r, c = row + dr * sign, col + dc * sign
            while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == color:
                cells.append((r, c))
                r, c = r + dr * sign, c + dc * sign
        if len(cells) >= 4:
            return cells
    return None


def state_for(bid):
    return {
        "board": game["board"],
        "turn": game["turn"],
        "winner": game["winner"],
        "winning_cells": game["winning_cells"],
        "moves": game["moves"],
        "seats": {color: seat is not None for color, seat in game["seats"].items()},
        "you": player_for(bid),
        "version": game["version"],
    }


@app.route("/")
def home():
    browser_id()
    return render_template("index.html", rows=ROWS, cols=COLS)


@app.route("/instructions")
def instructions():
    return render_template("instructions.html")


@app.route("/api/state")
def get_state():
    bid = browser_id()
    with lock:
        return jsonify(state_for(bid))


@app.route("/api/join", methods=["POST"])
def join():
    bid = browser_id()
    with lock:
        if player_for(bid) is None:
            for color in PLAYERS:
                if game["seats"][color] is None:
                    game["seats"][color] = bid
                    game["version"] += 1
                    break
            else:
                return jsonify(error="Both seats are taken. You can still watch the game."), 409
        return jsonify(state_for(bid))


@app.route("/api/move", methods=["POST"])
def move():
    bid = browser_id()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "col" not in data:
        return jsonify(error='Send your move as JSON, like {"col": 3}.'), 400
    col = data["col"]
    # bool is a subclass of int, so rule it out explicitly.
    if type(col) is not int or not 0 <= col < COLS:
        return jsonify(error=f"Column must be a whole number from 0 to {COLS - 1}."), 400
    with lock:
        player = player_for(bid)
        if player is None:
            return jsonify(error="You haven't joined the game."), 403
        if game["winner"]:
            return jsonify(error="The game is over."), 400
        if None in game["seats"].values():
            return jsonify(error="Waiting for a second player."), 400
        if game["turn"] != player:
            return jsonify(error="It's not your turn."), 400

        board = game["board"]
        row = next((r for r in range(ROWS - 1, -1, -1) if board[r][col] is None), None)
        if row is None:
            return jsonify(error="That column is full."), 400

        board[row][col] = player
        game["moves"].append({"player": player, "row": row, "col": col})

        win = find_win(board, row, col)
        if win:
            game["winner"] = player
            game["winning_cells"] = win
        elif len(game["moves"]) == ROWS * COLS:
            game["winner"] = "draw"
        else:
            game["turn"] = "yellow" if player == "red" else "red"

        game["version"] += 1
        return jsonify(state_for(bid))


@app.route("/api/reset", methods=["POST"])
def reset():
    """Start a new game. Seated players keep their colors."""
    global game
    bid = browser_id()
    with lock:
        if player_for(bid) is None:
            return jsonify(error="Only players can reset the game."), 403
        version = game["version"]
        game = new_game(seats=game["seats"])
        game["version"] = version + 1
        return jsonify(state_for(bid))


@app.route("/api/leave", methods=["POST"])
def leave():
    """Give up your seat so someone else can join."""
    bid = browser_id()
    with lock:
        player = player_for(bid)
        if player:
            game["seats"][player] = None
            game["version"] += 1
        return jsonify(state_for(bid))


def wants_json():
    return request.path.startswith("/api/")


@app.errorhandler(HTTPException)
def handle_http_error(e):
    """404s, wrong methods, bad requests: a friendly message, never a traceback."""
    messages = {
        400: "That request didn't make sense to the server.",
        404: "That page doesn't exist.",
        405: f"{request.method} isn't allowed here.",
    }
    message = messages.get(e.code, e.description)
    if wants_json():
        return jsonify(error=message), e.code
    return render_template("error.html", code=e.code, title=e.name, message=message), e.code


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Anything we didn't plan for: log the details, show the player a plain message."""
    app.logger.exception("Unhandled error on %s %s", request.method, request.path)
    message = "Something went wrong on our end. Please try again."
    if wants_json():
        return jsonify(error=message), 500
    return render_template("error.html", code=500, title="Server error", message=message), 500


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Debug mode shows tracebacks in the browser, so it's off unless FLASK_DEBUG=1.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5001)
