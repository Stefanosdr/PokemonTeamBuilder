import requests


def showdown_to_pokepaste(
    team_text: str,
    title: str = "My Showdown Team",
    author: str = "",
    notes: str = "",
    public: bool = True,
) -> str:
    """Upload a Pokémon Showdown team text to Pokepaste and return the paste URL.

    Parameters
    ----------
    team_text: str
        The raw Pokémon Showdown team export text.
    title: str
        Title that will be shown on Pokepaste.
    author: str
        Optional author name.
    notes: str
        Optional notes / description.
    public: bool
        If True, paste is public; if False, it is unlisted.
    """
    url = "https://pokepast.es/create"

    # Normalise line endings to CRLF with a trailing newline to better
    # match browser form submissions, which Pokepaste's parser expects.
    normalized_team = team_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_team = normalized_team.replace("\n", "\r\n")
    if not normalized_team.endswith("\r\n"):
        normalized_team += "\r\n"

    data = {
        "paste": normalized_team,
        "title": title,
        "author": author,
        "notes": notes,
        "visibility": "public" if public else "unlisted",
    }

    # Pokepaste returns a redirect (303 See Other in practice) to the new paste;
    # we do not follow redirects so we can grab the Location header directly.
    response = requests.post(url, data=data, allow_redirects=False, timeout=30)

    if response.status_code in (301, 302, 303) and "Location" in response.headers:
        paste_url = response.headers["Location"]
        if paste_url.startswith("/"):
            paste_url = "https://pokepast.es" + paste_url
        return paste_url

    raise RuntimeError(
        f"Failed to create pokepaste: status {response.status_code}, "
        f"response: {response.text[:500]}"
    )

