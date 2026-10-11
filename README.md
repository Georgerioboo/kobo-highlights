# Kobo Highlights

![tests](https://github.com/Georgerioboo/kobo-highlights/actions/workflows/tests.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Export the highlights and notes from a **Kobo eReader**, in reading order and with clean text, ready to paste into Notion, Obsidian or any notes app.

No dependencies: just Python and the standard library.

## The problem

Kobo stores highlights exactly as they appear in the book's layout. When you export them, they come out broken: line breaks in the middle of sentences, words split by hyphens, double spaces and invisible characters. They are also sorted by the date you made them, not by their position in the book.

**Before** (raw text from the Kobo database):

```
La tormenta llegó de madrugada y el pue-
blo entero se quedó a oscuras , sin
   saber qué hacer.
```

**After** (`kobo_highlights.py`):

```
La tormenta llegó de madrugada y el pueblo entero se quedó a oscuras, sin saber qué hacer.
```

## Features

- **Clean text**: joins lines broken by the layout, rebuilds hyphenated words, removes soft hyphens and non-breaking spaces, and fixes spacing around punctuation. Real paragraph breaks and dialogue lines are kept.
- **Reading order**: highlights are sorted by chapter and position in the book, not by date.
- **Notes included**: the notes you write on the Kobo are exported next to their highlight.
- **Notion-ready Markdown**: titles, chapters and quotes keep their formatting when pasted into Notion. Plain text is also available.
- **Safe**: works on a temporary copy of the Kobo database, so the device is never modified.
- **Automatic detection** of the connected Kobo on Windows, macOS and Linux.
- **Clipboard copy**, batch export of the whole library, and selection of several books at once.

## Requirements

- Python 3.10 or newer
- A Kobo eReader connected by USB

## Installation

```bash
git clone https://github.com/Georgerioboo/kobo-highlights.git
cd kobo-highlights
```

Or download the ZIP from the green **Code** button on GitHub.

## Usage

Connect the Kobo and run:

```bash
python kobo_highlights.py
```

A menu lists the books with highlights. Type a number (`3`), several (`1,4`), a range (`2-5`), `t` for all, `h` for help or `q` to quit.

> The interface is in Spanish.

### Options

| Option | Short | Description |
|---|---|---|
| `--todos` | `-t` | Export every book without showing the menu |
| `--formato {md,txt}` | `-f` | Markdown for Notion (default) or plain text |
| `--sin-parrafos` | `-p` | Put each highlight in a single paragraph |
| `--copiar` | `-c` | Copy the result to the clipboard |
| `--salida CARPETA` | `-s` | Output folder (default: `~/Documents/kobo_highlights`) |
| `--db RUTA` | `-d` | Path to `KoboReader.sqlite` if the Kobo is not detected |
| `--help` | `-h` | Show the help with examples |
| `--version` | `-v` | Show the version |

### Examples

```bash
# Choose books and copy them to the clipboard, ready to paste into Notion
python kobo_highlights.py --copiar

# Export the whole library as plain text
python kobo_highlights.py -t -f txt

# Use a specific database path
python kobo_highlights.py --db "F:\.kobo\KoboReader.sqlite"
```

## Output example

```markdown
# Libro de prueba — Autora Inventada

3 resaltes · exportado el 11/10/2026

## Capítulo uno

> Primera del capítulo uno.

> Segunda del capítulo uno.

## Capítulo dos

> Frase del capítulo dos.

📝 Mi nota
```

## How it works

The Kobo keeps its library in an SQLite database at `.kobo/KoboReader.sqlite`. The script:

1. Copies the database to a temporary folder and opens the copy.
2. Reads the highlights from the `Bookmark` table and joins them with the `content` table to get the book title, author and chapter.
3. Sorts them by chapter (`VolumeIndex`) and position inside the chapter (`ChapterProgress`, `StartContainerPath`).
4. Normalizes the text and writes one file per book.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests use a small made-up database with the same structure as the Kobo one. They also run automatically on GitHub Actions on every push.

## Privacy

Your Kobo database contains your reading history and fragments of copyrighted books. The `.gitignore` in this repository prevents committing the database or your exported highlights.

## Roadmap

- [ ] Sync highlights directly to a Notion database
- [ ] Personal highlights library with full-text search

## License

[MIT](LICENSE)
