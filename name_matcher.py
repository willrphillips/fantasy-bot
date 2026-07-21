"""
Fuzzy name matcher for cross-source player name resolution.
ESPN, Razzball, MLB Stats API all format names slightly differently.
This normalizes + fuzzy-matches them.
"""
from __future__ import annotations
import re
import unicodedata
from rapidfuzz import fuzz, process


def normalize(name: str) -> str:
    """Lowercase, strip accents, remove punctuation/suffixes."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"\s+(jr|sr|ii|iii|iv)\.?$", "", name)
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def best_match(query, candidates, threshold=85):
    """Find best fuzzy match for query in candidates list.
    Returns (matched_name, score) or None if below threshold."""
    if not query or not candidates:
        return None
    norm_query = normalize(query)
    norm_map = {normalize(c): c for c in candidates}
    norm_keys = list(norm_map.keys())

    result = process.extractOne(norm_query, norm_keys, scorer=fuzz.WRatio)
    if not result:
        return None
    matched_norm, score, _ = result
    if score < threshold:
        return None
    return (norm_map[matched_norm], int(score))


def build_lookup(candidates):
    """Build a normalized-name -> original-name lookup dict."""
    return {normalize(c): c for c in candidates}


if __name__ == "__main__":
    test_cases = [
        ("Ronald Acuna Jr.", ["Ronald Acuna Jr.", "Luis Garcia", "Juan Soto"]),
        ("J. Soto", ["Ronald Acuna Jr.", "Luis Garcia", "Juan Soto"]),
        ("Shohei Ohtani", ["S. Ohtani", "Mike Trout"]),
        ("Random Player", ["Ronald Acuna Jr.", "Juan Soto"]),
    ]
    print("=== name_matcher self-test ===")
    for query, pool in test_cases:
        result = best_match(query, pool)
        if result:
            print(f"  '{query}' -> '{result[0]}' (score: {result[1]})")
        else:
            print(f"  '{query}' -> NO MATCH")
