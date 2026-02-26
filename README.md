# Pipeline za ažuriranje cijena u ugovorima

Automatizirani sustav za ekstrakciju cijena iz ugovora klijenata, pregled u kontrolnoj tablici (Excel) i generiranje novih aneks dokumenata (.docx) s ažuriranim cijenama.

---

## Brzi početak

```
1. Instalirajte Python 3.10+
2. Pokrenite:  python3 setup_env.py
3. Pokrenite:  python3 launch.py
```

---

## 1. Preduvjeti

### Python 3.10+

Preuzmite s [python.org/downloads](https://www.python.org/downloads/) i instalirajte.

Provjera verzije u Terminalu:
```bash
python3 --version   # macOS / Linux
python --version    # Windows
```

> **macOS napomena:** Sustav dolazi s Python 3.9 koji ima staru verziju Tk (8.5) i GUI neće raditi. Potrebno je instalirati Python 3.10+ s python.org ili putem Homebrew.

### LibreOffice (opcionalno)

Potreban samo ako imate ugovore u starom `.doc` formatu.
Preuzmite s [libreoffice.org](https://www.libreoffice.org/).

### Anthropic API ključ

Sustav koristi Claude AI za ekstrakciju podataka iz ugovora.
API ključ možete unijeti kroz GUI (korak Postavke) ili u `.env` datoteku:

```
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 2. Instalacija

Otvorite Terminal, pozicionirajte se u mapu projekta i pokrenite:

```bash
python3 setup_env.py      # macOS / Linux
python setup_env.py       # Windows
```

Skripta automatski:
- Kreira virtualno okruženje (`.venv`)
- Instalira sve Python ovisnosti
- Provjerava sistemske zahtjeve (LibreOffice, predložak, konfiguraciju)
- Kreira `pipeline.toml` iz predloška ako ne postoji

### Konfiguracija

Datoteka `pipeline.toml` sadrži sve postavke. Ako ne postoji, kreira se automatski iz `pipeline.toml.template`. Provjerite i po potrebi prilagodite:

| Postavka | Opis |
|---|---|
| `company_name` | Naziv tvrtke davatelja usluge |
| `company_oib` | OIB davatelja usluge |
| `company_address` | Adresa davatelja usluge |
| `company_director` | Direktor davatelja usluge |
| `source` | Putanja do mape s ugovorima klijenata |
| `default_effective_date` | Datum stupanja na snagu novih cijena (YYYY-MM-DD) |
| `model` | Claude AI model za ekstrakciju |
| `use_batch_api` | Batch API — 50% jeftinije, traje ~30 min |

---

## 3. Pokretanje

**macOS:** Dvostruki klik na `Launch Pipeline.command` u Finderu.

**Windows:** Dvostruki klik na `launch.py`.

**Terminal:**
```bash
python3 launch.py      # macOS / Linux
python launch.py       # Windows
```

---

## 4. Korištenje — koraci u GUI-u

GUI vas vodi kroz 4 koraka. Svaki korak mora biti završen prije nego što se sljedeći otključa.

### Korak 0 — Postavke

- Postavite putanju do mape s ugovorima klijenata
- Provjerite podatke o tvrtki (naziv, OIB, adresa, direktor)
- Unesite API ključ ako nije već postavljen
- Kliknite **"Test API"** za provjeru veze

### Korak 1 — Priprema

- Kopira ugovore u radnu mapu (`data/`)
- Skenira i klasificira sve datoteke (ugovor, aneks, prilog...)
- Prikazuje inventar svih klijenata i njihovih dokumenata
- Izvorna mapa `contracts/` se ne mijenja

### Korak 2 — Ekstrakcija

- Čita sve ugovore i anekse
- Pomoću Claude AI-a ekstrahira cijene, stavke i podatke o klijentu
- Generira kontrolnu tablicu: `output/control_spreadsheet.xlsx`
- Možete filtrirati klijente po imenu

### Korak 3 — Pregled i unos novih cijena

Otvorite `output/control_spreadsheet.xlsx` u Excelu:

**Sheet 1 (Klijenti):**
- Stupac **Status** — postavite na **"Odobreno"** za klijente kojima želite generirati aneks

**Sheet 2 (Stavke):**
Za svaku stavku unesite novu cijenu na jedan od dva načina:

| Stupac | Naziv | Opis |
|---|---|---|
| G | Nova cijena EUR | Izravna nova cijena u EUR |
| H | % povećanja | Postotni pomak (npr. `5` za +5%, `-3` za -3%) |

- Možete koristiti bilo koji način po retku
- Ako su oba popunjena, postotak ima prednost
- Spremite i zatvorite Excel prije nastavka

U GUI-u možete pregledati ekstrakcije po klijentu.

### Korak 4 — Generiranje aneksa

- Pregledajte što će se generirati (Preview)
- Generirajte aneks dokumente
- Gotovi dokumenti spremaju se u `output/annexes/`

### Tipkovni prečaci

| Prečac | Akcija |
|---|---|
| Cmd/Ctrl + 1-4 | Navigacija na korak |
| Enter | Pokreni trenutni korak |
| Esc | Otkaži operaciju |
| Cmd/Ctrl + F | Pretraži log |

---

## 5. Struktura datoteka

```
contracts/                          Izvorni ugovori (ne mijenja se)
data/                               Radni podaci (kopije, ekstrakcije)
output/
  control_spreadsheet.xlsx          Kontrolna tablica za pregled
  annexes/                          Generirani aneks dokumenti
templates/
  default/aneks_template.docx       Predložak za anekse
pipeline.toml                       Konfiguracija
pipeline.toml.template              Predložak za konfiguraciju
.env                                API ključ
```

---

## 6. Ažuriranje

Za preuzimanje novih verzija:

```bash
cd /putanja/do/projekta
git pull
python3 setup_env.py    # ponovo pokreni ako su se ovisnosti promijenile
```

---

## 7. CLI (napredno)

Pipeline se može koristiti i putem naredbenog retka:

```bash
# Aktivirajte virtualno okruženje
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\activate             # Windows

# Naredbe
pipeline setup --source ./contracts
pipeline extract [--force] [--verbose]
pipeline generate --start-number 30 [--verbose]
pipeline status
pipeline inventory
pipeline validate-template
pipeline reset setup               # Resetiraj jednu fazu
pipeline reset --all                # Resetiraj sve faze
```

| Zastavica | Opis |
|---|---|
| `--version` / `-V` | Prikaži verziju |
| `--verbose` | Puni ispis grešaka |
| `--force` | Ponovi ekstrakciju već obrađenih dokumenata |

---

## 8. Rješavanje problema

| Problem | Rješenje |
|---|---|
| GUI se ruši odmah po pokretanju | macOS Python 3.9 ima staru verziju Tk. Instalirajte Python 3.10+ s [python.org](https://www.python.org/downloads/) i pokrenite `setup_env.py` ponovo. |
| "Virtual environment not found" | Pokrenite `python3 setup_env.py` prije pokretanja GUI-a. |
| "Konfiguracija se ne može učitati" | Provjerite da `pipeline.toml` postoji i ima ispravnu TOML sintaksu. |
| "Inventory not found" | Pokrenite korak Priprema (Setup) prije Ekstrakcije. |
| Korak nije dostupan (siv) | Koraci se izvršavaju redom. Završite prethodni korak. |
| Pipeline zaglavljen u "running" stanju | Pipeline je prekinut. Koristite `pipeline reset [faza]` u terminalu ili ponovo pokrenite GUI. |
| Problemi s .doc datotekama | Instalirajte [LibreOffice](https://www.libreoffice.org/). |
| API greške | Provjerite API ključ u postavkama. Koristite "Test API" gumb. Pozivi se automatski ponavljaju do 3 puta. |
| Greška u kontrolnoj tablici | Ne mijenjajte strukturu tablice (zaglavlja, redoslijed stupaca). Ako je oštećena, ponovo pokrenite ekstrakciju. |

---

## English Summary

This is an automated pipeline for updating prices in Croatian-language client contracts. It extracts current pricing data from contracts using AI, generates a review spreadsheet, and produces new annex documents with updated prices.

**Quick start:**
1. Install Python 3.10+ from [python.org](https://www.python.org/downloads/)
2. Run `python3 setup_env.py` (one-time setup)
3. Run `python3 launch.py` (launches the GUI)
4. Follow the 4-step wizard: Settings → Setup → Extraction → Review → Generation

**To update:** `git pull` then re-run `setup_env.py` if dependencies changed.
