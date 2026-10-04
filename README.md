# labirintul-audio

Genereaza automat audio (voce Piper `ro_RO-mihai-medium`, licenta CC0) pentru
articolele de pe labirintulmagazin.org si il publica pe GitHub Pages.

- `scripts/generate.py` - citeste articolele prin API-ul WordPress si creeaza
  `public/audio/<id>.mp3` + `<id>.json` (propozitii cu momente de timp).
- `.github/workflows/audio.yml` - ruleaza la 30 de minute si publica site-ul.
- Se pastreaza ultimele 200 de articole (`KEEP_LATEST`).

Aplicatia mobila cere `https://dragoslup1976.github.io/labirintul-audio/audio/<id>.json`.
Daca nu exista (404), foloseste vocea telefonului.
