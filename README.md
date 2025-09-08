Pokémon MCP Battle ServerThis project provides a Pokémon battle simulator powered by the PokéAPI and the Model Context Protocol (MCP). It exposes MCP tools so that an LLM (or MCP inspector) can fetch Pokémon, fetch moves, start battles, and play turn-based battles with proper damage mechanics, type effectiveness, and status effects.

✨ Features
-Fetch Pokémon: Fetches data from PokéAPI with caching to avoid repeated API calls.
-Fetch Moves: Retrieves move metadata like power, type, and status effects, also with caching.
-Turn-Based Battle System: Includes a comprehensive battle system with:Damage calculation (STAB, type effectiveness, critical hits).
-Status conditions (paralysis, sleep, burn, poison, freeze).End-of-turn effects (poison/burn damage, sleep duration).
-Random Opponent: The opponent is chosen randomly from the full Pokédex for varied gameplay.

# 📦 InstallationClone or download this repository:
```bash
git clone [https://github.com/Athlon07/Pokemon-mcp-server.git]
cd Pokemon-mcp-server
```
Make sure you have Python 3.10+ installed.Create a virtual environment and install the dependencies:

# Create and activate the virtual environment
```bash
python -m venv venv
venv\Scripts\activate
```
# Install required packages
```bash
pip install -r requirements.txt
The requirements.txt file contains:
requests
mcp[fastmcp]
mcp[cli]
```
🚀 Running the ServerStart the MCP server with the following command in your terminal:
```bash
python server.py
```
# Connecting to an MCP Client (e.g., Claude Desktop):
Edit your client's configuration file to point to your local server. You will need to provide the absolute path to the server.py file.

```claude_desktop_config.json:
{
  "mcpServers": {
    "pokemon-mcp": {
      "command": "python",
      "args": ["/path/to/your/server.py"],
      "cwd": "/path/to/your/project/directory"
    }
  }
}
```

# 🛠️ Available MCP Tools
-get_pokemon(name: str)Fetches Pokémon details, including stats, types, and available moves.
Example: "pikachu" → { "stats": {...}, "types": ["electric"], ... }
-get_move(name: str)Fetches move metadata, such as power, accuracy, type, and any ailment effects.
Example: "thunderbolt"
-start_battle(user_pokemon: str)
Starts a battle between your chosen Pokémon and a randomly selected opponent. Returns the complete initial battle state, including HP, turn number, and a battle log.
-play_turn(state: dict, move_user: str)
Plays one turn of the battle. You provide the current battle state and your chosen move. The opponent will select and execute its move automatically.
-play_turn_chance(state: dict, move_user: str)
This is an alias for play_turn for compatibility purposes. It functions identically.