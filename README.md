# Pipeline za ugovore / Contract Pipeline

Automatski sustav za ažuriranje cijena u ugovorima klijenata.
Automated system for updating prices in client contracts.

---

## Preduvjeti / Prerequisites

- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/)
  - macOS: sustav dolazi s Python 3.9, ali za GUI (tkinter) potreban je noviji Python s Tk 8.6+
  - macOS: the system Python 3.9 ships with broken Tk 8.5; install a newer Python (e.g. via python.org or Homebrew)
- **LibreOffice** *(opcionalno)* — potreban samo za .doc datoteke
  *(optional)* — only needed for .doc files

### Provjera Pythona / Check Python

```bash
python --version    # Windows
python3 --version   # macOS / Linux
```

---

## Postavljanje / First-Time Setup

Pokrenite jednom u Terminalu / Run once in Terminal:

```bash
python setup_env.py       # Windows
python3 setup_env.py      # macOS / Linux
```

Skripta automatski / The script automatically:
- Kreira virtualno okruženje (.venv) / Creates virtual environment
- Instalira sve ovisnosti / Installs all dependencies
- Provjerava sistemske zahtjeve / Checks system requirements
- Kreira pipeline.toml ako ne postoji / Creates pipeline.toml if missing

---

## Pokretanje / Running

**macOS:** Dvostruki klik na **`Launch Pipeline.command`** u Finderu.
Double-click **`Launch Pipeline.command`** in Finder.

**Windows:** Dvostruki klik na **`launch.py`**.
Double-click **`launch.py`**.

**Terminal (oba sustava / both):**
```bash
python launch.py       # Windows
python3 launch.py      # macOS / Linux
```

---

## Korištenje / Usage

GUI vas vodi kroz 5 koraka:

### 0. Postavke / Settings
- Postavite putanju do mape s ugovorima
- Provjerite podatke o tvrtki
- Unesite API ključ (ako nije već postavljen)

### 1. Priprema / Setup
- Kopira ugovore u radnu mapu
- Skenira i klasificira sve datoteke
- Prikazuje inventar klijenata

### 2. Ekstrakcija / Extraction
- Čita ugovore i ekstrahira cijene pomoću AI
- Generira kontrolnu tablicu (Excel)

### 3. Pregled / Review
- Otvorite kontrolnu tablicu u Excelu
- Na Sheet 1: označite klijente kao "Odobreno"
- Na Sheet 2: unesite nove cijene
- Spremite i zatvorite

### 4. Generiranje / Generation
- Pregledajte što će se generirati (Preview)
- Generirajte aneks dokumente

---

## Struktura datoteka / File Structure

```
contracts/          Izvorni ugovori (ne mijenja se / read-only)
data/               Radni podaci (kopije, ekstrakcije)
output/
  control_spreadsheet.xlsx    Kontrolna tablica za pregled
  annexes/                    Generirani aneksi
templates/          Predlošci za anekse
pipeline.toml       Konfiguracija
.env                API ključ
```

---

## CLI (napredno) / CLI (advanced)

Pipeline se može koristiti i putem naredbenog retka:

```bash
# Aktivirajte virtualno okruženje / Activate virtual environment
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\activate             # Windows

# Pokrenite naredbe / Run commands
pipeline setup --source ./contracts
pipeline extract
pipeline generate --start-number 30
pipeline status
pipeline inventory
```

---

## Česti problemi / Troubleshooting

**GUI se ruši odmah po pokretanju / GUI crashes immediately on launch**
macOS Python 3.9 ima staru verziju Tk (8.5) koja ne radi na novijim macOS verzijama.
Instalirajte Python 3.10+ s [python.org](https://www.python.org/downloads/) i ponovno pokrenite `setup_env.py`.
The system Python 3.9 on macOS ships with Tk 8.5 which crashes on modern macOS.
Install Python 3.10+ from python.org and re-run `setup_env.py`.

**"Virtual environment not found"**
Pokrenite `python3 setup_env.py` prije pokretanja GUI-a.
Run `python3 setup_env.py` before launching the GUI.

**"Konfiguracija se ne može učitati"**
Provjerite da `pipeline.toml` postoji i ima ispravnu sintaksu.
Check that `pipeline.toml` exists and has valid TOML syntax.

**"Inventory not found"**
Pokrenite korak Priprema (Setup) prije Ekstrakcije.
Run the Setup step before Extraction.

**Problemi s .doc datotekama / Problems with .doc files**
Instalirajte LibreOffice: [libreoffice.org](https://www.libreoffice.org/)

**API greške / API errors**
Provjerite API ključ u postavkama ili u `.env` datoteci.
Check the API key in Settings or in the `.env` file.
