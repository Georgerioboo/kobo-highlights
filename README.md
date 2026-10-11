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
- **Notion sync**: sends the highlights of the books you choose to their page in your Notion reading database. Only missing highlights are added, and existing content is never modified or deleted.
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

A menu lists the books with highlights. Type a number (`3`), several (`1,4`), a range (`2-5`), `a` for all, `h` for help or `q` to quit.

> The interface is in Spanish.

### Options

| Option | Short | Description |
|---|---|---|
| `--all` | `-a` | Export every book without showing the menu |
| `--format {md,txt}` | `-f` | Markdown for Notion (default) or plain text |
| `--no-paragraphs` | `-p` | Put each highlight in a single paragraph |
| `--copy` | `-c` | Copy the result to the clipboard |
| `--output FOLDER` | `-o` | Output folder (default: `~/Documents/kobo_highlights`) |
| `--db PATH` | `-d` | Path to `KoboReader.sqlite` if the Kobo is not detected |
| `--dry-run` | | Show what would be sent to Notion without changing anything |
| `--no-notion` | | Don't ask about Notion even if it is configured |
| `--help` | `-h` | Show the help with examples |
| `--version` | `-v` | Show the version |

### Examples

```bash
# Choose books and copy them to the clipboard, ready to paste into Notion
python kobo_highlights.py --copy

# Export the whole library as plain text
python kobo_highlights.py -a -f txt

# Use a specific database path
python kobo_highlights.py --db "F:\.kobo\KoboReader.sqlite"
```

## Notion sync

After exporting, the script asks whether to send the selected books to Notion. For each book it looks for its page in your reading database by title (ignoring accents, case and punctuation):

- **No page yet**: it asks before creating one, with the title and author.
- **Page exists**: it reads the page and appends only the highlights that are not there yet, under a dated *Resaltes de Kobo* heading. Highlights you pasted by hand earlier are recognized even if they were badly formatted.
- **All books selected**: it asks for a second confirmation before touching Notion.

### Setup

1. Go to [notion.so/profile/integrations](https://www.notion.so/profile/integrations), create an **internal integration** and copy its secret (it starts with `ntn_`).
2. Open your books database in Notion, click **···** → **Connections** and add the integration.
3. Copy `.env.example` to `.env` and fill in the token and the URL of the database (or of the page that contains it: the script finds the database inside).

```bash
cp .env.example .env
python kobo_highlights.py --dry-run   # check what would happen, without writing
```

The `.env` file is in `.gitignore`, so your token never reaches GitHub. The script uses the Notion API version `2025-09-03` and only the Python standard library.

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
5. Optionally syncs the selected books to Notion (`notion_sync.py`).

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests use a small made-up database with the same structure as the Kobo one, and an in-memory fake of the Notion API, so they never touch a real account. They also run automatically on GitHub Actions on every push.

## Privacy

Your Kobo database contains your reading history and fragments of copyrighted books. The `.gitignore` in this repository prevents committing the database or your exported highlights.

## Roadmap

- [x] Sync highlights directly to a Notion database
- [ ] Personal highlights library with full-text search

## License

[MIT](LICENSE)
