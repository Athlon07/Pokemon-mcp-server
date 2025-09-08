# server.py
from mcp.server.fastmcp import FastMCP
import requests, random, time
from typing import Dict, Optional

mcp = FastMCP(name="Pokémon MCP Server")

# -------------------------------
# Utilities / Caching
# -------------------------------
MOVE_CACHE: Dict[str, Dict] = {}
POKEMON_CACHE: Dict[str, Dict] = {}
ALL_POKEMON_CACHE: Optional[list] = None
POKEAPI_BASE = "https://pokeapi.co/api/v2"

def normalize_name(name: str) -> str:
    """Normalize names for PokeAPI (lowercase, spaces -> hyphens, strip punctuation)."""
    return name.strip().lower().replace(" ", "-").replace("'", "").replace(".", "").replace(":", "")

# -------------------------------
# Pokémon fetching with caching
# -------------------------------
def fetch_pokemon(name: str) -> Dict:
    """Fetch Pokémon data from PokéAPI with caching."""
    key = normalize_name(name)
    if key in POKEMON_CACHE:
        return POKEMON_CACHE[key]

    url = f"{POKEAPI_BASE}/pokemon/{key}"
    res = requests.get(url)
    if res.status_code != 200:
        raise ValueError(f"Pokémon '{name}' not found (status {res.status_code}).")
    data = res.json()
    stats = {s["stat"]["name"]: s["base_stat"] for s in data["stats"]}
    moves = [m["move"]["name"] for m in data["moves"]]  # full move list

    pokemon_obj = {
        "name": data["name"],
        "stats": stats,
        "max_hp": stats["hp"],
        "types": [t["type"]["name"] for t in data["types"]],
        "moves": moves,
        # status stored as dict {"name":..., "duration": int|None} or None
        "status": None,
        "status_counter": 0,
    }

    POKEMON_CACHE[key] = pokemon_obj
    return pokemon_obj

# -------------------------------
# Move fetching with caching (and caching failures)
# -------------------------------
def fetch_move(move_name: str) -> Dict:
    """Fetch move meta from PokeAPI with simple caching; returns normalized fields."""
    key = normalize_name(move_name)
    if key in MOVE_CACHE:
        # return {"key":"from cache"}
        return MOVE_CACHE[key]

    url = f"{POKEAPI_BASE}/move/{key}"
    res = requests.get(url)
    if res.status_code != 200:
        # Cache the failure result so we don't repeatedly hit the same bad move
        placeholder = {
            "name": move_name,
            "power": None,
            "accuracy": None,
            "type": None,
            "damage_class": "status",
            "ailment": None,
            "ailment_chance": 0
        }
        MOVE_CACHE[key] = placeholder
        return placeholder

    m = res.json()
    meta = m.get("meta", {}) or {}
    info = {
        "name": m["name"],
        "power": m["power"],  # None for many status moves
        "accuracy": m["accuracy"],  # percent or None
        "type": m["type"]["name"] if m.get("type") else None,
        "damage_class": m["damage_class"]["name"] if m.get("damage_class") else "status",
        "ailment": meta.get("ailment", {}).get("name", None),
        "ailment_chance": meta.get("ailment_chance", 0) or 0,
    }
    MOVE_CACHE[key] = info
    return info

# -------------------------------
# Type effectiveness (partial, add more if you want)
# -------------------------------
TYPE_EFFECTIVENESS = {
    "normal": {},
    "fire":     {"grass": 2.0, "water": 0.5, "fire": 0.5, "rock": 0.5, "ice": 2.0, "bug": 2.0, "steel": 2.0},
    "water":    {"fire": 2.0, "water": 0.5, "grass": 0.5, "ground": 2.0, "rock": 2.0},
    "grass":    {"water": 2.0, "fire": 0.5, "grass": 0.5, "ground": 2.0, "rock": 2.0, "flying": 0.5},
    "electric": {"water": 2.0, "electric": 0.5, "ground": 0.0, "flying": 2.0},
    "ground":   {"fire": 2.0, "electric": 2.0, "grass": 0.5, "flying": 0.0},
    "ice":      {"grass": 2.0, "ground": 2.0, "flying": 2.0, "dragon": 2.0, "fire": 0.5, "water": 0.5},
    # extend as required...
}

# -------------------------------
# Status effect helpers
# -------------------------------
def apply_status_end_of_turn(pokemon: Dict, current_hp: int) -> (int, list):
    """Apply per-turn passive damage for statuses and decrement durations."""
    logs = []
    status = pokemon.get("status")
    if not status:
        return current_hp, logs

    name = status["name"]
    duration = status.get("duration")  # None means persistent until cured
    max_hp = pokemon.get("max_hp", current_hp)

    # Passive damage per classic mechanics (burn: 1/16, poison: 1/8)
    if name == "burn":
        dmg = max(1, max_hp // 16)
        current_hp -= dmg
        logs.append(f"{pokemon['name']} is hurt by burn and loses {dmg} HP.")
    elif name == "poison":
        dmg = max(1, max_hp // 8)
        current_hp -= dmg
        logs.append(f"{pokemon['name']} is hurt by poison and loses {dmg} HP.")

    # decrement duration if present and positive
    if isinstance(duration, int):
        status["duration"] = duration - 1
        if status["duration"] <= 0:
            pokemon["status"] = None
            logs.append(f"{pokemon['name']} is no longer {name}.")

    return current_hp, logs

def can_act(pokemon: Dict) -> (bool, Optional[str]):
    """Return (can_act, message). Handles paralysis, sleep, freeze behavior."""
    status = pokemon.get("status")
    if not status:
        return True, None

    name = status["name"]
    duration = status.get("duration")

    if name == "paralysis":
        if random.random() < 0.25:
            return False, f"{pokemon['name']} is paralyzed! It can't move!"
        return True, None

    if name == "sleep":
        if isinstance(duration, int) and duration > 0:
            status["duration"] = duration - 1
            return False, f"{pokemon['name']} is fast asleep..."
        else:
            pokemon["status"] = None
            return True, None

    if name == "freeze":
        if random.random() < 0.2:
            pokemon["status"] = None
            return True, None
        else:
            return False, f"{pokemon['name']} is frozen solid!"

    return True, None

# -------------------------------
# Damage & status application
# -------------------------------
def apply_ailment_from_move(move_meta: Dict, target: Dict, by_status_move: bool):
    """
    Apply ailment to target based on move_meta.
    - If move is a status move (by_status_move==True), we assume deterministic application.
    - If it's a secondary effect from a damaging move, use ailment_chance.
    """
    ailment = move_meta.get("ailment")
    chance = move_meta.get("ailment_chance", 0) or 0

    if not ailment:
        return None

    # determine whether ailment happens
    happens = False
    if by_status_move:
        happens = True
    else:
        if chance and random.randint(1, 100) <= chance:
            happens = True

    if not happens:
        return None

    # classic rule: do not overwrite an existing major status
    if target.get("status"):
        return f"{target['name']} is already afflicted and the move had no effect."

    # Set durations by ailment type
    if ailment == "sleep":
        dur = random.randint(1, 3)
    elif ailment == "poison":
        dur = None  # now classic: persistent until cured
    elif ailment == "paralysis":
        dur = None
    elif ailment == "burn":
        dur = None
    elif ailment == "freeze":
        dur = None
    else:
        dur = None

    target["status"] = {"name": ailment, "duration": dur}
    if ailment == "sleep":
        target["status"]["duration"] = dur
        return f"{target['name']} fell asleep! ({dur} turn(s))"
    return f"{target['name']} is afflicted with {ailment}!"

def calculate_damage(att: Dict, defn: Dict, move_meta: Dict) -> (int, Optional[dict]):
    """
    Return (damage, meta) where meta contains details or is a string for 'miss' or status message.
    """
    if move_meta["damage_class"] == "status" or move_meta["power"] is None:
        msg = apply_ailment_from_move(move_meta, defn, by_status_move=True)
        if msg:
            return 0, msg
        return 0, "It failed."

    # Damaging move
    power = move_meta["power"] or 0
    damage_class = move_meta["damage_class"]
    atk_stat = "attack" if damage_class == "physical" else "special-attack"
    def_stat = "defense" if damage_class == "physical" else "special-defense"

    atk = att["stats"].get(atk_stat, att["stats"].get("attack", 1))
    defense = defn["stats"].get(def_stat, defn["stats"].get("defense", 1))

    # Burn penalty
    if att.get("status") and att["status"]["name"] == "burn" and damage_class == "physical":
        atk = max(1, atk // 2)

    # Accuracy
    accuracy = move_meta.get("accuracy")
    if accuracy is not None:
        if random.randint(1, 100) > int(accuracy):
            return 0, "miss"

    # Crit, rand
    crit = 2 if random.random() < 0.10 else 1
    rand = random.uniform(0.85, 1.0)

    # STAB
    stab = 1.5 if move_meta.get("type") in att.get("types", []) else 1.0

    # Type effectiveness using local TYPE_EFFECTIVENESS (fast, cached)
    t_mult = 1.0
    mtype = move_meta.get("type")
    if mtype:
        for d in defn.get("types", []):
            t_mult *= TYPE_EFFECTIVENESS.get(mtype, {}).get(d, 1.0)

    # Simplified damage formula
    base = (((2 * 50 / 5 + 2) * power * (atk / max(1, defense))) / 50) + 2
    dmg = int(base * stab * t_mult * crit * rand)
    meta = {"stab": stab, "type_mult": t_mult, "crit": crit}
    return max(0, dmg), meta

# -------------------------------
# MCP Tools (get_pokemon, get_move, start_battle, play_turn)
# -------------------------------
@mcp.tool()
def get_pokemon(name: str) -> Dict:
    """Fetch and return Pokémon data (names, stats, types, move list)."""
    return fetch_pokemon(name)

@mcp.tool()
def get_move(name: str) -> Dict:
    """Fetch and return move metadata (cached)."""
    return fetch_move(name)

@mcp.tool()
def start_battle(user_pokemon: str) -> Dict:
    """Initialize a 1v1 battle.
    User chooses their Pokémon; opponent chosen randomly from the entire Pokédex.
    """
    global ALL_POKEMON_CACHE
    p1 = fetch_pokemon(user_pokemon)

    # Fetch full Pokémon list once and cache it in memory
    if ALL_POKEMON_CACHE is None:
        url = f"{POKEAPI_BASE}/pokemon?limit=10000"
        res = requests.get(url)
        if res.status_code != 200:
            # fallback small pool if API fails
            all_pokemon = ["charmander", "squirtle", "bulbasaur", "pidgey", "geodude"]
        else:
            all_pokemon = [entry["name"] for entry in res.json().get("results", [])]
        ALL_POKEMON_CACHE = all_pokemon
    else:
        all_pokemon = ALL_POKEMON_CACHE

    # Pick random opponent from cached list; ensure it's not the same as user
    opponent_choice = random.choice(all_pokemon)
    # avoid exact same species as user's choice when possible
    if normalize_name(opponent_choice) == normalize_name(user_pokemon) and len(all_pokemon) > 1:
        # try a few times
        for _ in range(3):
            candidate = random.choice(all_pokemon)
            if normalize_name(candidate) != normalize_name(user_pokemon):
                opponent_choice = candidate
                break

    p2 = fetch_pokemon(opponent_choice)

    state = {
        "pokemon1": p1,
        "pokemon2": p2,
        "hp1": p1["max_hp"],
        "hp2": p2["max_hp"],
        "turn": 1,
        "log": [f"A wild {p2['name']} appeared! Battle starts!"],
    }
    return state

@mcp.tool()
def play_turn(state: Dict, move_user: str) -> Dict:
    """
    Play one turn with proper status/damage mechanics and cached lookups.
    """
    p1 = state["pokemon1"]
    p2 = state["pokemon2"]
    hp1 = state["hp1"]
    hp2 = state["hp2"]

    # Opponent (LLM) chooses a move: prefer damaging moves; fetch_move is cached
    opp_moves = p2["moves"][:]
    random.shuffle(opp_moves)
    move_opponent = None
    for m in opp_moves:
        mm = fetch_move(m)
        if mm["damage_class"] in ("physical", "special") and mm.get("power"):
            move_opponent = mm["name"]
            break
    if not move_opponent:
        move_opponent = opp_moves[0] if opp_moves else "tackle"

    # normalize move names
    move_user_norm = normalize_name(move_user)
    move_opponent_norm = normalize_name(move_opponent)

    # Determine speed with paralysis
    spd1 = p1["stats"]["speed"] // 2 if p1.get("status") and p1["status"]["name"] == "paralysis" else p1["stats"]["speed"]
    spd2 = p2["stats"]["speed"] // 2 if p2.get("status") and p2["status"]["name"] == "paralysis" else p2["stats"]["speed"]

    order = [(p1, move_user_norm, "hp2"), (p2, move_opponent_norm, "hp1")]
    if spd2 > spd1:
        order.reverse()

    for attacker, move_name_norm, target_hp in order:
        defender = p2 if target_hp == "hp2" else p1

        # check if attacker can act
        ok, msg = can_act(attacker)
        if not ok:
            state["log"].append(msg)
            continue

        # fetch move metadata (cached)
        move_meta = fetch_move(move_name_norm)
        dmg_or_msg, meta = calculate_damage(attacker, defender, move_meta)

        # meta as string -> status applied or special message
        if isinstance(meta, str):
            state["log"].append(f"{attacker['name']} used {move_meta['name']}! {meta}")
        else:
            if dmg_or_msg == 0:
                state["log"].append(f"{attacker['name']} used {move_meta['name']}, but it missed!")
            else:
                if target_hp == "hp2":
                    hp2 -= dmg_or_msg
                else:
                    hp1 -= dmg_or_msg

                eff = " Super effective!" if meta.get("type_mult", 1.0) > 1.0 else (" Not very effective..." if 0 < meta.get("type_mult", 1.0) < 1.0 else "")
                crit_txt = " Critical hit!" if meta.get("crit", 1) > 1 else ""
                state["log"].append(f"{attacker['name']} used {move_meta['name']}! {dmg_or_msg} damage.{eff}{crit_txt}")

        # apply secondary ailment if move_meta has an ailment chance (damaging moves)
        if isinstance(meta, dict) and move_meta.get("ailment"):
            apply_msg = apply_ailment_from_move(move_meta, defender, by_status_move=False)
            if apply_msg:
                state["log"].append(f"Secondary effect: {apply_msg}")

        # immediate faint check
        if hp1 <= 0 or hp2 <= 0:
            hp1 = max(0, hp1)
            hp2 = max(0, hp2)
            winner = p1["name"] if hp2 <= 0 else p2["name"]
            state.update({"hp1": hp1, "hp2": hp2})
            state["log"].append(f"{winner} wins the battle!")
            return state

    # End-of-turn passive damage and duration handling
    hp1, logs1 = apply_status_end_of_turn(p1, hp1)
    hp2, logs2 = apply_status_end_of_turn(p2, hp2)
    state["log"].extend(logs1 + logs2)

    if hp1 <= 0 or hp2 <= 0:
        hp1 = max(0, hp1)
        hp2 = max(0, hp2)
        winner = p1["name"] if hp2 <= 0 else p2["name"]
        state.update({"hp1": hp1, "hp2": hp2})
        state["log"].append(f"{winner} wins the battle!")
        return state

    # update and return
    state.update({"hp1": hp1, "hp2": hp2, "turn": state["turn"] + 1})
    return state

# Backwards-compatible alias
@mcp.tool()
def play_turn_chance(state: Dict, move_user: str) -> Dict:
    return play_turn(state, move_user)

# -------------------------------
# Entrypoint
# -------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
