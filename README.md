# Pokémon Team Builder MVP

A Streamlit-based application that generates random competitive Pokémon teams using Smogon strategies and uploads them to PokePaste.

[**Try out the Live Demo!**](https://pokemonteamgenerator.streamlit.app/)

## Features

- **Random Team Generation**: Generates valid competitive teams for various tiers (e.g., OU) based on usage statistics and strategies.
- **PokePaste Integration**: Automatically uploads generated teams to [PokePaste](https://pokepast.es/) and provides a shareable link.
- **Visual Team Display**: Displays the generated team in a clean, visual grid with sprites, moves, items, abilities, and EV spreads.
- **Showdown Export**: Provides the raw Showdown-formatted text for easy import into Pokémon Showdown.

## Installation

1.  **Clone the repository:**

    ```bash
    git clone <repository-url>
    cd PokemonTeamBuilder
    ```

2.  **Install dependencies:**

    It is recommended to use a virtual environment.

    ```bash
    pip install -r requirements.txt
    ```

## Usage

1.  **Ensure the database is ready:**
    The application relies on `pokemon_strategies.db`. If this file is missing, you may need to run the database creation script (e.g., `create_db.py` or `main.py` depending on your specific setup, or ensure the pre-built database is present).

2.  **Run the Streamlit application:**

    ```bash
    streamlit run ui_app.py
    ```

3.  **Generate a Team:**
    - Open the URL provided by Streamlit (usually `http://localhost:8501`).
    - Select a tier from the dropdown.
    - Click "Generate random team".
    - View the generated team visually or click the PokePaste link.

## Project Structure

- `ui_app.py`: The main Streamlit application file.
- `pokemon_strategies.db`: SQLite database containing Pokémon builds and tier information.
- `pokepaste_uploader.py`: Helper script for uploading teams to PokePaste.
- `requirements.txt`: List of Python dependencies.
- `streamlit_styles.css`: Custom CSS for the Streamlit UI.
