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

GUI vas vodi kroz 5 koraka / The GUI guides you through 5 steps:

### 0. Postavke / Settings
- Postavite putanju do mape s ugovorima / Set the path to the contracts folder
- Provjerite podatke o tvrtki / Verify company details
- Unesite API ključ (ako nije već postavljen) / Enter API key (if not already set)
- Testirajte API vezu gumbom "Test API" / Test the API connection with the "Test API" button

### 1. Priprema / Setup
- Kopira ugovore u radnu mapu / Copies contracts to the working directory
- Skenira i klasificira sve datoteke / Scans and classifies all files
- Prikazuje inventar klijenata / Displays client inventory

### 2. Ekstrakcija / Extraction
- Čita ugovore i ekstrahira cijene pomoću AI / Reads contracts and extracts prices via AI
- Generira kontrolnu tablicu (Excel) / Generates the control spreadsheet (Excel)
- Filtrirajte klijente po imenu / Filter clients by name

### 3. Pregled / Review
- Otvorite kontrolnu tablicu u Excelu / Open the control spreadsheet in Excel
- Na Sheet 1: označite klijente kao "Odobreno" / On Sheet 1: mark clients as "Odobreno"
- Na Sheet 2: unesite nove cijene na jedan od dva načina:
  On Sheet 2: enter new prices using one of two methods:
  - **Stupac G "Nova cijena EUR"** — unesite izravnu novu cijenu u EUR
    Column G "Nova cijena EUR" — enter a direct new price in EUR
  - **Stupac H "% povećanja"** — unesite postotak promjene (npr. `5` za +5%, `-3` za -3%)
    Column H "% povećanja" — enter a percentage change (e.g. `5` for +5%, `-3` for -3%)
  - Možete koristiti bilo koji način po retku. Ako su oba popunjena, postotak ima prednost.
    You can use either method per row. If both are filled, percentage takes precedence.
- Spremite i zatvorite / Save and close
- Pregledajte ekstrakcije po klijentu u GUI-u / Preview per-client extractions in the GUI

### 4. Generiranje / Generation
- Pregledajte što će se generirati (Preview) / Preview what will be generated
- Generirajte aneks dokumente / Generate annex documents

### Tipkovni prečaci / Keyboard Shortcuts

| Prečac / Shortcut | Akcija / Action |
|---|---|
| Cmd/Ctrl + 1-4 | Navigacija na korak / Navigate to step |
| Enter | Pokreni trenutni korak / Run current step |
| Esc | Otkaži operaciju / Cancel operation |
| Cmd/Ctrl + F | Pretraži log / Search log |

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
The pipeline can also be used via the command line:

```bash
# Aktivirajte virtualno okruženje / Activate virtual environment
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\activate             # Windows

# Verzija / Version
pipeline --version

# Pokrenite naredbe / Run commands
pipeline setup --source ./contracts
pipeline extract [--force] [--verbose]
pipeline generate --start-number 30 [--verbose]
pipeline status
pipeline inventory
pipeline validate-template

# Resetiranje faza / Reset phases
pipeline reset setup               # Resetira jednu fazu / Reset a single phase
pipeline reset --all                # Resetira sve faze / Reset all phases
```

Zastavice / Flags:
- `--version` / `-V` — Prikaži verziju / Show version
- `--verbose` — Puni ispis grešaka / Full error tracebacks
- `--force` — Ponovi ekstrakciju već obrađenih / Re-extract already processed docs

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

**Korak nije dostupan / Step is not available**
Koraci se moraju izvršiti redom: Priprema → Ekstrakcija → Pregled → Generiranje.
Nedostupni koraci su označeni sivom bojom u GUI-u.
Steps must be run in order: Setup → Extraction → Review → Generation.
Unavailable steps are greyed out in the GUI.

**Pipeline se zaglavio u "running" stanju / Pipeline stuck in "running" state**
Ako je pipeline prekinut tijekom izvršavanja, koristite `pipeline reset [faza]` za resetiranje.
U GUI-u će se prikazati upozorenje o zastoju.
If the pipeline was interrupted, use `pipeline reset [phase]` to reset the state.
The GUI will show a warning about stale running phases.

**Problemi s .doc datotekama / Problems with .doc files**
Instalirajte LibreOffice: [libreoffice.org](https://www.libreoffice.org/)

**API greške / API errors**
Provjerite API ključ u postavkama ili u `.env` datoteci.
Koristite gumb "Test API" u postavkama za provjeru veze.
Check the API key in Settings or in the `.env` file.
Use the "Test API" button in Settings to verify the connection.
API pozivi automatski se ponavljaju do 3 puta kod privremenih grešaka.
API calls automatically retry up to 3 times on transient errors.

**Greška u kontrolnoj tablici / Spreadsheet errors**
Ako ste promijenili strukturu tablice (zaglavlja, redoslijed stupaca), pipeline neće moći pročitati podatke.
Generirajte novu tablicu ponovnim pokretanjem ekstrakcije.
If you changed the spreadsheet structure (headers, column order), the pipeline cannot read the data.
Generate a new spreadsheet by re-running extraction.
