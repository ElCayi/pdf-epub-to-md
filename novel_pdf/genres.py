from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .common import GENRE_ALIASES, ISBN_RE, normalize_text, slugify

def is_valid_isbn(isbn: str) -> bool:
    """Valida el dígito de control de un ISBN-10 o ISBN-13."""
    if len(isbn) == 10:
        values = [10 if char == "X" else int(char) for char in isbn]
        return sum((10 - index) * value for index, value in enumerate(values)) % 11 == 0
    if len(isbn) == 13 and isbn.isdigit():
        checksum = sum(
            int(char) * (1 if index % 2 == 0 else 3)
            for index, char in enumerate(isbn[:12])
        )
        return (10 - checksum % 10) % 10 == int(isbn[-1])
    return False

def extract_isbn(candidates: list[str]) -> str:
    """Busca el primer ISBN válido en los textos iniciales del libro."""
    for text in candidates:
        for match in ISBN_RE.finditer(text):
            isbn = re.sub(r"[^0-9X]", "", match.group(1).upper())
            if is_valid_isbn(isbn):
                return isbn
    return ""

def canonical_genres(categories: list[str]) -> list[str]:
    """Traduce categorías bibliográficas a un vocabulario estable."""
    genres: list[str] = []
    for category in categories:
        normalized = slugify(category).replace("-", " ")
        for alias, genre in GENRE_ALIASES.items():
            if alias.replace("-", " ") in normalized and genre not in genres:
                genres.append(genre)
    return genres

def normalize_manual_genres(genres: list[str]) -> list[str]:
    """Normaliza los géneros indicados expresamente por consola."""
    normalized: list[str] = []
    for genre in genres:
        slug = slugify(genre)
        canonical = GENRE_ALIASES.get(slug.replace("-", " "), slug)
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized

def fetch_json(url: str) -> dict[str, Any]:
    """Descarga un objeto JSON con un tiempo de espera corto."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "pdf-epub-to-md/1.0 (metadata lookup)"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.load(response)
    return data if isinstance(data, dict) else {}

def google_books_categories(isbn: str) -> list[str]:
    """Consulta las categorías de Google Books para un ISBN."""
    query = urllib.parse.urlencode({"q": f"isbn:{isbn}", "maxResults": 5})
    data = fetch_json(f"https://www.googleapis.com/books/v1/volumes?{query}")
    categories: list[str] = []
    for item in data.get("items", []):
        volume_info = item.get("volumeInfo", {})
        categories.extend(volume_info.get("categories", []))
    return [value for value in categories if isinstance(value, str)]

def open_library_categories(isbn: str) -> list[str]:
    """Consulta materias de Open Library para un ISBN."""
    data = fetch_json(f"https://openlibrary.org/isbn/{isbn}.json")
    categories = [
        value for value in data.get("subjects", []) if isinstance(value, str)
    ]
    for work in data.get("works", [])[:1]:
        key = work.get("key") if isinstance(work, dict) else None
        if key:
            work_data = fetch_json(f"https://openlibrary.org{key}.json")
            categories.extend(
                value
                for value in work_data.get("subjects", [])
                if isinstance(value, str)
            )
    return categories

def google_books_search_categories(title: str, author: str) -> list[str]:
    """Consulta Google Books por título y autor cuando no hay ISBN."""
    terms = [f'intitle:"{title}"']
    if author:
        terms.append(f'inauthor:"{author}"')
    query = urllib.parse.urlencode({"q": " ".join(terms), "maxResults": 5})
    data = fetch_json(f"https://www.googleapis.com/books/v1/volumes?{query}")
    categories: list[str] = []
    for item in data.get("items", []):
        volume_info = item.get("volumeInfo", {})
        categories.extend(volume_info.get("categories", []))
    return [value for value in categories if isinstance(value, str)]

def open_library_search_categories(title: str, author: str) -> list[str]:
    """Consulta Open Library por título y autor cuando no hay ISBN."""
    parameters = {"title": title, "fields": "subject", "limit": 5}
    if author:
        parameters["author"] = author
    query = urllib.parse.urlencode(parameters)
    data = fetch_json(f"https://openlibrary.org/search.json?{query}")
    categories: list[str] = []
    for document in data.get("docs", []):
        categories.extend(document.get("subject", []))
    return [value for value in categories if isinstance(value, str)]

def genre_cache_path() -> Path:
    """Devuelve la ruta de la caché bibliográfica del usuario."""
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "pdf-to-md-novelas" / "genres.json"

def read_genre_cache() -> dict[str, list[str]]:
    """Lee la caché de géneros, ignorando archivos ausentes o dañados."""
    try:
        data = json.loads(genre_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(isbn): [genre for genre in genres if isinstance(genre, str)]
        for isbn, genres in data.items()
        if isinstance(genres, list)
    }

def write_genre_cache(cache: dict[str, list[str]]) -> None:
    """Guarda la caché sin impedir la conversión si no se puede escribir."""
    try:
        path = genre_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass

def lookup_genres(
    isbn: str, title: str = "", author: str = "", original_title: str = ""
) -> list[str]:
    """Obtiene géneros por ISBN o, en su defecto, por título y autor."""
    lookup_title = original_title or title
    cache_key = isbn or f"book:{slugify(lookup_title)}|{slugify(author)}"
    if not isbn and not title:
        return []
    cache = read_genre_cache()
    if cache_key in cache:
        return cache[cache_key]

    categories: list[str] = []
    if isbn:
        primary_lookup = lambda: open_library_categories(isbn)
        fallback_lookup = lambda: google_books_categories(isbn)
    else:
        primary_lookup = lambda: open_library_search_categories(lookup_title, author)
        fallback_lookup = lambda: google_books_search_categories(lookup_title, author)
    try:
        categories.extend(primary_lookup())
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        pass
    if not categories:
        try:
            categories.extend(fallback_lookup())
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            pass

    genres = canonical_genres(categories)
    if genres:
        cache[cache_key] = genres
        write_genre_cache(cache)
    return genres
