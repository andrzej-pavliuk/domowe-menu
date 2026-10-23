# Domowe Menu 0.4.1

Polska aplikacja dla jednej rodziny, z interfejsem opartym na zaakceptowanym prototypie: jasny i ciemny motyw, przepisy w kafelkach, kalendarz, dolne menu i formularze wygodne na telefonie. Oddzielna, lekka aplikacja w Pythonie i SQLite. Nie wymaga Mealie, Postgresa, AI ani płatnych usług. Nie jest pełną kopią wszystkich funkcji Mealie.

## Wersja na komputer

Od szerokości okna 1024 px aplikacja przełącza się na pulpit z bocznym menu. Widok startowy ma osobny panel bilansu, przepisy wyświetlają się w 3–4 kolumnach, szczegóły przepisu i dziennik mają układ dwukolumnowy, a zakupy są rozdzielone na działy obok siebie. Planer pokazuje tydzień w siedmiu kolumnach lub miesiąc z nazwami dań. Na mniejszych laptopach sam kalendarz można przewijać poziomo; reszta strony mieści się w oknie.

Kliknięcie „＋ Dodaj” w kalendarzu ustawia datę oraz typ posiłku w formularzu. Widok telefonu pozostaje z dolnym menu. Układ dobiera się automatycznie, bez oddzielnej instalacji i bez dodatkowego kontenera. Zmiana 0.4.0 → 0.4.1 nie zmienia struktury bazy i zachowuje zgodność kopii 0.4.0.

## Budowanie w WSL — dokładne ścieżki

Pobierz `domowe-menu-0.4.1-ugreen.zip` do katalogu Pobrane w Windows. W WSL:

```bash
cd /mnt/c/Users/andrzej.pavliuk/Downloads/
unzip domowe-menu-0.4.1-ugreen.zip -d domowe-menu-0.4.1
sudo sh /mnt/c/Users/andrzej.pavliuk/Downloads/domowe-menu-0.4.1/domowe-menu/build-image.sh
```

Skrypt sam przechodzi do katalogu z Dockerfile. Jeśli budowanie nie powiedzie się, kończy pracę i nie wykonuje eksportu nieistniejącego obrazu.

Wynik: `Downloads/domowe-menu-0.4.1/domowe-menu-0.4.1.tar`.

Odpowiedniki poleceń wykonywanych przez skrypt:

```bash
sudo docker buildx build --platform linux/amd64 --load -t domowe-menu:0.4.1 .
sudo docker save -o ../domowe-menu-0.4.1.tar domowe-menu:0.4.1
```

Te dwa polecenia uruchamiaj wyłącznie w katalogu `domowe-menu`, który zawiera Dockerfile. Do pobrania obrazu bazowego Docker potrzebuje internetu podczas budowania.

## Portainer i Nginx Proxy Manager

1. Przed aktualizacją pobierz kopię danych w dotychczasowej aplikacji i sprawdź nazwę zamontowanego wolumenu `/data`.
2. W Portainerze użyj **Images → Import** i wgraj TAR. Powinien pojawić się obraz `domowe-menu:0.4.1`.
3. Aktualizuj istniejący stack Domowego Menu. Wklej `docker-compose.yml` z paczki. Zachowaj nazwę stacka i istniejący wolumen `domowe_menu_data` — faktyczna nazwa wolumenu zwykle ma prefiks stacka.
4. W `APP_PASSWORD` wpisz własne hasło mające co najmniej 16 znaków. Znak `$` wpisany bezpośrednio w Compose zapisz jako `$$`. Przykładowy placeholder celowo nie pozwala uruchomić aplikacji.
5. Wykonaj **Update the stack / Deploy**, bez pobierania obrazu z rejestru. Nie usuwaj wolumenu. Nie uruchamiaj równolegle drugiego kontenera z aliasem `domowe-menu` w tej samej sieci.
6. W NPM ustaw swoją domenę, schemat **http**, **Forward Hostname: domowe-menu**, **Forward Port: 8080**, certyfikat dla tej domeny i **Force SSL**. Sieć: `nginx-proxy-manager_default`, tak jak w działającym budżecie.
7. W Portainerze sprawdź logi: `Domowe Menu 0.4.1 · http://0.0.0.0:8080`. Healthcheck powinien przejść na `healthy`.

Aplikacja nie wymaga publikowania portu na NAS-ie. Logowanie wymaga HTTPS, ponieważ ciasteczko sesji ma flagę Secure. Nie podłączaj wolumenu Mealie ani jego bazy Postgres do `/data`.

Po aktualizacji zamknij i otwórz aplikację ponownie. Jeżeli telefon nadal pokazuje stary wygląd, odśwież stronę w Safari. Nowy service worker usuwa stare wersje zasobów Domowego Menu. Przy dodawaniu do ekranu początkowego użyj Safari → Udostępnij → Do ekranu początkowego. Ekrany danych wymagają połączenia z serwerem — nie ma jeszcze pracy i synchronizacji offline.

## Pierwsze użycie

- Zaloguj się hasłem ze stacka.
- Menu **☰ → Domownicy**: dodaj rodzinę, ustaw porcje i opcjonalne cele. Dla dziecka lub innej osoby można pozostawić limit kalorii pusty.
- **Przepisy**: dodaj własne lub użyj **Ustawienia i kopie → Import Mealie**. Każdy przepis do generatora musi mieć typ posiłku, kalorie na porcję i czas aktywnej pracy, jeśli obowiązuje limit czasu.
- **☰ → Reguły generatora**: ustaw domyślne powtórzenia, czasy, sezonowość, ograniczenia i przekąski. Możesz dodać gotowy przykład weekendu i własne reguły jesienne.
- **Planer → Generuj**: wybierz dni, obejrzyj propozycję i zatwierdź ją. Istniejące wspólne posiłki pozostają; generator uzupełnia wolne miejsca.
- **Zakupy → Lista**: wybierz zakres i zatwierdź podgląd zmian. Statusy „Kupione” i „Mam w domu” pozostają zachowane po aktualizacji.

## Co działa w tej wersji

### Rodzinny planer i generator

- Śniadanie, obiad i kolacja mają wspólny przepis dla rodziny. Domyślnie każda osoba ma taką samą porcję; współczynnik porcji można zmienić w profilu lub wpisać ręcznie przy planowaniu.
- Wyższy cel kalorii jest uzupełniany przekąskami przypisanymi do konkretnej osoby. Generator korzysta z praktycznych porcji i kończy szukanie po osiągnięciu tolerancji.
- Domyślna tolerancja ±5%; można ustawić procent lub własną liczbę kcal, np. 50, 100 lub 150. Plan 2512 kcal przy celu 2500 i ±5% jest prawidłowy.
- Plan tygodniowy i miesięczny, ręczne dodawanie dań, kopiowanie poprzedniego dnia, wybór alternatywnego dania z podglądem zmiany kalorii i zakresu całego dnia.
- Osobna data gotowania, osoba gotująca, notatki, oznaczanie przygotowania. Jedna partia może obejmować wiele dni. Zamiana jednego dnia oddziela go od pozostałych dni partii.
- Reguły z priorytetem, dniami tygodnia, porami roku i opcjonalnym zakresem dat. Osobne limity aktywnej pracy, czasu łącznego i liczby dni powtórzeń dla każdego głównego posiłku. Odstęp między gotowaniami tego samego dania, maksymalna liczba gotowań w tygodniu, ograniczenia składników i tagów, preferowane tagi.
- Domyślnie obowiązuje ścisła sezonowość. Własne miesiące produktu/składnika mają pierwszeństwo. Lista typowych sezonów świeżych owoców i warzyw dotyczy Polski; konserwy, słoiki, suszone i mrożone produkty są domyślnie dostępne cały rok. Można przełączyć na preferencję lub wyłączyć filtr.
- Generator nie pomija po cichu ograniczeń, kiedy brakuje przepisów. Pokazuje brakujący posiłek lub najlepszy znaleziony wariant i odchylenie. Szukanie ma ograniczoną liczbę prób, więc nie gwarantuje matematycznego optimum.
- Podgląd nie zapisuje planu. Po zmianie danych trzeba wygenerować go ponownie; ponowne zatwierdzenie tej samej propozycji jest blokowane.

### Przepisy, produkty i gotowanie

- Dodawanie, edycja, zdjęcia JPG/PNG/WebP do 8 MB, wyszukiwanie, ulubione, archiwum, kategorie, kolekcje z tagów, notatki, instrukcje i przeliczanie składników na porcje.
- Tryb gotowania z odhaczaniem kroków i minutnikiem. Kroki przypisane do partii są przechowywane na serwerze; gotowanie samego przepisu pamięta kroki w danej przeglądarce. Minutnik działa przy otwartej aplikacji, nie jest alarmem systemowym iPhone'a.
- Przepis podaje składniki całej partii, a kalorie i makro jednej porcji. Własny zapis miar: `cukier | 1 | łyżeczka | 5` pokazuje także 5 g i uwzględnia je w zakupach.
- Rozszerzony zapis składnika: `truskawki | 200 | g | | frozen |`. Postać: `fresh`, `frozen`, `canned`, `jar`, `dried`. Ostatnia kolumna to opcjonalne miesiące, np. `5,6,7`.
- Własna baza produktów na 100 g, miary domowe, gramatura, postać, miesiące dostępności i grupa zamienników. Przycisk w edycji przepisu przelicza makro na porcję na podstawie produktów o dokładnie pasującej nazwie i postaci; brak danych zgłasza zamiast zgadywać.
- Import URL z danymi Recipe w Schema.org/JSON-LD; nie każda strona je udostępnia. Import ZIP Mealie z `database.json`: przepisy, ilości i miary, instrukcje, wartości odżywcze, czasy i obsługiwane zdjęcia oryginalne. Ponowny import tego samego UUID Mealie pomija zaimportowany przepis.
- Po imporcie trzeba sprawdzić miary, kalorie, typy posiłków i czasy. Składniki bez ustrukturyzowanych danych Mealie pozostają tekstem. Automatyczna ocena alergenów opiera się na wpisanych nazwach składników; aplikacja nie rozpoznaje ukrytych alergenów.

### Cele i dziennik

- Profile z opcjonalnymi parametrami, celem masy, datami, aktywnością, tempem, zatwierdzonym limitem i opcjonalnymi celami makroskładników.
- BMI, BMR Mifflina–St Jeora i TDEE jako szacunek. Zatwierdzenie z możliwością korekty; brak automatycznego nadpisywania celu. Kalkulator jest wyłączony dla osób poniżej 18 i powyżej 80 lat, ciąży, karmienia i wskazanych indywidualnych zaleceń.
- Historia i wykres masy, zmiana w czasie, postęp do celu. Pomiar aktualizuje bieżącą masę, ale nie zmienia limitu kalorii.
- Dziennik: zjedzone, zaplanowane, pominięte, jednorazowe przenoszenie posiłku z planu, wpisy poza planem, produkty z gramaturą i automatycznym przeliczeniem z etykiety.
- Planowany bilans i faktycznie zjedzone wartości pokazują się osobno. Opis posiłku nie wylicza sam kalorii: potrzebna jest etykieta, lokalny produkt lub wartości wpisane ręcznie.

### Zakupy i kopie

- Lista zakupów na wybrany zakres dni sumuje wszystkie porcje i osobiste przekąski. Jeśli zakres obejmuje choć jeden dzień danej partii, zakupy uwzględniają składniki całej tej partii, również na jej pozostałe dni.
- Grupowanie działami, własne pozycje, statusy „Do kupienia”, „Kupione”, „Mam w domu”. Podgląd dodanych, usuniętych i zmienionych pozycji po zmianie planu. Większa ilość produktu wcześniej oznaczonego jako posiadany wymaga sprawdzenia.
- Pełny eksport i przywracanie kopii 0.4 w JSON, ze zdjęciami. Przywracanie bazy jest transakcyjne; przed nim aplikacja zachowuje `/data/przed-przywroceniem.sqlite` jako kopię awaryjną.
- Istniejąca baza 0.3 w zachowanym wolumenie jest aktualizowana przy starcie; dotychczasowe plany zostają przeniesione. JSON starej wersji nie jest obecnie obsługiwany przez nowy formularz przywracania.

## Dalsze rozwijanie

Nie ma jeszcze automatycznych zamienników składników z przeliczeniem gramatury i pakowania zakupów ani pełnego systemu zapobiegania marnowaniu. Reguły sezonowe i wspólne partie gotowania są jego podstawą. Grupa zamienników produktu jest już zapisywana, ale nie uruchamia samoczynnej zamiany. Opcjonalne cele makro są zapisywane; generator dopasowuje obecnie kalorie, nie optymalizuje osobno każdego makroskładnika. Udostępnianie publicznych przepisów, konta z rolami i pozostałe funkcje Mealie nie należą do tej wersji.

## Sprawdzenie źródeł

```bash
DATA_DIR=/tmp/domowe-menu-test python3 -m unittest discover -s tests -v
python3 -m py_compile app.py family.py imports.py server.py
node --check static/app.js
node tests/test_frontend.cjs
```

Testy obejmują obliczenia, ograniczenia, wspólne posiłki i przekąski, zatrzymanie po osiągnięciu tolerancji, reguły i sezony, sumowanie partii, zachowanie statusów zakupów, kopie i wycofanie błędnego przywracania, migrację oraz logowanie posiłku bez duplikatów. Testy JavaScript sprawdzają renderowanie ekranów i formularzy; nie zastępują testu na fizycznym iPhonie. Obraz Docker trzeba zbudować i zweryfikować w swoim WSL/NAS-ie.
