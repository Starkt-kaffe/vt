# Västtrafik avgångsvisare

En liten Flask-app som visar avgångar från en hållplats via Västtrafiks
"Planera Resa"-API. Det här är en ombyggd version av det gamla CGI-skriptet
`jsonhtml.py` — samma funktion, men skriven så att den går att lägga på
GitHub och köra på PythonAnywhere (eller vilken WSGI-värd som helst).

## Vad som ändrats jämfört med det gamla CGI-skriptet

- Ingen `print()`-baserad CGI-utskrift — det är nu en riktig Flask-app
  (`app.py`) som svarar via HTTP.
- `client_id`/`client_secret` läses från miljövariablerna
  `VT_CLIENT_ID`/`VT_CLIENT_SECRET` i stället för att stå hårdkodade i
  koden.
- Cache-filerna `active-token` och `stop-areas` sparas relativt till
  appens mapp (går att styra med `VT_CACHE_DIR`) i stället för det
  hårdkodade `/var/www/cgi-bin/...`.
- Enkelt XSS-skydd på sökfältet via `markupsafe.escape`.

## ⚠️ Innan ni lägger detta på GitHub

De gamla nycklarna har legat i klartext i era tidigare skript
(`client_id`/`client_secret`). Om ni någonsin har committat de gamla
filerna, eller om nycklarna har synts någon annanstans (t.ex. i den här
chatten), betrakta dem som röjda:

1. Logga in på https://developer.vasttrafik.se
2. Skapa nya API-nycklar för er applikation.
3. Använd bara de nya nycklarna i `.env`/miljövariabler härifrån.

`.gitignore` ser till att `.env`, `active-token` och `stop-areas` aldrig
committas — men de **gamla** nycklarna i den gamla filhistoriken försvinner
inte av sig själva.

## Köra lokalt

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# redigera .env och fyll i VT_CLIENT_ID / VT_CLIENT_SECRET

# ladda miljövariablerna och starta
export $(grep -v '^#' .env | xargs)   # macOS/Linux
flask --app app run --debug
```

Öppna sedan http://127.0.0.1:5000/

## Lägga upp på GitHub

```bash
git init
git add .
git commit -m "Flask-version av avgångsvisaren"
git branch -M main
git remote add origin https://github.com/<ditt-användarnamn>/<repo>.git
git push -u origin main
```

Kontrollera i webbgränssnittet att `.env`, `active-token` och `stop-areas`
**inte** ligger med i repot (de ska vara ignorerade av `.gitignore`).

## Driftsätta på PythonAnywhere

1. **Klona/ladda upp koden**
   Öppna en Bash-konsol på PythonAnywhere och kör:
   ```bash
   git clone https://github.com/<ditt-användarnamn>/<repo>.git
   cd <repo>
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Skapa en Web-app**
   Gå till fliken **Web** → **Add a new web app** → välj **Manual configuration**
   (inte "Flask"-guiden, då den skapar en egen `flask_app.py`) → välj samma
   Python-version som ni testat lokalt.

3. **Peka ut virtualenv**
   Under **Virtualenv**, fyll i sökvägen till venv:n ni skapade, t.ex.
   `/home/dittanvandarnamn/<repo>/venv`

4. **Sätt miljövariabler**
   Under **Web**-fliken finns en sektion för miljövariabler (eller lägg dem
   i WSGI-filen enligt nästa steg). Sätt:
   - `VT_CLIENT_ID`
   - `VT_CLIENT_SECRET`
   - ev. `VT_CACHE_DIR` om ni vill peka cache-filerna till en annan mapp
     (t.ex. om projektmappen är skrivskyddad).

5. **Redigera WSGI-filen**
   Klicka på länken till WSGI-konfigurationsfilen under **Code**-sektionen
   och ersätt innehållet med (justera sökvägar och ev. miljövariabler):

   ```python
   import sys
   import os

   project_home = '/home/dittanvandarnamn/<repo>'
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   # Om ni inte satte miljövariablerna i webbgränssnittet, sätt dem här:
   # os.environ['VT_CLIENT_ID'] = 'din-client-id'
   # os.environ['VT_CLIENT_SECRET'] = 'din-client-secret'

   from app import app as application
   ```

6. **Reload**
   Klicka **Reload** på Web-fliken och besök er `<användarnamn>.pythonanywhere.com`.

### Om ni vill slippa det första, långsamma API-anropet

Det finns en tidigare hämtad hållplatslista i `stop-areas.seed.json`
(från era gamla filer). Om ni kopierar/döper om den till `stop-areas`
(utan filändelse, precis som appen förväntar sig) i samma mapp som
`app.py`, slipper första besökaren vänta på att hela hållplatslistan
hämtas från API:et:

```bash
cp stop-areas.seed.json stop-areas
```

Filen förnyas automatiskt av appen efter `VT_CACHE_HOURS` timmar (standard
20), så den behöver inte hållas uppdaterad för hand.

## Filstruktur

```
app.py             Flask-appen (all logik)
requirements.txt    Pip-beroenden
.env.example         Mall för miljövariabler (kopieras till .env, committas ej)
.gitignore
README.md
```
