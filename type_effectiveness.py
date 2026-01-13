from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TypeEffectivenessError(ValueError):
    """Raised when a type effectiveness lookup fails.

    This exception is used for invalid/unrecognized Pokémon type names or when
    a chart lookup cannot be completed.

    Attributes
    ----------
    message:
        Human-readable error message.
    """

    message: str

    def __str__(self) -> str:  # pragma: no cover
        """Return the error message."""

        return self.message


# Canonical Pokémon type names.
# NOTE: Keep these as Title Case strings matching Smogon/Pokémon standard naming.
TYPE_ORDER: tuple[str, ...] = (
    "Normal",
    "Fire",
    "Water",
    "Electric",
    "Grass",
    "Ice",
    "Fighting",
    "Poison",
    "Ground",
    "Flying",
    "Psychic",
    "Bug",
    "Rock",
    "Ghost",
    "Dragon",
    "Dark",
    "Steel",
    "Fairy",
)


# Optional abbreviations from the user's table.
# These are accepted as input and normalized to the canonical names.
_ABBREV_TO_CANONICAL: dict[str, str] = {
    "nor": "Normal",
    "fir": "Fire",
    "wat": "Water",
    "ele": "Electric",
    "gra": "Grass",
    "ice": "Ice",
    "fig": "Fighting",
    "poi": "Poison",
    "gro": "Ground",
    "fly": "Flying",
    "psy": "Psychic",
    "bug": "Bug",
    "roc": "Rock",
    "gho": "Ghost",
    "dra": "Dragon",
    "dar": "Dark",
    "ste": "Steel",
    "fai": "Fairy",
}


# Type effectiveness chart (attacking_type -> defending_type -> multiplier).
#
# Source: user-provided table.
#
# Important: The multiplier returned by `type_multiplier()` for dual-type
# defenders is the product of the multipliers against each defending type.
# For example, Fire against Grass/Ice is 2 * 2 = 4.
_TYPE_CHART: dict[str, dict[str, float]] = {
    "Normal": {
        "Normal": 1,
        "Fire": 1,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 0.5,
        "Ghost": 0,
        "Dragon": 1,
        "Dark": 1,
        "Steel": 0.5,
        "Fairy": 1,
    },
    "Fire": {
        "Normal": 1,
        "Fire": 0.5,
        "Water": 0.5,
        "Electric": 1,
        "Grass": 2,
        "Ice": 2,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 2,
        "Rock": 0.5,
        "Ghost": 1,
        "Dragon": 0.5,
        "Dark": 1,
        "Steel": 2,
        "Fairy": 1,
    },
    "Water": {
        "Normal": 1,
        "Fire": 2,
        "Water": 0.5,
        "Electric": 1,
        "Grass": 0.5,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 2,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 2,
        "Ghost": 1,
        "Dragon": 0.5,
        "Dark": 1,
        "Steel": 1,
        "Fairy": 1,
    },
    "Electric": {
        "Normal": 1,
        "Fire": 1,
        "Water": 2,
        "Electric": 0.5,
        "Grass": 0.5,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 0,
        "Flying": 2,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 1,
        "Dragon": 0.5,
        "Dark": 1,
        "Steel": 1,
        "Fairy": 1,
    },
    "Grass": {
        "Normal": 1,
        "Fire": 0.5,
        "Water": 2,
        "Electric": 1,
        "Grass": 0.5,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 0.5,
        "Ground": 2,
        "Flying": 0.5,
        "Psychic": 1,
        "Bug": 0.5,
        "Rock": 2,
        "Ghost": 1,
        "Dragon": 0.5,
        "Dark": 1,
        "Steel": 0.5,
        "Fairy": 1,
    },
    "Ice": {
        "Normal": 1,
        "Fire": 0.5,
        "Water": 0.5,
        "Electric": 1,
        "Grass": 2,
        "Ice": 0.5,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 2,
        "Flying": 2,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 1,
        "Dragon": 2,
        "Dark": 1,
        "Steel": 0.5,
        "Fairy": 1,
    },
    "Fighting": {
        "Normal": 2,
        "Fire": 1,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 2,
        "Fighting": 1,
        "Poison": 0.5,
        "Ground": 1,
        "Flying": 0.5,
        "Psychic": 0.5,
        "Bug": 0.5,
        "Rock": 2,
        "Ghost": 0,
        "Dragon": 1,
        "Dark": 2,
        "Steel": 2,
        "Fairy": 0.5,
    },
    "Poison": {
        "Normal": 1,
        "Fire": 1,
        "Water": 1,
        "Electric": 1,
        "Grass": 2,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 0.5,
        "Ground": 0.5,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 0.5,
        "Ghost": 0.5,
        "Dragon": 1,
        "Dark": 1,
        "Steel": 0,
        "Fairy": 2,
    },
    "Ground": {
        "Normal": 1,
        "Fire": 2,
        "Water": 1,
        "Electric": 2,
        "Grass": 0.5,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 2,
        "Ground": 1,
        "Flying": 0,
        "Psychic": 1,
        "Bug": 0.5,
        "Rock": 2,
        "Ghost": 1,
        "Dragon": 1,
        "Dark": 1,
        "Steel": 2,
        "Fairy": 1,
    },
    "Flying": {
        "Normal": 1,
        "Fire": 1,
        "Water": 1,
        "Electric": 0.5,
        "Grass": 2,
        "Ice": 1,
        "Fighting": 2,
        "Poison": 1,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 2,
        "Rock": 0.5,
        "Ghost": 1,
        "Dragon": 1,
        "Dark": 1,
        "Steel": 0.5,
        "Fairy": 1,
    },
    "Psychic": {
        "Normal": 1,
        "Fire": 1,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 1,
        "Fighting": 2,
        "Poison": 2,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 0.5,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 1,
        "Dragon": 1,
        "Dark": 0,
        "Steel": 0.5,
        "Fairy": 1,
    },
    "Bug": {
        "Normal": 1,
        "Fire": 0.5,
        "Water": 1,
        "Electric": 1,
        "Grass": 2,
        "Ice": 1,
        "Fighting": 0.5,
        "Poison": 0.5,
        "Ground": 1,
        "Flying": 0.5,
        "Psychic": 2,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 0.5,
        "Dragon": 1,
        "Dark": 2,
        "Steel": 0.5,
        "Fairy": 0.5,
    },
    "Rock": {
        "Normal": 1,
        "Fire": 2,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 2,
        "Fighting": 0.5,
        "Poison": 1,
        "Ground": 0.5,
        "Flying": 2,
        "Psychic": 1,
        "Bug": 2,
        "Rock": 1,
        "Ghost": 1,
        "Dragon": 1,
        "Dark": 1,
        "Steel": 0.5,
        "Fairy": 1,
    },
    "Ghost": {
        "Normal": 0,
        "Fire": 1,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 2,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 2,
        "Dragon": 1,
        "Dark": 0.5,
        "Steel": 1,
        "Fairy": 1,
    },
    "Dragon": {
        "Normal": 1,
        "Fire": 1,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 1,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 1,
        "Dragon": 2,
        "Dark": 1,
        "Steel": 0.5,
        "Fairy": 0,
    },
    "Dark": {
        "Normal": 1,
        "Fire": 1,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 1,
        "Fighting": 0.5,
        "Poison": 1,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 2,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 2,
        "Dragon": 1,
        "Dark": 0.5,
        "Steel": 1,
        "Fairy": 0.5,
    },
    "Steel": {
        "Normal": 1,
        "Fire": 0.5,
        "Water": 0.5,
        "Electric": 0.5,
        "Grass": 1,
        "Ice": 2,
        "Fighting": 1,
        "Poison": 1,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 2,
        "Ghost": 1,
        "Dragon": 1,
        "Dark": 1,
        "Steel": 0.5,
        "Fairy": 2,
    },
    "Fairy": {
        "Normal": 1,
        "Fire": 0.5,
        "Water": 1,
        "Electric": 1,
        "Grass": 1,
        "Ice": 1,
        "Fighting": 2,
        "Poison": 0.5,
        "Ground": 1,
        "Flying": 1,
        "Psychic": 1,
        "Bug": 1,
        "Rock": 1,
        "Ghost": 1,
        "Dragon": 2,
        "Dark": 2,
        "Steel": 0.5,
        "Fairy": 1,
    },
}


def normalize_type_name(type_name: str) -> str:
    """Normalize a Pokémon type name to its canonical Title Case form.

    This function accepts either full type names (e.g. "Fire") or the
    abbreviations from the user-provided chart (e.g. "Fir"). It also tolerates
    different casing (e.g. "fire", "FIRE").

    Parameters
    ----------
    type_name:
        A Pokémon type string, such as "Fire" or "Fir".

    Returns
    -------
    str
        Canonical type name matching the keys of `TYPE_ORDER`.

    Raises
    ------
    TypeEffectivenessError
        If the type name is not recognized.
    """

    raw = (type_name or "").strip()
    if not raw:
        raise TypeEffectivenessError("Empty type name")

    key = raw.lower()

    # If it's an abbreviation, map it.
    if key in _ABBREV_TO_CANONICAL:
        return _ABBREV_TO_CANONICAL[key]

    # Otherwise normalize capitalization.
    candidate = raw[:1].upper() + raw[1:].lower()
    if candidate in TYPE_ORDER:
        return candidate

    raise TypeEffectivenessError(f"Unknown Pokémon type: {type_name!r}")


def single_type_multiplier(attacking_type: str, defending_type: str) -> float:
    """Compute the multiplier of an attack against a single defending type.

    Parameters
    ----------
    attacking_type:
        Type of the move/attack (e.g. "Fire" or "Fir").
    defending_type:
        Type of the defender (e.g. "Grass" or "Gra").

    Returns
    -------
    float
        Type effectiveness multiplier: 0, 0.5, 1, or 2.

    Raises
    ------
    TypeEffectivenessError
        If either type is not recognized or the chart entry is missing.
    """

    atk = normalize_type_name(attacking_type)
    dfn = normalize_type_name(defending_type)

    # Defensive programming: the chart is expected to be complete.
    # If something is missing, we raise a helpful error rather than returning
    # a wrong default.
    try:
        return float(_TYPE_CHART[atk][dfn])
    except KeyError as exc:
        raise TypeEffectivenessError(f"Missing chart entry for attack={atk!r} vs defense={dfn!r}") from exc


def type_multiplier(attacking_type: str, defender_types: Iterable[str]) -> float:
    """Compute the overall multiplier against a Pokémon with 1-2 types.

    This function implements the standard Pokémon type effectiveness rule:

    - Single-type defender: multiplier = chart[atk][def]
    - Dual-type defender: multiplier = chart[atk][def1] * chart[atk][def2]

    Parameters
    ----------
    attacking_type:
        Type of the move/attack.
    defender_types:
        An iterable of defending types. Typically one or two values.

    Returns
    -------
    float
        The final effectiveness multiplier.

    Raises
    ------
    TypeEffectivenessError
        If `defender_types` is empty, contains more than 2 types, or contains an
        invalid type.
    """

    types = [t for t in defender_types if t is not None]

    if not types:
        raise TypeEffectivenessError("Defender types must contain at least one type")

    if len(types) > 2:
        raise TypeEffectivenessError(f"Defender types must contain at most 2 types (got {len(types)})")

    # Multiply the per-type effectiveness values.
    multiplier = 1.0
    for t in types:
        multiplier *= single_type_multiplier(attacking_type, t)

    return float(multiplier)


def _self_check() -> None:
    """Run a few sanity checks on the chart.

    This is not a full test suite, but it protects against accidental edits to
    `_TYPE_CHART`.
    """

    # Immunities
    assert single_type_multiplier("Normal", "Ghost") == 0
    assert single_type_multiplier("Electric", "Ground") == 0
    assert single_type_multiplier("Dragon", "Fairy") == 0

    # Common super-effective matchups
    assert single_type_multiplier("Fire", "Grass") == 2
    assert single_type_multiplier("Water", "Fire") == 2

    # Dual-type multiplication: Fire vs Grass/Ice -> 4x
    assert type_multiplier("Fire", ["Grass", "Ice"]) == 4


_self_check()
